"""Idempotent boot-time schema patches.

SQLAlchemy's `create_all` only creates tables that don't already exist; it
never ALTERs. For a small project without a full migration framework this
runner closes the gap: it inspects the live DB and applies any missing
columns or enum-value changes that newer code needs.

Each patch is wrapped in try/except and logs at info-or-warning so a single
failure doesn't take down boot. Patches must be safe to re-run.
"""
import logging
from sqlalchemy import inspect, text

logger = logging.getLogger(__name__)


def _has_column(inspector, table: str, column: str) -> bool:
    try:
        cols = [c['name'] for c in inspector.get_columns(table)]
        return column in cols
    except Exception:
        return False


def _add_column(engine, table: str, column: str, ddl: str):
    """Add a column to a table if it doesn't exist (MySQL-flavored)."""
    insp = inspect(engine)
    if _has_column(insp, table, column):
        return False
    with engine.begin() as conn:
        conn.execute(text(f'ALTER TABLE `{table}` ADD COLUMN `{column}` {ddl}'))
    logger.info("migrations: added %s.%s", table, column)
    return True


def _has_index(engine, table: str, index_name: str) -> bool:
    try:
        with engine.connect() as conn:
            r = conn.execute(text(f'SHOW INDEX FROM `{table}` WHERE Key_name = :n'), {'n': index_name})
            return r.first() is not None
    except Exception:
        return False


def _add_index(engine, table: str, index_name: str, columns: list):
    """Create a composite index if missing (MySQL). Safe to re-run; two
    processes booting at once just race to create it and the loser logs a
    warning."""
    if _has_index(engine, table, index_name):
        return False
    cols = ", ".join(f"`{c}`" for c in columns)
    try:
        with engine.begin() as conn:
            conn.execute(text(f'CREATE INDEX `{index_name}` ON `{table}` ({cols})'))
        logger.info("migrations: added index %s on %s (%s)", index_name, table, cols)
        return True
    except Exception as e:
        logger.warning("migrations: add index %s on %s failed: %s", index_name, table, e)
        return False


def _drop_index(engine, table: str, index_name: str):
    """Drop an index if present (MySQL). Safe to re-run / race; logs on failure
    (e.g. if MySQL still needs it for an FK — caller must ensure another
    covering index exists first)."""
    if not _has_index(engine, table, index_name):
        return False
    try:
        with engine.begin() as conn:
            conn.execute(text(f'DROP INDEX `{index_name}` ON `{table}`'))
        logger.info("migrations: dropped redundant index %s on %s", index_name, table)
        return True
    except Exception as e:
        logger.warning("migrations: drop index %s on %s failed: %s", index_name, table, e)
        return False


def _modify_enum(engine, table: str, column: str, values: list):
    """Widen an ENUM column to include extra values (MySQL only).

    Safe to re-run; MODIFY with the same set is a no-op besides table-metadata
    work. We always set the same NOT NULL/default so callers don't need to
    repeat them — adjust here if the column shape ever drifts.
    """
    vals = ", ".join(f"'{v}'" for v in values)
    ddl = f"ENUM({vals}) NOT NULL DEFAULT 'created'"
    try:
        with engine.begin() as conn:
            conn.execute(text(f'ALTER TABLE `{table}` MODIFY COLUMN `{column}` {ddl}'))
        logger.info("migrations: widened %s.%s enum (%s)", table, column, len(values))
    except Exception as e:
        logger.warning("migrations: enum widen failed for %s.%s: %s", table, column, e)


def _modify_column(engine, table: str, column: str, ddl: str):
    """ALTER a column's type/shape (MySQL). Idempotent — MODIFY to the same
    definition is a metadata no-op, so safe to re-run every boot."""
    try:
        with engine.begin() as conn:
            conn.execute(text(f'ALTER TABLE `{table}` MODIFY COLUMN `{column}` {ddl}'))
        logger.info("migrations: modified %s.%s -> %s", table, column, ddl)
    except Exception as e:
        logger.warning("migrations: column modify failed for %s.%s: %s", table, column, e)


