"""Mailer auth endpoints: signup, login, logout, /me, verify, resend."""
import hashlib
import logging
import re
import secrets
import urllib.request
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request, g
from werkzeug.security import generate_password_hash, check_password_hash

from app.extensions import get_db, limiter
from app.models.mailer_user import MailerUser
from app.models.mailer_company import MailerCompany
from app.models.activity_log import ACTION_SIGNUP, ACTION_LOGIN, ACTION_LOGOUT
from app.auth.jwt_utils import issue_token, set_session_cookie, clear_session_cookie
from app.auth.decorators import mailer_login_required
from app.services.activity_logger import log_activity
from app.services import email_service

logger = logging.getLogger(__name__)
auth_bp = Blueprint('auth', __name__, url_prefix='/api/auth')

EMAIL_RE = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
VERIFICATION_TTL = timedelta(hours=24)

# Generic signup reply — identical whether or not the email already exists, so
# the endpoint can't be used to enumerate registered users.
GENERIC_SIGNUP_MSG = ('If that email is new, check your inbox for a link to '
                      'verify your account. If you already have an account, '
                      'we\'ve emailed you a sign-in reminder.')


def _err(msg, status=400):
    return jsonify({'error': msg}), status


def _login_email_key():
    """Rate-limit key for the per-account login limit: stops a password-spray
    against one email even if the attacker rotates IPs."""
    data = request.get_json(silent=True) or {}
    return 'login:' + (data.get('email') or '').strip().lower()


def _password_pwned(password):
    """True if the password appears in the Have I Been Pwned breach corpus,
    checked via the k-anonymity range API (only the first 5 SHA-1 hex chars
    leave this server; the full hash never does). Fails OPEN — if HIBP is
    unreachable we allow the password rather than block signups on a 3rd-party
    outage."""
    try:
        sha1 = hashlib.sha1(password.encode('utf-8')).hexdigest().upper()
        prefix, suffix = sha1[:5], sha1[5:]
        req = urllib.request.Request(
            f'https://api.pwnedpasswords.com/range/{prefix}',
            headers={'User-Agent': 'gravitas-mailer-signup'})
        with urllib.request.urlopen(req, timeout=3) as r:
            body = r.read().decode('utf-8', 'ignore')
        return any(line.split(':', 1)[0] == suffix for line in body.splitlines())
    except Exception as e:
        logger.warning('HIBP check skipped (allowing): %s', e)
        return False


def _new_verification_token():
    return secrets.token_urlsafe(32)


@auth_bp.route('/signup', methods=['POST'])
@limiter.limit("5 per minute;20 per hour")
def signup():
    data = request.get_json(silent=True) or {}
    full_name = (data.get('full_name') or '').strip()
    company_name = (data.get('company_name') or '').strip()
    email = (data.get('email') or '').strip().lower()
    phone = (data.get('phone') or '').strip()
    password = data.get('password') or ''

    if not full_name:    return _err('Full name is required')
    if not company_name: return _err('Company name is required')
    if not EMAIL_RE.match(email): return _err('Valid email is required')
    if len(password) < 8: return _err('Password must be at least 8 characters')
    # #5: reject passwords known to be compromised (NIST 800-63B guidance).
    if _password_pwned(password):
        return _err('This password has appeared in a known data breach. '
                    'Please choose a different password.')

    db = get_db()
    if db is None:
        return _err('Database unavailable', 503)

    existing = db.query(MailerUser).filter_by(email=email).first()
    if existing:
        # Never reveal that the account exists (no enumeration). If it's an
        # unverified signup, re-send the link so they can finish; if verified,
        # nudge them to sign in. Either way the response below is identical.
        if not existing.email_verified:
            existing.verification_token = _new_verification_token()
            existing.verification_sent_at = datetime.utcnow()
            db.commit()
            email_service.send_verification_email(existing.email, existing.full_name,
                                                  existing.verification_token)
        else:
            email_service.send_existing_account_notice(existing.email, existing.full_name)
        return jsonify({'message': GENERIC_SIGNUP_MSG}), 200

    company = db.query(MailerCompany).filter_by(company_name=company_name).first()
    if company is None:
        company = MailerCompany(company_name=company_name, status='active')
        db.add(company)
        db.flush()

    token = _new_verification_token()
    user = MailerUser(
        company_id=company.id,
        full_name=full_name,
        email=email,
        phone=phone or None,
        password_hash=generate_password_hash(password),
        role='owner',
        email_verified=False,                 # must confirm via the emailed link
        verification_token=token,
        verification_sent_at=datetime.utcnow(),
        last_login_at=None,                    # not logged in until verified
    )
    db.add(user)
    db.commit()

    log_activity(company.id, ACTION_SIGNUP, user_id=user.id,
                 meta={'email': email, 'company_name': company_name, 'verified': False})
    email_service.send_verification_email(user.email, user.full_name, token)
    # No session cookie — the account is inert until the email link is clicked.
    return jsonify({'message': GENERIC_SIGNUP_MSG}), 200


@auth_bp.route('/login', methods=['POST'])
@limiter.limit("10 per minute;100 per hour")
@limiter.limit("5 per minute;20 per hour", key_func=_login_email_key)
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''
    if not email or not password:
        return _err('Email and password required')

    db = get_db()
    if db is None:
        return _err('Database unavailable', 503)

    user = db.query(MailerUser).filter_by(email=email).first()
    if not user or not check_password_hash(user.password_hash, password):
        return _err('Invalid email or password', 401)

    # Block sign-in until the email is verified (existing users were grandfathered
    # verified at migration time). Surface a flag so the UI can offer "resend".
    if not user.email_verified:
        return jsonify({'error': 'Please verify your email first — check your inbox '
                                 'for the verification link.',
                        'unverified': True, 'email': user.email}), 403

    company = db.query(MailerCompany).filter_by(id=user.company_id).first()
    user.last_login_at = datetime.utcnow()
    db.commit()

    token = issue_token(user.id, company.id)
    resp = jsonify({
        'user': user.to_dict(),
        'company': company.to_dict() if company else None,
    })
    set_session_cookie(resp, token)
    log_activity(company.id if company else None, ACTION_LOGIN, user_id=user.id)
    return resp


@auth_bp.route('/resend-verification', methods=['POST'])
@limiter.limit("3 per minute;10 per hour")
def resend_verification():
    """Re-send the verification link. Generic response (no enumeration): always
    'check your email' regardless of whether the address exists / is verified."""
    data = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()
    generic = jsonify({'message': 'If that account exists and is unverified, '
                                  'we\'ve sent a fresh verification link.'}), 200
    if not EMAIL_RE.match(email):
        return generic
    db = get_db()
    if db is None:
        return generic
    user = db.query(MailerUser).filter_by(email=email).first()
    if user and not user.email_verified:
        user.verification_token = _new_verification_token()
        user.verification_sent_at = datetime.utcnow()
        db.commit()
        email_service.send_verification_email(user.email, user.full_name,
                                              user.verification_token)
    return generic


@auth_bp.route('/logout', methods=['POST'])
def logout():
    from app.auth.decorators import current_user_or_none
    user, company = current_user_or_none()
    if user and company:
        log_activity(company.id, ACTION_LOGOUT, user_id=user.id)

    resp = jsonify({'ok': True})
    clear_session_cookie(resp)
    return resp


@auth_bp.route('/me', methods=['GET'])
@mailer_login_required
def me():
    from app.auth.decorators import is_admin_user
    user = g.current_user
    company = g.current_company
    return jsonify({
        'user': user.to_dict(),
        'company': company.to_dict() if company else None,
        'is_admin': is_admin_user(user),
    })
