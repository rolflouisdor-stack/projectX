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

    # Expand the status enum with the two new states (awaiting_mapping, importing,
    # plus the new uploading state for in-flight uploads). Keep all the
    # historical values so existing rows stay valid.
    _modify_enum(engine, 'scrub_jobs', 'status', [
        'created', 'uploading', 'uploaded',
        'awaiting_mapping', 'importing',
        'validating', 'scrubbing', 'priced',
        'awaiting_payment', 'paid', 'generating', 'complete', 'failed',
    ])

    # Composite index so the import worker's EAV id-lookup
    # (WHERE scrub_job_id=? AND row_index BETWEEN ? AND ?) is a tight range scan
    # instead of re-scanning every row of the job per batch (was O(n^2) on a
    # large EAV-heavy upload — 49 min import on an 878k-row / 4.4M-EAV file).
    _add_index(engine, 'scrub_job_records', 'idx_sjr_job_rowidx', ['scrub_job_id', 'row_index'])

    # ── purchase_jobs: Spaces-backed result ──
    _add_column(engine, 'purchase_jobs', 'result_s3_key', 'VARCHAR(500) NULL')
