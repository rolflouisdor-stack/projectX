"""JWT mint/verify helpers for the mailer session cookie."""
import time
import jwt
from flask import current_app


COOKIE_NAME = 'gm_session'


def issue_token(user_id, company_id):
    secret = current_app.config['SECRET_KEY']
    ttl = current_app.config['JWT_TOKEN_EXPIRY_SECONDS']
    payload = {
        'uid': int(user_id),
        'cid': int(company_id),
        'iat': int(time.time()),
        'exp': int(time.time()) + int(ttl),
    }
    return jwt.encode(payload, secret, algorithm='HS256')


def verify_token(token):
    if not token:
        return None
    try:
        return jwt.decode(token, current_app.config['SECRET_KEY'], algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def set_session_cookie(response, token):
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=current_app.config['JWT_TOKEN_EXPIRY_SECONDS'],
        httponly=True,
        secure=current_app.config['JWT_COOKIE_SECURE'],
        samesite='Lax',
        path='/',
    )


def clear_session_cookie(response):
    response.set_cookie(COOKIE_NAME, '', max_age=0, path='/')
