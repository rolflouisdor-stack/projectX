"""Result-artifact worker.

Runs in the RQ `worker` process. Builds a scrub job's result `.xlsx` from its
imported records and uploads it to Spaces, then flips the job to `complete`.

Why this is a background job and not done in the web request: building the
workbook reads every unique+valid record (138k+ on a large file) and uploads
the result — well past gunicorn's request timeout. The web `pay` endpoint sets
the job to `generating`, enqueues this, and returns immediately; the browser
polls `/api/scrub-jobs/{id}` until `complete`.
"""
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)


def _open_app_context():
    from app import create_app
    from config import DevelopmentConfig, ProductionConfig
    cls = ProductionConfig if os.getenv('FLASK_ENV') == 'production' else DevelopmentConfig
    return create_app(cls)


def _user_email(db, job):
    """Best-effort lookup of the job owner's email for notifications."""
    try:
        from app.models.mailer_user import MailerUser
        u = db.query(MailerUser).filter_by(id=job.user_id).first()
        return u.email if u else None
    except Exception:
        return None


def generate_scrub_artifact_job(scrub_job_id: int):
    """RQ entry point: build + store the scrub result xlsx, mark job complete."""
    app = _open_app_context()
    with app.app_context():
        from app.extensions import get_db
        from app.models.scrub_job import ScrubJob
        from app.services.xlsx_generator import generate_scrub_artifact

        db = get_db()
        job = db.query(ScrubJob).filter_by(id=scrub_job_id).first()
        if not job:
            logger.error("generate_scrub_artifact_job: scrub_job %s not found", scrub_job_id)
            return
        try:
            filename, s3_key = generate_scrub_artifact(job)
            job.result_filename = filename
            job.result_s3_key = s3_key
            job.status = 'complete'
            job.completed_at = datetime.utcnow()
            db.commit()
            logger.info("artifact: scrub_job %s complete (%s)", scrub_job_id, filename)

            # Notify: payment processed + file ready to download (best-effort).
            try:
                from app.services.email_service import notify_scrub_complete
                notify_scrub_complete(job, _user_email(db, job))
            except Exception:
                logger.warning("complete notification failed for scrub_job %s", scrub_job_id, exc_info=True)
        except Exception as e:
            logger.exception("artifact generation failed for scrub_job %s", scrub_job_id)
            # Clear the (possibly poisoned) transaction before recording failure,
            # then re-fetch on the clean session so the UI shows failed, not a
            # job stuck forever in `generating`.
            db.rollback()
            job = db.query(ScrubJob).filter_by(id=scrub_job_id).first()
            if job:
                job.status = 'failed'
                job.failure_reason = str(e)[:500]
                db.commit()
                try:
                    from app.services.email_service import notify_scrub_failed
                    notify_scrub_failed(job, _user_email(db, job))
                except Exception:
                    logger.warning("failure notification failed for scrub_job %s", scrub_job_id, exc_info=True)
            raise
