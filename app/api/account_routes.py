"""Account-page API: read + update profile, company, saved card.

All endpoints require a mailer login; updates only ever touch the caller's
own row (defence in depth — the JWT decides the company, not the request).
"""
import re
import logging
from datetime import datetime
from flask import Blueprint, jsonify, request, g

from app.extensions import get_db
from app.auth.decorators import mailer_login_required
from app.models.mailer_user import MailerUser
from app.models.mailer_company import MailerCompany
from app.models.payment_method import PaymentMethod
from app.services.stripe_stub import (
    attach_payment_method,
    detach_payment_method,
    CardValidationError,
)

logger = logging.getLogger(__name__)
account_bp = Blueprint('account', __name__, url_prefix='/api/account')

EMAIL_RE = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')


def _err(msg, status=400):
    return jsonify({'error': msg}), status


def _serialize(user, company, pm):
    return {
        'user': user.to_dict(),
        'company': company.to_dict() if company else None,
        'payment_method': pm.to_dict() if pm else None,
    }


@account_bp.route('', methods=['GET'])
@mailer_login_required
def get_account():
    db = get_db()
    pm = db.query(PaymentMethod).filter_by(company_id=g.current_company.id).first()
    return jsonify(_serialize(g.current_user, g.current_company, pm))


@account_bp.route('/profile', methods=['PUT'])
@mailer_login_required
def update_profile():
    data = request.get_json(silent=True) or {}
    full_name = (data.get('full_name') or '').strip()
    email = (data.get('email') or '').strip().lower()
    phone = (data.get('phone') or '').strip()

    if not full_name:
        return _err('Full name is required')
    if not EMAIL_RE.match(email):
        return _err('Valid email is required')

    db = get_db()
    user = g.current_user

    if email != user.email:
        clash = db.query(MailerUser).filter(MailerUser.email == email,
                                            MailerUser.id != user.id).first()
        if clash:
            return _err('An account with that email already exists', 409)

    user.full_name = full_name
    user.email = email
    user.phone = phone or None
    db.commit()
    return jsonify(user.to_dict())


@account_bp.route('/company', methods=['PUT'])
@mailer_login_required
def update_company():
    if g.current_user.role != 'owner':
        return _err('Only an owner can change company details', 403)

    data = request.get_json(silent=True) or {}
    company_name = (data.get('company_name') or '').strip()
    if not company_name:
        return _err('Company name is required')

    db = get_db()
    company = g.current_company

    if company_name != company.company_name:
        clash = db.query(MailerCompany).filter(
            MailerCompany.company_name == company_name,
            MailerCompany.id != company.id,
        ).first()
        if clash:
            return _err('Another company already uses that name', 409)

    company.company_name = company_name
    company.updated_at = datetime.utcnow()
    db.commit()
    return jsonify(company.to_dict())


@account_bp.route('/payment-method', methods=['PUT'])
@mailer_login_required
def upsert_payment_method():
    data = request.get_json(silent=True) or {}
    try:
        result = attach_payment_method(
            card_number=data.get('card_number', ''),
            exp_month=data.get('exp_month'),
            exp_year=data.get('exp_year'),
            cvc=data.get('cvc', ''),
            cardholder_name=data.get('cardholder_name', ''),
        )
    except CardValidationError as e:
        return _err(str(e))

    db = get_db()
    pm = db.query(PaymentMethod).filter_by(company_id=g.current_company.id).first()
    if pm is None:
        pm = PaymentMethod(company_id=g.current_company.id,
                           brand=result['brand'],
                           last4=result['last4'],
                           exp_month=result['exp_month'],
                           exp_year=result['exp_year'],
                           cardholder_name=result['cardholder_name'],
                           stripe_payment_method_id=result['id'])
        db.add(pm)
    else:
        # If we had a previous stripe id, detach it (stub no-op for now).
        if pm.stripe_payment_method_id:
            try:
                detach_payment_method(pm.stripe_payment_method_id)
            except Exception:
                logger.exception('Failed to detach previous payment method (non-fatal)')
        pm.brand = result['brand']
        pm.last4 = result['last4']
        pm.exp_month = result['exp_month']
        pm.exp_year = result['exp_year']
        pm.cardholder_name = result['cardholder_name']
        pm.stripe_payment_method_id = result['id']
        pm.updated_at = datetime.utcnow()
    db.commit()
    return jsonify(pm.to_dict())


@account_bp.route('/payment-method', methods=['DELETE'])
@mailer_login_required
def remove_payment_method():
    db = get_db()
    pm = db.query(PaymentMethod).filter_by(company_id=g.current_company.id).first()
    if not pm:
        return jsonify({'ok': True, 'noop': True})
    if pm.stripe_payment_method_id:
        try:
            detach_payment_method(pm.stripe_payment_method_id)
        except Exception:
            logger.exception('Failed to detach payment method at Stripe (non-fatal)')
    db.delete(pm)
    db.commit()
    return jsonify({'ok': True})
