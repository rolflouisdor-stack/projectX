"""Stripe payments gateway.

Real Stripe when STRIPE_ENABLED=true (keys set), otherwise a deterministic stub
so dev/QA without keys still works. Replaces the old stripe_stub module.

Job payments use PaymentIntents confirmed on-page via the Stripe Payment Element;
the authoritative "paid" signal is the `payment_intent.succeeded` webhook, which
advances the job (EO submit / artifact build / purchase complete). Card-on-file
uses a SetupIntent + Customer so a raw PAN never touches our servers.
"""
import re
import secrets
from datetime import datetime

from flask import current_app


class CardValidationError(ValueError):
    """Raised when manually-submitted card fields fail basic validation (stub path)."""


def enabled() -> bool:
    return bool(current_app.config.get('STRIPE_ENABLED'))


def publishable_key() -> str:
    return current_app.config.get('STRIPE_PUBLISHABLE_KEY') or ''


def _stripe():
    """Configured stripe module. Only import/touch when enabled."""
    import stripe
    stripe.api_key = current_app.config.get('STRIPE_SECRET_KEY') or ''
    return stripe


# ── Payment intents (one-off job charges) ──────────────────────────────────

def create_payment_intent(amount_cents: int, *, description: str = '',
                          metadata: dict = None, customer_id: str = None) -> dict:
    """Create a PaymentIntent (or a stub one when disabled).

    Returns a dict with at least: id, client_secret, amount, currency, status.
    """
    amount_cents = int(amount_cents or 0)
    if not enabled():
        tok = secrets.token_hex(8)
        return {
            'id': f'pi_stub_{tok}',
            'client_secret': f'pi_stub_{tok}_secret_{secrets.token_hex(8)}',
            'amount': amount_cents, 'currency': 'usd', 'status': 'succeeded',
            'description': description, 'stub': True,
        }
    s = _stripe()
    kwargs = dict(
        amount=amount_cents, currency='usd',
        description=description or None,
        metadata=metadata or {},
        automatic_payment_methods={'enabled': True},
    )
    if customer_id:
        kwargs['customer'] = customer_id
    pi = s.PaymentIntent.create(**kwargs)
    return {
        'id': pi.id, 'client_secret': pi.client_secret, 'amount': pi.amount,
        'currency': pi.currency, 'status': pi.status, 'stub': False,
    }


def retrieve_payment_intent(pi_id: str):
    if not enabled():
        return {'id': pi_id, 'status': 'succeeded', 'stub': True}
    return _stripe().PaymentIntent.retrieve(pi_id)


# ── Customers + saved cards (SetupIntent) ──────────────────────────────────

def ensure_customer(company) -> str:
    """Return the Stripe Customer id for a company, creating one if needed.
    Caller is responsible for persisting `company.stripe_customer_id`."""
    existing = getattr(company, 'stripe_customer_id', None)
    if existing:
        return existing
    cust = _stripe().Customer.create(
        name=company.company_name,
        metadata={'company_id': company.id},
    )
    return cust.id


def create_setup_intent(customer_id: str) -> dict:
    si = _stripe().SetupIntent.create(
        customer=customer_id,
        usage='off_session',
        automatic_payment_methods={'enabled': True},
    )
    return {'id': si.id, 'client_secret': si.client_secret}


def retrieve_payment_method(pm_id: str):
    return _stripe().PaymentMethod.retrieve(pm_id)


def detach_payment_method(pm_id: str) -> dict:
    if not enabled():
        return {'id': pm_id, 'detached': True, 'stub': True}
    try:
        _stripe().PaymentMethod.detach(pm_id)
    except Exception:
        # Already detached / unknown — treat as success so our row can be removed.
        pass
    return {'id': pm_id, 'detached': True, 'stub': False}


def pm_card_fields(pm) -> dict:
    """Pull the safe-to-store card fields off a Stripe PaymentMethod object."""
    card = (pm.get('card') if isinstance(pm, dict) else getattr(pm, 'card', None)) or {}
    get = (lambda k: card.get(k)) if isinstance(card, dict) else (lambda k: getattr(card, k, None))
    billing = (pm.get('billing_details') if isinstance(pm, dict) else getattr(pm, 'billing_details', None)) or {}
    bget = (lambda k: billing.get(k)) if isinstance(billing, dict) else (lambda k: getattr(billing, k, None))
    pm_id = pm.get('id') if isinstance(pm, dict) else getattr(pm, 'id', None)
    return {
        'id': pm_id,
        'brand': get('brand') or 'card',
        'last4': get('last4') or '',
        'exp_month': int(get('exp_month') or 0),
        'exp_year': int(get('exp_year') or 0),
        'cardholder_name': bget('name'),
    }


# ── Webhook ────────────────────────────────────────────────────────────────

def construct_event(payload: bytes, sig_header: str):
    """Verify + parse a Stripe webhook. Raises on bad signature."""
    secret = current_app.config.get('STRIPE_WEBHOOK_SECRET') or ''
    return _stripe().Webhook.construct_event(payload, sig_header, secret)


# ── Stub manual-card validation (disabled path only) ───────────────────────
# Used by the account page when Stripe is off: validates raw card fields and
# derives brand/last4 without a real gateway. When Stripe is ON the account
# page uses a SetupIntent instead and this is not called.

def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _detect_brand(digits: str) -> str:
    if digits.startswith('4'):
        return 'visa'
    if digits[:2] in ('34', '37'):
        return 'amex'
    if digits.startswith(('51', '52', '53', '54', '55')):
        return 'mastercard'
    if 2221 <= int(digits[:4] or '0') <= 2720:
        return 'mastercard'
    if digits.startswith(('6011', '65')) or digits[:3] in ('644', '645', '646', '647', '648', '649'):
        return 'discover'
    if digits.startswith(('300', '301', '302', '303', '304', '305', '36', '38')):
        return 'diners'
    if digits.startswith(('3528', '3589')):
        return 'jcb'
    return 'card'


def attach_payment_method(card_number: str, exp_month, exp_year, cvc: str,
                          cardholder_name: str = '') -> dict:
    """Validate + return safe card fields (stub path, Stripe disabled).
    Raises CardValidationError on bad input."""
    digits = re.sub(r'\D', '', card_number or '')
    if len(digits) < 13 or len(digits) > 19:
        raise CardValidationError('Card number must be 13–19 digits')
    if not _luhn_ok(digits):
        raise CardValidationError('Card number failed checksum — double-check the digits')
    try:
        exp_month = int(exp_month)
        exp_year = int(exp_year)
    except (TypeError, ValueError):
        raise CardValidationError('Expiry month and year must be numbers')
    if not (1 <= exp_month <= 12):
        raise CardValidationError('Expiry month must be 1–12')
    if exp_year < 100:
        exp_year += 2000
    now = datetime.utcnow()
    if (exp_year, exp_month) < (now.year, now.month):
        raise CardValidationError('Card is expired')
    cvc_digits = re.sub(r'\D', '', cvc or '')
    if len(cvc_digits) not in (3, 4):
        raise CardValidationError('CVC must be 3 or 4 digits')
    return {
        'id': f'pm_stub_{secrets.token_hex(8)}',
        'brand': _detect_brand(digits), 'last4': digits[-4:],
        'exp_month': exp_month, 'exp_year': exp_year,
        'cardholder_name': (cardholder_name or '').strip() or None, 'stub': True,
    }