def run_migrations(engine):
    """Apply every known patch. Skips MySQL-specific patches on other dialects."""
    dialect = engine.dialect.name
    if dialect != 'mysql':
        logger.info("migrations: skipping MySQL-specific patches on dialect=%s", dialect)
        return

    # ── scrub_jobs: Spaces-backed upload columns ──
    _add_column(engine, 'scrub_jobs', 's3_bucket', 'VARCHAR(120) NULL')
    _add_column(engine, 'scrub_jobs', 's3_key', 'VARCHAR(500) NULL')
    _add_column(engine, 'scrub_jobs', 'multipart_upload_id', 'VARCHAR(255) NULL')
    _add_column(engine, 'scrub_jobs', 'original_filename', 'VARCHAR(255) NULL')
    _add_column(engine, 'scrub_jobs', 'file_size_bytes', 'BIGINT NULL')
    _add_column(engine, 'scrub_jobs', 'content_type', 'VARCHAR(120) NULL')
    _add_column(engine, 'scrub_jobs', 'detected_headers_json', 'JSON NULL')
    _add_column(engine, 'scrub_jobs', 'delimiter', 'VARCHAR(8) NULL')
    _add_column(engine, 'scrub_jobs', 'result_s3_key', 'VARCHAR(500) NULL')

    # Inline custom-column storage on records (replaces the EAV table for new
    # jobs — see scrub_job_record.custom_json). INSTANT add on MySQL 8.
    _add_column(engine, 'scrub_job_records', 'custom_json', 'JSON NULL')

    # EmailOversight FTP round-trip columns (see FTP.md).
    _add_column(engine, 'scrub_jobs', 'email_column_index', 'INT NULL')
    _add_column(engine, 'scrub_jobs', 'ftp_submitted_filename', 'VARCHAR(255) NULL')
    _add_column(engine, 'scrub_jobs', 'ftp_processed_filename', 'VARCHAR(255) NULL')
    _add_column(engine, 'scrub_jobs', 'ftp_submitted_at', 'DATETIME NULL')
    _add_column(engine, 'scrub_jobs', 'ftp_result_ingested_at', 'DATETIME NULL')

    # EO per-record rates are sub-cent ($0.000375 + tiered margin); 4dp rounds
    # them all to 0.0005. Widen so the stored/displayed rate is accurate.
    _modify_column(engine, 'scrub_jobs', 'rate_per_record', 'DECIMAL(12,6) DEFAULT 0')

    # Actual amount charged to the card (price_cents grossed up for the Stripe
    # processing fee). Stored at pay time so the confirmation email reports what
    # the customer really paid, not the pre-fee base price.
    _add_column(engine, 'scrub_jobs', 'amount_paid_cents', 'BIGINT NULL DEFAULT 0')

    # Stripe Customer id per company (saved cards + off-session charges).
    _add_column(engine, 'mailer_companies', 'stripe_customer_id', 'VARCHAR(100) NULL')

    # Expand the status enum with the two new states (awaiting_mapping, importing,
    # plus the new uploading state for in-flight uploads). Keep all the
    # historical values so existing rows stay valid.
    _modify_enum(engine, 'scrub_jobs', 'status', [
        'created', 'uploading', 'uploaded',
        'awaiting_mapping', 'importing',
        'validating', 'scrubbing', 'priced',
        'awaiting_payment', 'paid',
        'submitting_ftp', 'awaiting_ftp_result',
        'generating', 'complete', 'failed',
    ])

    # Composite index so the import worker's EAV id-lookup
    # (WHERE scrub_job_id=? AND row_index BETWEEN ? AND ?) is a tight range scan
    # instead of re-scanning every row of the job per batch (was O(n^2) on a
    # large EAV-heavy upload — 49 min import on an 878k-row / 4.4M-EAV file).
    _add_index(engine, 'scrub_job_records', 'idx_sjr_job_rowidx', ['scrub_job_id', 'row_index'])

    # Drop redundant duplicate indexes that inflated every insert (each column
    # was indexed 2-3x). Done AFTER idx_sjr_job_rowidx exists, so scrub_job_id's
    # FK keeps a covering index. Safe: idx_sjr_company keeps company_id,
    # idx_sjr_job_rowidx keeps scrub_job_id, idx_sjrf_record keeps record_id,
    # idx_sjrf_job_field keeps the EAV scrub_job_id.
    for _tbl, _idx in (
        ('scrub_job_records', 'ix_scrub_job_records_scrub_job_id'),
        ('scrub_job_records', 'idx_sjr_job'),
        ('scrub_job_records', 'ix_scrub_job_records_company_id'),
        ('scrub_job_record_fields', 'ix_scrub_job_record_fields_record_id'),
        ('scrub_job_record_fields', 'ix_scrub_job_record_fields_scrub_job_id'),
    ):
        _drop_index(engine, _tbl, _idx)

    # ── purchase_jobs: Spaces-backed result ──
    _add_column(engine, 'purchase_jobs', 'result_s3_key', 'VARCHAR(500) NULL')

    # Actual amount charged (price grossed up for the Stripe fee), mirroring
    # scrub_jobs — so job history / dashboard show what the customer really paid.
    _add_column(engine, 'purchase_jobs', 'amount_paid_cents', 'BIGINT NULL DEFAULT 0')
