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


def _amount_paid_cents(job):
    """What the customer actually paid (price_cents grossed up for the Stripe
    fee). Prefer the value stored at pay time; for jobs paid before that was
    persisted, reconstruct it from the current fee config so the email still
    reports the charged total rather than the pre-fee base price."""
    paid = int(getattr(job, 'amount_paid_cents', 0) or 0)
    if paid > 0:
        return paid
    try:
        from app.services.pricing import add_processing_fee
        return int(add_processing_fee(int(job.price_cents or 0)).get('total_cents', 0))
    except Exception:
        return int(job.price_cents or 0)


def notify_eo_payment_received(job, to_email):
    """EO flow: payment confirmed and the list has been handed to EmailOversight
    for cleaning. Sent right after the FTP submit succeeds."""
    link = _link("/jobs")
    fname = job.original_filename or f"list #{job.id}"
    subject = f"Payment received — cleaning your list \"{fname}\""
    text = (
        f"Thanks — your payment was received and we've sent your list \"{fname}\" "
        f"to our validation partner for cleaning.\n\n"
        f"Records to clean: {int(job.uploaded_count or 0):,}\n"
        f"Amount paid: ${_amount_paid_cents(job) / 100:,.2f}\n\n"
        f"This runs in a queue and can take anywhere from a few minutes to several "
        f"hours depending on size. You don't need to wait around — we'll email you "
        f"the moment your cleaned file is ready. You can also check progress here:\n"
        f"{link}\n\n"
        f"— Gravitas Leads"
    )
    return send_email(to_email, subject, text)


def notify_eo_complete(job, to_email):
    """EO flow: cleaned file retrieved from EmailOversight — ready to download."""
    link = _link(f"/scrub?job={job.id}")
    fname = job.original_filename or f"list #{job.id}"
    subject = f"Your cleaned list is ready to download — {fname}"
    text = (
        f"Good news — your list \"{fname}\" has finished cleaning.\n\n"
        f"{int(job.uploaded_count or 0):,} records validated. Your file includes "
        f"each email's deliverability status.\n\n"
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
