"""Safe, fire-and-forget logger for every meaningful mailer action.

Failures inside the logger never propagate — they should never break the real
endpoint flow. This is the data source behind the cross-system activity API.
"""
import logging
from flask import has_request_context, request
from app.extensions import get_db
from app.models.activity_log import ActivityLog

logger = logging.getLogger(__name__)


def log_activity(company_id, action, *,
                 user_id=None,
                 vertical_id=None,
                 scrub_job_id=None,
                 purchase_job_id=None,
                 meta=None):
    """Insert one row in activity_log. Never raises."""
    if not company_id or not action:
        return
    try:
        db = get_db()
        if db is None:
            return

        ip = None
        ua = None
        if has_request_context():
            ip = (request.headers.get('X-Forwarded-For') or request.remote_addr or '')[:45]
            ua = (request.headers.get('User-Agent') or '')[:255]

        row = ActivityLog(
            company_id=int(company_id),
            user_id=int(user_id) if user_id else None,
            action=str(action)[:40],
            vertical_id=int(vertical_id) if vertical_id else None,
            scrub_job_id=int(scrub_job_id) if scrub_job_id else None,
            purchase_job_id=int(purchase_job_id) if purchase_job_id else None,
            meta=meta or {},
            ip=ip,
            user_agent=ua,
        )
        with db.no_autoflush:
            db.add(row)
        db.commit()
    except Exception as e:
        try:
            db = get_db()
            if db is not None:
                db.rollback()
        except Exception:
            pass
        logger.debug("activity_log write failed (non-fatal): %s", e)
