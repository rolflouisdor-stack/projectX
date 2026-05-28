"""Pricing math shared by scrub and purchase flows.

All amounts handled as cents (int) until the very last presentation layer.
"""
from decimal import Decimal
from app.extensions import get_db
from app.models.vertical import Vertical
from app.models.pricing_tier import PricingTier
from app.models.promo_code import PromoCode
from datetime import datetime


SCRUB_BASE_RATE = Decimal('0.018')        # $/unique-record
SCRUB_CLEAN_PREMIUM = Decimal('1.20')     # +20% if cleaning opted-in


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
