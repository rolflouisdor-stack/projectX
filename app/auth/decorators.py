"""@mailer_login_required + a current-user helper.

The decorator populates flask.g.current_user / g.current_company on success and
redirects (HTML) or returns 401 (JSON/api) on failure.
"""
from functools import wraps
from flask import request, redirect, jsonify, g
from app.auth.jwt_utils import verify_token, COOKIE_NAME
from app.extensions import get_db


def _load_user(token):
    payload = verify_token(token)
    if not payload:
        return None, None
    from app.models.mailer_user import MailerUser
    from app.models.mailer_company import MailerCompany
    db = get_db()
    if not db:
        return None, None
    user = db.query(MailerUser).filter_by(id=payload.get('uid')).first()
    if not user:
        return None, None
    company = db.query(MailerCompany).filter_by(id=user.company_id).first()
    return user, company


def _wants_json():
    if request.path.startswith('/api/'):
        return True
    accept = request.headers.get('Accept', '')
    return 'application/json' in accept and 'text/html' not in accept


def mailer_login_required(fn):
    @wraps(fn)
    def inner(*args, **kwargs):
        token = request.cookies.get(COOKIE_NAME)
        user, company = _load_user(token)
        if not user:
            if _wants_json():
                return jsonify({'error': 'not authenticated'}), 401
            return redirect('/login')
        g.current_user = user
        g.current_company = company
        return fn(*args, **kwargs)
    return inner


def current_user_or_none():
    """Best-effort load; safe to call from any handler without enforcement."""
    token = request.cookies.get(COOKIE_NAME)
    return _load_user(token)
