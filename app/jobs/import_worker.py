"""File-import worker.

Runs in the RQ `worker` process, not in gunicorn. Takes a single arg
(scrub_job_id), then:

  1. Streams the uploaded file from Spaces (CSV/TSV/pipe streamed line by
     line; XLSX read row-by-row via openpyxl read_only).
  2. Applies the user's field mapping (scrub_job_field_mappings) to each row,
     writing standard fields into scrub_job_records and custom fields into
     scrub_job_record_fields (EAV).
  3. Flips status `importing → validating → scrubbing`, then calls the
     scrub engine on the freshly-loaded records.

Status writes are committed in batches so the web process polling
/api/scrub-jobs/{id} sees real progress.
"""
import csv
import logging
import os
import sys
import tempfile
from datetime import datetime

logger = logging.getLogger(__name__)


def _raise_csv_field_limit():
    """Lift the csv module's 128 KB per-field cap.

    Real uploads contain very large single cells (long URLs / User-Agent
    strings) and sometimes a malformed/unclosed quote that makes csv treat many
    rows as one field — both trip the default limit with
    "field larger than field limit (131072)" and abort the import. Raise it as
    high as the platform's C long allows (sys.maxsize overflows on some, so step
    down until it sticks)."""
    limit = sys.maxsize
    while limit > 1:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:
            limit //= 10


_raise_csv_field_limit()

# How many records to commit per transaction. Keeps memory bounded and gives
# the polling UI visible progress without a transaction-per-row tax.
BATCH_SIZE = 1000


def _open_app_context():
    """Build a Flask app + push a context. The worker process re-enters this
    once at startup and once again per job (cheap; just resolves config)."""
    from app import create_app
    from config import DevelopmentConfig, ProductionConfig
    import os
    cls = ProductionConfig if os.getenv('FLASK_ENV') == 'production' else DevelopmentConfig
    app = create_app(cls)
    return app


def _normalize_email(s):
    if not s:
        return None
    return str(s).strip().lower() or None


def _str(v, limit=None):
    if v is None:
        return None
    s = str(v).strip()
    if limit and len(s) > limit:
        s = s[:limit]
    return s or None


def _row_iter_csv(path, delimiter):
    """Yield data rows from a CSV/TSV/pipe file on local disk (header skipped)."""
    f = open(path, 'r', encoding='utf-8', errors='replace', newline='')
    reader = csv.reader(f, delimiter=delimiter or ',')
    next(reader, None)   # skip header row
    try:
        for row in reader:
            yield row
    finally:
        f.close()


def _row_iter_xlsx(path):
    """Yield data rows from an xlsx file on local disk. openpyxl reads the zip
    directly off the path in read_only mode — no full-file buffer in memory."""
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    header_seen = False
    try:
        for row in ws.iter_rows(values_only=True):
            if not header_seen:
                header_seen = True
                continue
            yield list(row)
    finally:
        wb.close()


# Per-standard-field length caps mirror the ScrubJobRecord columns.
_STD_LIMITS = {
    'first_name': 120, 'last_name': 120, 'phone': 40,
    'email': 320, 'address': 255, 'city': 120, 'state': 40, 'zip': 20,
}


def _build_record(row, mappings, scrub_job_id, company_id, row_index):
    """Apply mappings to a single parsed row, returning (record_kwargs, extras_kwargs_list)."""
    from app.models.scrub_job_record import ScrubJobRecord  # noqa: F401  (caller imports)
    std = {}
    extras = []
    for m in mappings:
        if m.skip:
            continue
        idx = m.column_index
        val = row[idx] if idx < len(row) else None
        if m.is_standard:
            std[m.target_field] = _str(val, _STD_LIMITS.get(m.target_field))
        else:
            extras.append({
                'scrub_job_id': scrub_job_id,
                'field_name': m.target_field,
                'value_text': _str(val, 65535),
            })
    if 'email' in std:
        std['email_normalized'] = _normalize_email(std.get('email'))
    return std, extras


