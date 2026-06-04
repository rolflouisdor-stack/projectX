"""EmailOversight cleaning workers.

Three RQ jobs implement the EO flow (see FTP.md):
  - eo_quote_job   : count records in the upload + compute the EO clean price,
                     then mark the job `priced` (so the user can pay).
  - eo_submit_job  : after payment, upload the file to EO's FTP, mark
                     `awaiting_ftp_result`, and schedule the first poll.
  - eo_poll_job    : look for OUR processed file in /cx3ads/processed/ (matched
                     by stem prefix — EO inserts its own id), confirm it's
                     size-stable across two polls, retrieve it to Spaces, mark
                     `complete`, and email the user. Reschedules itself until the
                     file lands or the window elapses (EO is a FIFO queue — a
                     tiny file can wait behind a big CX3-ops job; SLA up to
                     ~30 h for 10M rows).

Sends are best-effort; a notification failure never breaks the job.
"""
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

POLL_INTERVAL_SEC = 180          # 3 min between polls
POLL_MAX_ATTEMPTS = 720          # ~36 h ceiling (EO SLA ~24-30 h for 10M rows)


def _app():
    from app import create_app
    from config import DevelopmentConfig, ProductionConfig
    cls = ProductionConfig if os.getenv('FLASK_ENV') == 'production' else DevelopmentConfig
    return create_app(cls)


def _company_name(db, job):
    try:
        from app.models.mailer_company import MailerCompany
        c = db.query(MailerCompany).filter_by(id=job.company_id).first()
        return c.company_name if c else None
    except Exception:
        return None


def _user_email(db, job):
    try:
        from app.models.mailer_user import MailerUser
        u = db.query(MailerUser).filter_by(id=job.user_id).first()
        return u.email if u else None
    except Exception:
        return None


def _fail(db, scrub_job_id, reason, email=True):
    """Roll back, mark failed on a clean session, optionally notify."""
    db.rollback()
    from app.models.scrub_job import ScrubJob
    job = db.query(ScrubJob).filter_by(id=scrub_job_id).first()
    if not job:
        return
    job.status = 'failed'
    job.failure_reason = str(reason)[:500]
    db.commit()
    if email:
        try:
            from app.services.email_service import notify_scrub_failed
            notify_scrub_failed(job, _user_email(db, job))
        except Exception:
            logger.warning("failure notification failed for scrub_job %s", scrub_job_id, exc_info=True)


def eo_quote_job(scrub_job_id: int):
    """Count records + price the EO clean job; mark priced."""
    app = _app()
    with app.app_context():
        from app.extensions import get_db
        from app.models.scrub_job import ScrubJob
        from app.services.header_detector import count_data_rows
        from app.services.pricing import calc_eo_clean_price
        db = get_db()
        job = db.query(ScrubJob).filter_by(id=scrub_job_id).first()
        if not job:
            return
        try:
            n = count_data_rows(job.s3_key, job.original_filename)
            price = calc_eo_clean_price(n)
            job.uploaded_count = n
            job.rate_per_record = price['rate_per_record']
            job.price_cents = price['price_cents']
            job.status = 'priced'
            db.commit()
            logger.info("eo_quote: scrub_job %s -> %s records, $%.2f",
                        scrub_job_id, n, price['price_cents'] / 100)
        except Exception as e:
            logger.exception("eo_quote failed for scrub_job %s", scrub_job_id)
            _fail(db, scrub_job_id, e, email=False)   # not paid yet; surface in UI
            raise


