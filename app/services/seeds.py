"""Idempotent boot-time seeds: default verticals/subcats, pricing tiers, a
sample promo banner, and the AUTO15 demo promo code.

Safe to run on every boot — never overwrites an existing row.
"""
from datetime import datetime, timedelta
from app.extensions import get_db
from app.models.vertical import Vertical
from app.models.subcategory import Subcategory
from app.models.promo_banner import PromoBanner
from app.models.promo_code import PromoCode
from app.models.pricing_tier import PricingTier


DEFAULT_VERTICALS = [
    {
        'slug': 'auto-ins', 'icon': '🚗', 'name': 'Auto Insurance',
        'desc': 'Quote-intent leads from major carriers.',
        'base_rate': 0.025, 'available': 287492,
        'subcats': ['GEICO', 'Progressive', 'State Farm', 'Allstate', 'Liberty Mutual', 'USAA', 'Other'],
    },
    {
        'slug': 'home-ins', 'icon': '🏠', 'name': 'Home Insurance',
        'desc': 'Homeowners + renters insurance leads.',
        'base_rate': 0.022, 'available': 145221,
        'subcats': ['Homeowners', 'Renters', 'Condo', 'Flood'],
    },
    {
        'slug': 'home-imp', 'icon': '🔨', 'name': 'Home Improvement',
        'desc': 'Renovation, roofing, solar, HVAC, more.',
        'base_rate': 0.020, 'available': 198510,
        'subcats': ['Roofing', 'Windows', 'Solar', 'HVAC', 'Bathroom', 'Kitchen', 'Siding'],
    },
    {
        'slug': 'lead-gen', 'icon': '🧲', 'name': 'Lead Generation',
        'desc': 'General-purpose B2B & B2C lead lists.',
        'base_rate': 0.018, 'available': 412308,
        'subcats': ['Local Services', 'Real Estate', 'Education', 'Financial Services'],
    },
    {
        'slug': 'ecom', 'icon': '🛒', 'name': 'E-commerce',
        'desc': 'Health, beauty, fitness, consumer goods.',
        'base_rate': 0.015, 'available': 532170,
        'subcats': ['Beauty', 'Health & Wellness', 'Fitness', 'Apparel', 'Pet', 'Other'],
    },
]

DEFAULT_TIERS = [
    (1000,    0),
    (10000,   5),
    (25000,  10),
    (50000,  15),
    (100000, 20),
]


def seed_defaults():
    db = get_db()
    if db is None:
        return

    # ── verticals + subcategories
    for i, v in enumerate(DEFAULT_VERTICALS):
        row = db.query(Vertical).filter_by(slug=v['slug']).first()
        if not row:
            row = Vertical(
                slug=v['slug'], display_name=v['name'], icon=v['icon'],
                description=v['desc'], base_rate=v['base_rate'],
                available_records=v['available'], sort_order=i,
            )
            db.add(row)
            db.flush()
            for j, name in enumerate(v['subcats']):
                db.add(Subcategory(vertical_id=row.id, name=name, sort_order=j))

    # ── pricing tiers
    for vol, pct in DEFAULT_TIERS:
        if not db.query(PricingTier).filter_by(min_volume=vol).first():
            db.add(PricingTier(min_volume=vol, discount_pct=pct, sort_order=vol))

    # ── starter promo banner
    if db.query(PromoBanner).count() == 0:
        db.add(PromoBanner(
            tag='Spring Special',
            title='15% off all Auto Insurance data through May 31',
            body='Use code <strong>AUTO15</strong> at checkout. Stack with volume tiers for the best rate of the year.',
            cta_label='Shop Auto data',
            cta_url='/buy',
            icon='🎉',
            is_active=True,
            starts_at=datetime.utcnow() - timedelta(days=7),
            ends_at=datetime.utcnow() + timedelta(days=45),
        ))

    # ── starter promo code
    if not db.query(PromoCode).filter_by(code='AUTO15').first():
        db.add(PromoCode(
            code='AUTO15',
            discount_pct=15,
            starts_at=datetime.utcnow() - timedelta(days=7),
            ends_at=datetime.utcnow() + timedelta(days=45),
            applies_to_vertical_id=None,
            is_active=True,
        ))

    db.commit()
