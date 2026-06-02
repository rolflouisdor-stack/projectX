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

from app.extensions import get_db
from app.models.scrub_job_record import ScrubJobRecord
from app.services.pricing import calc_scrub_price_cents

logger = logging.getLogger(__name__)


def run_mock_scrub_on_records(job):
    """Score every imported record + update aggregate counts + price on `job`.

    Operates on the rows the import worker just wrote — no file-size
    heuristics, no fake row counts. The bucketing math (valid % and
    unique %) is still random pending the real email validator + the
    real overlap-check; everything else is now backed by real data.
    """
    db = get_db()

    # Realistic stub thresholds, picked once per job so results are stable
    # within a single scrub but vary across uploads.
    valid_rate = random.uniform(0.91, 0.95) if job.cleaning_opted_in else 1.0
    unique_rate = random.uniform(0.32, 0.52)

    invalid_reasons = ('syntax', 'undeliverable', 'disposable', 'role')

    uploaded = 0
    validated = 0
    invalid = 0
    unique = 0
    overlap = 0

    # Process in chunks so we don't load everything in memory at once. Keyset
    # pagination (id > last_id), NOT offset — offset re-scans on every page,
    # which is O(n^2) on a 600k-row job. We read only (id, email_normalized) and
    # write verdicts back with a single bulk UPDATE per batch, instead of the
    # ORM dirty-tracking one UPDATE per row.
    BATCH = 5000
    last_id = 0
    while True:
        rows = (db.query(ScrubJobRecord.id, ScrubJobRecord.email_normalized)
                .filter(ScrubJobRecord.scrub_job_id == job.id,
                        ScrubJobRecord.id > last_id)
                .order_by(ScrubJobRecord.id)
                .limit(BATCH).all())
        if not rows:
            break
        updates = []
        for rid, email_norm in rows:
            uploaded += 1
            # Records with no email (or no email column mapped at all) can't
            # be validated or matched. Mark invalid, keep them in the table.
            if not email_norm:
                updates.append({'id': rid, 'is_valid': False, 'invalid_reason': 'syntax', 'is_unique': False})
                invalid += 1
            elif job.cleaning_opted_in and random.random() > valid_rate:
                updates.append({'id': rid, 'is_valid': False,
                                'invalid_reason': random.choice(invalid_reasons), 'is_unique': False})
                invalid += 1
            else:
                validated += 1
                is_uniq = random.random() < unique_rate
                updates.append({'id': rid, 'is_valid': True, 'invalid_reason': None, 'is_unique': is_uniq})
                if is_uniq:
                    unique += 1
                else:
                    overlap += 1
        db.bulk_update_mappings(ScrubJobRecord, updates)
        db.flush()
        last_id = rows[-1].id

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
