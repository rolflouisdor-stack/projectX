"""Pricing math shared by scrub and purchase flows.

All amounts handled as cents (int) until the very last presentation layer.
"""
import math
from decimal import Decimal
from flask import current_app
from app.extensions import get_db
from app.models.vertical import Vertical
from app.models.pricing_tier import PricingTier
from app.models.promo_code import PromoCode
from datetime import datetime


SCRUB_BASE_RATE = Decimal('0.018')        # $/unique-record
SCRUB_CLEAN_PREMIUM = Decimal('1.20')     # +20% if cleaning opted-in


def eo_margin_pct(record_count: int) -> Decimal:
    """Our markup % for an EO clean, tiered by list size — bigger lists get a
    smaller margin. Thresholds + percentages are config-tunable (see config.py).
        < EO_TIER_MID_MIN (100k)          -> EO_MARGIN_PCT_SMALL (40%)
        EO_TIER_MID_MIN .. <LARGE (150k)  -> EO_MARGIN_PCT_MID   (35%)
        >= EO_TIER_LARGE_MIN (150k)       -> EO_MARGIN_PCT_LARGE (30%)
    """
    cfg = current_app.config
    n = max(0, int(record_count or 0))
    if n >= int(cfg.get('EO_TIER_LARGE_MIN') or 150_000):
        return Decimal(str(cfg.get('EO_MARGIN_PCT_LARGE') or 30))
    if n >= int(cfg.get('EO_TIER_MID_MIN') or 100_000):
        return Decimal(str(cfg.get('EO_MARGIN_PCT_MID') or 35))
    return Decimal(str(cfg.get('EO_MARGIN_PCT_SMALL') or 40))


def add_processing_fee(base_cents: int) -> dict:
    """Gross up an order total so the Stripe payout nets `base_cents`.

    Stripe's fee is pct*gross + fixed, charged on the total. To net `base`:
        net = total - (pct*total + fixed) = base  ⇒  total = (base + fixed)/(1 - pct)
    Returns {base_cents, fee_cents, total_cents}. No-op when pass-through is off
    or base is 0 (free / nothing to charge).
    """
    cfg = current_app.config
    base = max(0, int(base_cents or 0))
    if base <= 0 or not cfg.get('STRIPE_PASS_FEE'):
        return {'base_cents': base, 'fee_cents': 0, 'total_cents': base}
    pct = Decimal(str(cfg.get('STRIPE_FEE_PERCENT') or 0)) / Decimal(100)
    fixed = int(cfg.get('STRIPE_FEE_FIXED_CENTS') or 0)
    if pct >= 1:
        return {'base_cents': base, 'fee_cents': 0, 'total_cents': base}
    total = int(math.ceil((Decimal(base) + Decimal(fixed)) / (Decimal(1) - pct)))
    return {'base_cents': base, 'fee_cents': total - base, 'total_cents': total}


def effective_total_cents(price_cents, amount_paid_cents=0) -> int:
    """The amount the customer pays/paid INCLUDING the card processing fee.

    Paid jobs return the exact charge stored at pay time; unpaid (quote) jobs
    return the grossed-up total they would be charged. This is the single number
    every customer-facing surface (checkout, email, job history, dashboard)
    should show — never the pre-fee base `price_cents`. Falls back to the base
    price if the fee config can't be read.
    """
    paid = int(amount_paid_cents or 0)
    if paid > 0:
        return paid
    try:
        return int(add_processing_fee(int(price_cents or 0)).get('total_cents', price_cents or 0))
    except Exception:
        return int(price_cents or 0)


def calc_eo_clean_price(record_count: int) -> dict:
    """Price an EmailOversight cleaning job: per-record EO cost + tiered margin.

    Rate (EO_PRICE_PER_RECORD) + the tiered margin come from config so they can
    be tuned without a redeploy. We charge upfront on total record count; EO
    doesn't bill us for 'Unknown' (code 11) rows, which nets as extra margin
    (see FTP.md §12). Returns {rate_per_record, margin_pct, eo_cost_cents, price_cents}.
    """
    cfg = current_app.config
    eo_rate = Decimal(str(cfg.get('EO_PRICE_PER_RECORD') or 0))   # $/record EO charges us
    n = max(0, int(record_count or 0))
    margin_pct = eo_margin_pct(n)
    margin = margin_pct / Decimal(100)

    eo_cost = Decimal(n) * eo_rate                       # our cost from EO ($)
    user_rate = eo_rate * (Decimal(1) + margin)          # $/record charged to user
    user_total = Decimal(n) * user_rate                  # user pays ($)
    price_cents = max(0, int(math.ceil(user_total * 100)))
    # Enforce a minimum order total (Stripe rejects < $0.50). Only when there's
    # something to clean — a 0-record job stays $0 and is blocked earlier.
    if n > 0:
        price_cents = max(price_cents, int(cfg.get('MIN_CHARGE_CENTS') or 50))
    return {
        'rate_per_record': float(user_rate),
        'margin_pct': float(margin_pct),
        'eo_cost_cents': int(math.ceil(eo_cost * 100)),
        'price_cents': price_cents,
    }