def _download_upload(job):
    """Pull the uploaded file from Spaces down to a local temp file and return
    its path. We download in full *before* parsing rather than streaming a
    GetObject body through the parse loop: holding one GetObject connection open
    for the whole parse+insert keeps the socket idle between batch commits long
    enough that Spaces drops it mid-stream (IncompleteRead) on large files. A
    fast continuous managed download, then parse-from-disk, decouples network
    I/O from the slow DB work. Caller is responsible for deleting the path."""
    from app.services import storage
    suffix = os.path.splitext(job.original_filename or job.s3_key or '')[1]
    fd, path = tempfile.mkstemp(prefix=f'scrub_{job.id}_', suffix=suffix)
    os.close(fd)
    storage.download_to_file(job.s3_key, path)
    return path


def _row_iter_for(path, job):
    """Pick the right row iterator for a downloaded upload on local disk."""
    name = (job.original_filename or job.s3_key or '').lower()
    if name.endswith('.xlsx'):
        return _row_iter_xlsx(path)
    return _row_iter_csv(path, job.delimiter or ',')


def run_import(scrub_job_id: int):
    """RQ entry point. Imports the file, then runs the scrub engine."""
    app = _open_app_context()
    with app.app_context():
        from app.extensions import get_db
        from app.models.scrub_job import ScrubJob
        from app.models.scrub_job_field_mapping import ScrubJobFieldMapping
        from app.models.scrub_job_record import ScrubJobRecord
        from app.models.scrub_job_record_field import ScrubJobRecordField
        from app.services.scrub_engine import run_mock_scrub_on_records

        db = get_db()
        job = db.query(ScrubJob).filter_by(id=scrub_job_id).first()
        if not job:
            logger.error("run_import: scrub_job %s not found", scrub_job_id)
            return
        try:
            job.status = 'importing'
            db.commit()

            mappings = (db.query(ScrubJobFieldMapping)
                        .filter_by(scrub_job_id=scrub_job_id)
                        .order_by(ScrubJobFieldMapping.column_index)
                        .all())
            if not mappings:
                raise RuntimeError("no field mappings — cannot import without them")

            tmp_path = _download_upload(job)
            try:
                row_iter = _row_iter_for(tmp_path, job)
                batch_records = []
                batch_extras_per_record = []   # one list per record in batch
                row_index = 0
                total_rows = 0

                for row in row_iter:
                    row_index += 1
                    std, extras = _build_record(
                        row, mappings, scrub_job_id, job.company_id, row_index)
                    rec = ScrubJobRecord(
                        scrub_job_id=scrub_job_id,
                        company_id=job.company_id,
                        row_index=row_index,
                        **std,
                    )
                    batch_records.append(rec)
                    batch_extras_per_record.append(extras)

                    if len(batch_records) >= BATCH_SIZE:
                        _flush_batch(db, batch_records, batch_extras_per_record)
                        total_rows += len(batch_records)
                        job.uploaded_count = total_rows
                        db.commit()
                        batch_records = []
                        batch_extras_per_record = []

                if batch_records:
                    _flush_batch(db, batch_records, batch_extras_per_record)
                    total_rows += len(batch_records)

                job.uploaded_count = total_rows
                job.status = 'scrubbing'
                db.commit()
            finally:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

            # Hand off to the (stub) scrub engine to bucket records + price
            run_mock_scrub_on_records(job)
            db.commit()

            # Mirror the activity log the old synchronous endpoint emitted.
            from app.services.activity_logger import log_activity
            from app.models.activity_log import ACTION_RUN_SCRUB
            log_activity(
                job.company_id, ACTION_RUN_SCRUB,
                user_id=job.user_id, scrub_job_id=job.id,
                meta={'uploaded': job.uploaded_count, 'unique': job.unique_count,
                      'overlap': job.overlap_count, 'price_cents': job.price_cents},
            )

            logger.info("run_import: scrub_job %s imported %s rows, priced", scrub_job_id, job.uploaded_count)
        except Exception as e:
            logger.exception("run_import failed for scrub_job %s", scrub_job_id)
            job.status = 'failed'
            job.failure_reason = str(e)[:500]
            db.commit()
            raise


def _flush_batch(db, records, extras_per_record):
    """Add records, flush to get IDs, attach EAV extras, then commit."""
    from app.models.scrub_job_record_field import ScrubJobRecordField
    for r in records:
        db.add(r)
    db.flush()   # populates r.id for every record
    for rec, extras in zip(records, extras_per_record):
        for ex in extras:
            ex['record_id'] = rec.id
            db.add(ScrubJobRecordField(**ex))
    # Caller commits at batch boundary so polling sees progress.
