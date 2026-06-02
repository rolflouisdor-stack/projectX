"""Scrub engine (STUB, but now operating on real records).

Real implementation responsibilities (documented in Partner_portals_Spec §6):
  1. Parse the uploaded CSV/XLSX, normalize emails, dedupe within the upload.
     → DONE — the import worker streams the file from Spaces, applies the
       user's column mapping, and inserts one row per parsed line into
       scrub_job_records.
  2. If cleaning_opted_in: send batches to NeverBounce/ZeroBounce/Kickbox
     and drop invalid + disposable + role addresses.
     → STILL STUBBED — we randomly bucket records as valid/invalid here.
  3. Cross-reference the remaining list against every Gravitas data source
     in the selected verticals — bucket each email as `unique` or `overlap`.
     → STILL STUBBED — we randomly bucket records as unique/overlap here.
  4. Generate an .xlsx with the unique rows for the mailer to download.
     → DONE in xlsx_generator: now reads the actual stored records.
  5. Persist `scrub_jobs.{validated_count, unique_count, overlap_count}`.
     → DONE — this function mutates the job row in place.
"""
import random
import logging

from sqlalchemy import text

from app.extensions import get_db
from app.services.pricing import calc_scrub_price_cents

logger = logging.getLogger(__name__)


def run_mock_scrub_on_records(job):
    """Score every imported record + update aggregate counts + price on `job`.

    Operates on the rows the import worker just wrote. The bucketing math
    (valid % and unique %) is still a random STUB pending the real email
    validator + the real overlap-check, but it's applied with set-based SQL —
    a handful of statements for the whole job, regardless of row count (the old
    per-row loop took ~19 min on an 878k-row job). Three passes leave every row
    with is_valid set; then we count the buckets.
    """
    db = get_db()
    sid = job.id

    # Realistic stub thresholds, picked once per job so results are stable
    # within a single scrub but vary across uploads.
    valid_rate = random.uniform(0.91, 0.95) if job.cleaning_opted_in else 1.0
    unique_rate = random.uniform(0.32, 0.52)

    # 1) No email (or no email column mapped) → can't validate/match → invalid.
    db.execute(text("""
        UPDATE scrub_job_records
           SET is_valid = 0, invalid_reason = 'syntax', is_unique = 0
         WHERE scrub_job_id = :sid
           AND (email_normalized IS NULL OR email_normalized = '')
    """), {'sid': sid})

    # 2) With cleaning, randomly drop ~(1 - valid_rate) of emailed rows.
    if job.cleaning_opted_in:
        db.execute(text("""
            UPDATE scrub_job_records
               SET is_valid = 0, invalid_reason = 'undeliverable', is_unique = 0
             WHERE scrub_job_id = :sid
               AND email_normalized IS NOT NULL AND email_normalized <> ''
               AND is_valid IS NULL
               AND RAND() >= :vr
        """), {'sid': sid, 'vr': valid_rate})

    # 3) Remaining emailed rows → valid, with a random unique/overlap split.
    db.execute(text("""
        UPDATE scrub_job_records
           SET is_valid = 1, invalid_reason = NULL, is_unique = (RAND() < :ur)
         WHERE scrub_job_id = :sid
           AND email_normalized IS NOT NULL AND email_normalized <> ''
           AND is_valid IS NULL
    """), {'sid': sid, 'ur': unique_rate})
    db.flush()

    def _count(where):
        return db.execute(
            text(f"SELECT COUNT(*) FROM scrub_job_records WHERE scrub_job_id = :sid AND {where}"),
            {'sid': sid},
        ).scalar() or 0

    uploaded = _count("1 = 1")
    invalid = _count("is_valid = 0")
    validated = _count("is_valid = 1")
    unique = _count("is_valid = 1 AND is_unique = 1")
    overlap = _count("is_valid = 1 AND is_unique = 0")

    price = calc_scrub_price_cents(unique, cleaning=bool(job.cleaning_opted_in))

    job.uploaded_count = uploaded
    job.validated_count = validated
    job.invalid_count = invalid
    job.unique_count = unique
    job.overlap_count = overlap
    job.rate_per_record = price['rate_per_record']
    job.price_cents = price['price_cents']
    job.status = 'priced'
    return job


# ── Backward-compatible alias ──
# Some older code paths (and tests) may still import run_mock_scrub. Keep it
# importable but make it raise so we notice if anything still uses the
# file-size-based stub — it should be dead now.
def run_mock_scrub(*args, **kwargs):
    raise RuntimeError(
        "run_mock_scrub is removed — use run_mock_scrub_on_records via the import worker"
    )
