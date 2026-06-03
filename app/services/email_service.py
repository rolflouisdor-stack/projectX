"""Transactional email — job notifications.

Provider-agnostic SMTP relay (SendGrid / Postmark / Resend / Mailgun / any SMTP
server). Gated by EMAIL_ENABLED; when off, every call is a no-op so the rest of
the pipeline runs unchanged. Sending is strictly best-effort — a mail failure is
logged and swallowed, never propagated, so it can't fail or roll back a job.

Sent from the RQ worker (inside an app context), which is where jobs reach their
terminal states (priced / complete / failed).
"""
import logging
import smtplib
from email.message import EmailMessage

from flask import current_app

logger = logging.getLogger(__name__)


def _link(path):
    base = (current_app.config.get('PUBLIC_BASE_URL') or '').rstrip('/')
    return f"{base}{path}"


def send_email(to, subject, text, html=None):
    """Send one email via SMTP. Returns True on send, False otherwise. Never
    raises — notifications must not break the job that triggered them."""
    cfg = current_app.config
    if not cfg.get('EMAIL_ENABLED'):
        logger.debug("email disabled — skipping '%s' to %s", subject, to)
        return False
    if not to or not cfg.get('SMTP_HOST'):
        logger.warning("email not sent (missing recipient or SMTP_HOST): %s", subject)
        return False
    try:
        msg = EmailMessage()
        msg['Subject'] = subject
        msg['From'] = cfg.get('EMAIL_FROM')
        msg['To'] = to
        msg.set_content(text)
        if html:
            msg.add_alternative(html, subtype='html')

        host, port = cfg['SMTP_HOST'], int(cfg.get('SMTP_PORT') or 587)
        with smtplib.SMTP(host, port, timeout=20) as s:
            if cfg.get('SMTP_USE_TLS'):
                s.starttls()
            if cfg.get('SMTP_USER'):
                s.login(cfg['SMTP_USER'], cfg.get('SMTP_PASSWORD') or '')
            s.send_message(msg)
        logger.info("email sent: '%s' to %s", subject, to)
        return True
    except Exception as e:
        logger.warning("email send failed ('%s' to %s): %s", subject, to, e)
        return False


# ── Job notifications ──────────────────────────────────────────────────────

def notify_scrub_priced(job, to_email):
    """Scrub finished processing — quote ready to review + pay."""
    link = _link(f"/scrub?job={job.id}")
    fname = job.original_filename or f"scrub #{job.id}"
    subject = f"Your scrub is ready — {fname}"
    text = (
        f"Good news — we finished scrubbing your list \"{fname}\".\n\n"
        f"Uploaded: {int(job.uploaded_count or 0):,} records\n"
        f"Unique matches: {int(job.unique_count or 0):,}\n"
        f"Price: ${int(job.price_cents or 0) / 100:,.2f}\n\n"
        f"Review and pay to download your results:\n{link}\n\n"
        f"— Gravitas Leads"
    )
    return send_email(to_email, subject, text)


def notify_scrub_complete(job, to_email):
    """Payment processed + result file generated — ready to download."""
    link = _link(f"/scrub?job={job.id}")
    fname = job.original_filename or f"scrub #{job.id}"
    subject = f"Your file is ready to download — {fname}"
    text = (
        f"Payment received — your scrubbed file is ready.\n\n"
        f"{int(job.unique_count or 0):,} unique records.\n\n"
        f"Download it here (you may need to sign in):\n{link}\n\n"
        f"— Gravitas Leads"
    )
    return send_email(to_email, subject, text)


def notify_scrub_failed(job, to_email):
    """A job failed — let them know why instead of leaving them waiting."""
    link = _link("/jobs")
    fname = job.original_filename or f"scrub #{job.id}"
    reason = (job.failure_reason or "an unexpected error").strip()
    subject = f"There was a problem with your scrub — {fname}"
    text = (
        f"We hit a problem processing your list \"{fname}\":\n\n"
        f"  {reason}\n\n"
        f"You can review your jobs or start a new scrub here:\n{link}\n\n"
        f"If this keeps happening, reply to this email and we'll help.\n\n"
        f"— Gravitas Leads"
    )
    return send_email(to_email, subject, text)