def eo_submit_job(scrub_job_id: int):
    """Upload the paid job's file to EO FTP; schedule the first poll."""
    app = _app()
    with app.app_context():
        from app.extensions import get_db
        from app.models.scrub_job import ScrubJob
        from app.services import eo_ftp
        from app.jobs.queue import schedule_eo_poll
        db = get_db()
        job = db.query(ScrubJob).filter_by(id=scrub_job_id).first()
        if not job:
            return
        try:
            job.status = 'submitting_ftp'
            db.commit()
            cname = _company_name(db, job)
            fmt = 'xlsx' if (job.original_filename or '').lower().endswith('.xlsx') else 'csv'
            raw = eo_ftp.submit(job, cname, fmt, job.delimiter, job.email_column_index)
            job.ftp_submitted_filename = raw
            job.ftp_submitted_at = datetime.utcnow()
            job.status = 'awaiting_ftp_result'
            db.commit()
            schedule_eo_poll(scrub_job_id, attempt=1, last_size=None)
            try:
                from app.services.email_service import notify_eo_payment_received
                notify_eo_payment_received(job, _user_email(db, job))
            except Exception:
                logger.warning("payment-received notification failed for scrub_job %s", scrub_job_id, exc_info=True)
            logger.info("eo_submit: scrub_job %s submitted as %s", scrub_job_id, raw)
        except Exception as e:
            logger.exception("eo_submit failed for scrub_job %s", scrub_job_id)
            _fail(db, scrub_job_id, e)
            raise


def eo_poll_job(scrub_job_id: int, attempt: int = 1, last_size=None):
    """Poll EO's processed/ folder for our result; retrieve + complete, or
    reschedule. Self-rescheduling via RQ's scheduler (enqueue_in)."""
    app = _app()
    with app.app_context():
        from app.extensions import get_db
        from app.models.scrub_job import ScrubJob
        from app.services import eo_ftp, storage
        from app.jobs.queue import schedule_eo_poll
        db = get_db()
        job = db.query(ScrubJob).filter_by(id=scrub_job_id).first()
        if not job or job.status != 'awaiting_ftp_result':
            return   # already complete/failed/superseded
        try:
            name, size = eo_ftp.find_processed(job.ftp_submitted_filename)
            if name is not None:
                # Settled = size known + >0 + unchanged since the previous poll
                # (last_size carried in the scheduled args), or size unavailable.
                settled = (size == -1) or (size is not None and size > 0 and size == last_size)
                if not settled:
                    schedule_eo_poll(scrub_job_id, attempt + 1, size)
                    return
                fname = f"gravitas_clean_{job.id}_{datetime.utcnow():%Y%m%d}.csv"
                dest = storage.result_key(job.company_id, job.id, fname)
                eo_ftp.retrieve_to_spaces(name, dest)
                job.ftp_processed_filename = name
                job.result_s3_key = dest
                job.result_filename = fname
                job.ftp_result_ingested_at = datetime.utcnow()
                job.completed_at = datetime.utcnow()
                job.status = 'complete'
                db.commit()
                try:
                    from app.services.email_service import notify_eo_complete
                    notify_eo_complete(job, _user_email(db, job))
                except Exception:
                    logger.warning("complete notification failed for scrub_job %s", scrub_job_id, exc_info=True)
                logger.info("eo_poll: scrub_job %s complete (%s)", scrub_job_id, name)
                return

            # Not there yet.
            if attempt >= POLL_MAX_ATTEMPTS:
                _fail(db, scrub_job_id, 'EmailOversight result not received within the expected window')
                return
            schedule_eo_poll(scrub_job_id, attempt + 1, None)
        except Exception as e:
            # Transient FTP/Spaces hiccup → reschedule (counts toward attempts);
            # only give up once the window is exhausted.
            logger.warning("eo_poll error for scrub_job %s (attempt %s): %s", scrub_job_id, attempt, e)
            db.rollback()
            if attempt < POLL_MAX_ATTEMPTS:
                try:
                    schedule_eo_poll(scrub_job_id, attempt + 1, last_size)
                except Exception:
                    logger.exception("could not reschedule eo_poll for scrub_job %s", scrub_job_id)
            else:
                _fail(db, scrub_job_id, f'EmailOversight polling failed: {e}')
