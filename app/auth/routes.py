"""Mailer auth endpoints: signup, login, logout, /me."""
import logging
import re
from datetime import datetime
from flask import Blueprint, jsonify, request, g
from werkzeug.security import generate_password_hash, check_password_hash

from app.extensions import get_db
from app.models.mailer_user import MailerUser
from app.models.mailer_company import MailerCompany
from app.models.activity_log import ACTION_SIGNUP, ACTION_LOGIN, ACTION_LOGOUT
from app.auth.jwt_utils import issue_token, set_session_cookie, clear_session_cookie
from app.auth.decorators import mailer_login_required
from app.services.activity_logger import log_activity

logger = logging.getLogger(__name__)
auth_bp = Blueprint('auth', __name__, url_prefix='/api/auth')

EMAIL_RE = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')


def _err(msg, status=400):
    return jsonify({'error': msg}), status


@auth_bp.route('/signup', methods=['POST'])
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

    db = get_db()
    if db is None:
        return _err('Database unavailable', 503)

    if db.query(MailerUser).filter_by(email=email).first():
        return _err('An account with that email already exists', 409)

    company = db.query(MailerCompany).filter_by(company_name=company_name).first()
    if company is None:
        company = MailerCompany(company_name=company_name, status='active')
        db.add(company)
        db.flush()

    user = MailerUser(
        company_id=company.id,
        full_name=full_name,
        email=email,
        phone=phone or None,
        password_hash=generate_password_hash(password),
        role='owner',
        last_login_at=datetime.utcnow(),
    )
    db.add(user)
    db.commit()

    token = issue_token(user.id, company.id)
    resp = jsonify({
        'user': user.to_dict(),
        'company': company.to_dict(),
    })
    set_session_cookie(resp, token)
    log_activity(company.id, ACTION_SIGNUP, user_id=user.id,
                 meta={'email': email, 'company_name': company_name})
    return resp


@auth_bp.route('/login', methods=['POST'])
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
