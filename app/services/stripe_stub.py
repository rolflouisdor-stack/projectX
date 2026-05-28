"""Stripe payment STUB.

The real client lives behind STRIPE_ENABLED=true. Until then, every "payment"
returns a fake payment_intent_id and immediately marks the job as paid.

Real implementation will:
  1. POST /v1/payment_intents with `amount=price_cents, currency=usd, ...`
  2. Return the client_secret so the front-end can call stripe.confirmCardPayment
  3. Verify the webhook for `payment_intent.succeeded` and flip status to 'paid'

For card-on-file (account page), the real flow uses Stripe SetupIntent +
Stripe Elements client-side so raw PAN never touches our servers. Until that
ships, attach_payment_method validates the input, derives brand/last4, and
returns a fake payment-method id we can persist.
"""
import re
import secrets
from datetime import datetime
from flask import current_app


def create_payment_intent(amount_cents: int, *, description: str = '') -> dict:
    if current_app.config.get('STRIPE_ENABLED'):
        raise NotImplementedError(
            "Real Stripe wiring goes here — see Partner_portals_Spec.md §6 (Payments)."
        )
    # Stub success: hand back a fake intent
    return {
        'id': f'pi_stub_{secrets.token_hex(8)}',
        'client_secret': f'pi_stub_{secrets.token_hex(8)}_secret_{secrets.token_hex(8)}',
        'amount': amount_cents,
        'currency': 'usd',
        'status': 'succeeded',
        'description': description,
        'stub': True,
    }


class CardValidationError(ValueError):
    """Raised when the submitted card fields don't pass basic validation."""


def _luhn_ok(digits: str) -> bool:
    """Standard Luhn checksum. `digits` is digits-only."""
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
    """Identify card network from the BIN prefix. Returns lowercase brand."""
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


def attach_payment_method(
    card_number: str,
    exp_month: int,
    exp_year: int,
    cvc: str,
    cardholder_name: str = '',
) -> dict:
    """Validate + persist a card-on-file (stub).

    Raises CardValidationError with a user-safe message on bad input.
    Returns a dict with the safe-to-persist fields plus a fake pm_id.
    """
    if current_app.config.get('STRIPE_ENABLED'):
        raise NotImplementedError(
            "Real Stripe SetupIntent + PaymentMethod wiring goes here."
        )

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
        'brand': _detect_brand(digits),
        'last4': digits[-4:],
        'exp_month': exp_month,
        'exp_year': exp_year,
        'cardholder_name': (cardholder_name or '').strip() or None,
        'stub': True,
    }


def detach_payment_method(pm_id: str) -> dict:
    if current_app.config.get('STRIPE_ENABLED'):
        raise NotImplementedError(
            "Real Stripe PaymentMethod.detach wiring goes here."
        )
    return {'id': pm_id, 'detached': True, 'stub': True}