def tier_discount_pct(volume: int) -> Decimal:
    """Return the tier discount as a Decimal percentage (0..100)."""
    db = get_db()
    best = Decimal('0')
    if db is None:
        return best
    for tier in db.query(PricingTier).filter_by(is_active=True).order_by(PricingTier.min_volume).all():
        if volume >= int(tier.min_volume):
            best = Decimal(str(tier.discount_pct or 0))
    return best


def calc_scrub_price_cents(unique_count: int, *, cleaning: bool) -> dict:
    """Returns {rate_per_record, price_cents}."""
    rate = SCRUB_BASE_RATE * (SCRUB_CLEAN_PREMIUM if cleaning else Decimal('1'))
    cents = int(Decimal(unique_count) * rate * 100)
    return {'rate_per_record': float(rate), 'price_cents': max(0, cents)}


def calc_purchase_quote(selected_verticals, volume: int, promo_code=None):
    """Compute a purchase quote.

    selected_verticals: [{'vertical_id': int, 'subcategory_ids': [int, ...]}]
    volume: total records requested (distributed evenly across verticals)
    promo_code: optional code string

    Returns:
      {
        rows: [{vertical_id, vertical_name, records, base_rate, line_cents}],
        subtotal_cents, tier_discount_cents, promo_discount_cents,
        discount_cents, price_cents, tier_pct, promo
      }
    """
    db = get_db()
    rows = []
    subtotal_cents = 0
    if not selected_verticals or volume <= 0 or db is None:
        return {
            'rows': [], 'subtotal_cents': 0, 'tier_discount_cents': 0,
            'promo_discount_cents': 0, 'discount_cents': 0, 'price_cents': 0,
            'tier_pct': 0.0, 'promo': None,
        }

    even_share = volume // max(1, len(selected_verticals))
    for sel in selected_verticals:
        vid = sel.get('vertical_id')
        if not vid:
            continue
        v = db.query(Vertical).filter_by(id=vid).first()
        if not v:
            continue
        records = min(even_share, int(v.available_records or 0))
        rate = Decimal(str(v.base_rate or 0))
        line_cents = int(Decimal(records) * rate * 100)
        rows.append({
            'vertical_id': v.id,
            'vertical_name': v.display_name,
            'records': records,
            'base_rate': float(rate),
            'line_cents': line_cents,
        })
        subtotal_cents += line_cents

    tier_pct = tier_discount_pct(volume)
    tier_discount_cents = int(Decimal(subtotal_cents) * tier_pct / Decimal(100))

    promo_discount_cents = 0
    promo_payload = None
    if promo_code:
        code = (promo_code or '').strip().upper()
        if code:
            pc = db.query(PromoCode).filter_by(code=code).first()
            now = datetime.utcnow()
            valid = (
                pc and pc.is_active
                and (not pc.starts_at or pc.starts_at <= now)
                and (not pc.ends_at or pc.ends_at >= now)
                and (not pc.max_redemptions or (pc.redeemed_count or 0) < pc.max_redemptions)
            )
            if valid:
                pct = Decimal(str(pc.discount_pct or 0))
                flat = int(pc.discount_cents or 0)
                promo_discount_cents = int(Decimal(subtotal_cents) * pct / Decimal(100)) + flat
                promo_payload = pc.to_dict()

    discount_cents = tier_discount_cents + promo_discount_cents
    price_cents = max(0, subtotal_cents - discount_cents)

    return {
        'rows': rows,
        'subtotal_cents': subtotal_cents,
        'tier_discount_cents': tier_discount_cents,
        'promo_discount_cents': promo_discount_cents,
        'discount_cents': discount_cents,
        'price_cents': price_cents,
        'tier_pct': float(tier_pct),
        'promo': promo_payload,
    }
