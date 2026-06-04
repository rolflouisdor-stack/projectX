"""Admin API (write side of the internal-only namespace).

All endpoints sit under `/api/internal/admin/` and require the same
`X-Internal-Api-Key` header that the read endpoints in `internal_routes.py`
use. Same hard rule: never expose this on a public host — gate by IP or VPN.

Three feature groups:
  - Pricing: pricing_tiers, verticals (base_rate / inventory), promo_codes
  - Banner: list/create/update/delete/activate promo_banners (the
    "push a notification that updates the banner" feature)
  - Reports: platform-wide rollups + paginated job/event lists across every
    mailer company, with optional company filter and date window.
"""
import logging
from datetime import datetime, timedelta
from collections import Counter, defaultdict
from flask import Blueprint, jsonify, request
from sqlalchemy import func, desc

from app.extensions import get_db
from app.api.internal_routes import require_internal_key, _parse_since
from app.models.mailer_company import MailerCompany
from app.models.mailer_user import MailerUser
from app.models.vertical import Vertical
from app.models.pricing_tier import PricingTier
from app.models.promo_code import PromoCode
from app.models.promo_banner import PromoBanner
from app.models.scrub_job import ScrubJob
from app.models.purchase_job import PurchaseJob
from app.models.activity_log import (
    ActivityLog,
    ACTION_PURCHASE, ACTION_DOWNLOAD, ACTION_RUN_SCRUB, ACTION_BUY_INIT,
    ACTION_APPLY_PROMO, ACTION_LOGIN, ACTION_SIGNUP,
)

logger = logging.getLogger(__name__)
admin_bp = Blueprint('admin', __name__, url_prefix='/api/internal/admin')


def _err(msg, status=400):
    return jsonify({'error': msg}), status


def _parse_dt(val):
    """Accept ISO-8601 or None; return naive UTC datetime or None."""
    if not val:
        return None
    try:
        return datetime.fromisoformat(str(val).replace('Z', '+00:00')).replace(tzinfo=None)
    except ValueError:
        return None


def _bounded(value, default, lo, hi):
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


# ─────────────────────────────────────────────────────────────────────────
# Pricing — tiers
# ─────────────────────────────────────────────────────────────────────────

@admin_bp.route('/pricing/tiers', methods=['GET'])
@require_internal_key
def list_pricing_tiers():
    db = get_db()
    rows = (db.query(PricingTier)
            .order_by(PricingTier.sort_order, PricingTier.min_volume)
            .all())
    return jsonify([r.to_dict() for r in rows])


@admin_bp.route('/pricing/tiers', methods=['POST'])
@require_internal_key
def create_pricing_tier():
    data = request.get_json(silent=True) or {}
    try:
        min_volume = int(data['min_volume'])
    except (KeyError, TypeError, ValueError):
        return _err('min_volume (int) is required')
    discount_pct = float(data.get('discount_pct') or 0)
    if discount_pct < 0 or discount_pct > 100:
        return _err('discount_pct must be 0–100')

    db = get_db()
    tier = PricingTier(
        min_volume=min_volume,
        discount_pct=discount_pct,
        is_active=bool(data.get('is_active', True)),
        sort_order=int(data.get('sort_order') or min_volume),
    )
    db.add(tier)
    db.commit()
    return jsonify(tier.to_dict()), 201


@admin_bp.route('/pricing/tiers/<int:tier_id>', methods=['PUT'])
@require_internal_key
def update_pricing_tier(tier_id):
    data = request.get_json(silent=True) or {}
    db = get_db()
    tier = db.query(PricingTier).filter_by(id=tier_id).first()
    if not tier:
        return _err('tier not found', 404)

    if 'min_volume' in data:
        try:
            tier.min_volume = int(data['min_volume'])
        except (TypeError, ValueError):
            return _err('min_volume must be int')
    if 'discount_pct' in data:
        v = float(data['discount_pct'] or 0)
        if v < 0 or v > 100:
            return _err('discount_pct must be 0–100')
        tier.discount_pct = v
    if 'is_active' in data:
        tier.is_active = bool(data['is_active'])
    if 'sort_order' in data:
        tier.sort_order = int(data['sort_order'] or 0)
    db.commit()
    return jsonify(tier.to_dict())


@admin_bp.route('/pricing/tiers/<int:tier_id>', methods=['DELETE'])
@require_internal_key
def delete_pricing_tier(tier_id):
    db = get_db()
    tier = db.query(PricingTier).filter_by(id=tier_id).first()
    if not tier:
        return _err('tier not found', 404)
    db.delete(tier)
    db.commit()
    return jsonify({'ok': True})


# ─────────────────────────────────────────────────────────────────────────
# Pricing — verticals (base_rate per vertical + inventory snapshot)
# ─────────────────────────────────────────────────────────────────────────

@admin_bp.route('/verticals', methods=['GET'])
@require_internal_key
def list_verticals_admin():
    db = get_db()
    rows = (db.query(Vertical)
            .order_by(Vertical.sort_order, Vertical.id)
            .all())
    return jsonify([r.to_dict() for r in rows])


@admin_bp.route('/verticals', methods=['POST'])
@require_internal_key
def create_vertical():
    data = request.get_json(silent=True) or {}
    slug = (data.get('slug') or '').strip().lower()
    display_name = (data.get('display_name') or '').strip()
    if not slug or not display_name:
        return _err('slug and display_name are required')
    db = get_db()
    if db.query(Vertical).filter_by(slug=slug).first():
        return _err('slug already exists', 409)
    v = Vertical(
        slug=slug,
        display_name=display_name,
        icon=data.get('icon'),
        description=data.get('description'),
        base_rate=float(data.get('base_rate') or 0.018),
        available_records=int(data.get('available_records') or 0),
        is_active=bool(data.get('is_active', True)),
        sort_order=int(data.get('sort_order') or 0),
    )
    db.add(v)
    db.commit()
    return jsonify(v.to_dict()), 201


@admin_bp.route('/verticals/<int:vid>', methods=['PUT'])
@require_internal_key
def update_vertical(vid):
    data = request.get_json(silent=True) or {}
    db = get_db()
    v = db.query(Vertical).filter_by(id=vid).first()
    if not v:
        return _err('vertical not found', 404)

    if 'display_name' in data:
        name = (data['display_name'] or '').strip()
        if not name:
            return _err('display_name cannot be empty')
        v.display_name = name
    if 'icon' in data:
        v.icon = data['icon']
    if 'description' in data:
        v.description = data['description']
    if 'base_rate' in data:
        try:
            br = float(data['base_rate'])
        except (TypeError, ValueError):
            return _err('base_rate must be numeric')
        if br < 0:
            return _err('base_rate cannot be negative')
        v.base_rate = br
    if 'available_records' in data:
        try:
            v.available_records = int(data['available_records'])
        except (TypeError, ValueError):
            return _err('available_records must be int')
    if 'is_active' in data:
        v.is_active = bool(data['is_active'])
    if 'sort_order' in data:
        v.sort_order = int(data['sort_order'] or 0)
    db.commit()
    return jsonify(v.to_dict())


@admin_bp.route('/verticals/<int:vid>', methods=['DELETE'])
@require_internal_key
def delete_vertical(vid):
    """Soft-delete by default (is_active=false). Pass ?hard=1 to remove —
    will fail if any purchase_jobs reference this vertical."""
    db = get_db()
    v = db.query(Vertical).filter_by(id=vid).first()
    if not v:
        return _err('vertical not found', 404)
    if request.args.get('hard') in ('1', 'true', 'yes'):
        db.delete(v)
        db.commit()
        return jsonify({'ok': True, 'hard_deleted': True})
    v.is_active = False
    db.commit()
    return jsonify({'ok': True, 'soft_deleted': True, 'id': v.id})


# ─────────────────────────────────────────────────────────────────────────
# Pricing — promo codes
# ─────────────────────────────────────────────────────────────────────────

@admin_bp.route('/promo-codes', methods=['GET'])
@require_internal_key
def list_promo_codes():
    db = get_db()
    rows = db.query(PromoCode).order_by(PromoCode.created_at.desc()).all()
    return jsonify([r.to_dict() for r in rows])


@admin_bp.route('/promo-codes', methods=['POST'])
@require_internal_key
def create_promo_code():
    data = request.get_json(silent=True) or {}
    code = (data.get('code') or '').strip().upper()
    if not code:
        return _err('code is required')
    db = get_db()
    if db.query(PromoCode).filter_by(code=code).first():
        return _err('code already exists', 409)

    discount_pct = float(data.get('discount_pct') or 0)
    discount_cents = int(data.get('discount_cents') or 0)
    if discount_pct == 0 and discount_cents == 0:
        return _err('Provide discount_pct (>0) or discount_cents (>0)')
    if discount_pct < 0 or discount_pct > 100:
        return _err('discount_pct must be 0–100')

    promo = PromoCode(
        code=code,
        discount_pct=discount_pct,
        discount_cents=discount_cents,
        starts_at=_parse_dt(data.get('starts_at')),
        ends_at=_parse_dt(data.get('ends_at')),
        max_redemptions=data.get('max_redemptions'),
        applies_to_vertical_id=data.get('applies_to_vertical_id'),
        is_active=bool(data.get('is_active', True)),
    )
    db.add(promo)
    db.commit()
    return jsonify(promo.to_dict()), 201


@admin_bp.route('/promo-codes/<int:pid>', methods=['PUT'])
@require_internal_key
def update_promo_code(pid):
    data = request.get_json(silent=True) or {}
    db = get_db()
    promo = db.query(PromoCode).filter_by(id=pid).first()
    if not promo:
        return _err('promo not found', 404)

    if 'discount_pct' in data:
        v = float(data['discount_pct'] or 0)
        if v < 0 or v > 100:
            return _err('discount_pct must be 0–100')
        promo.discount_pct = v
    if 'discount_cents' in data:
        promo.discount_cents = int(data['discount_cents'] or 0)
    if 'starts_at' in data:
        promo.starts_at = _parse_dt(data['starts_at'])
    if 'ends_at' in data:
        promo.ends_at = _parse_dt(data['ends_at'])
    if 'max_redemptions' in data:
        promo.max_redemptions = data['max_redemptions']
    if 'applies_to_vertical_id' in data:
        promo.applies_to_vertical_id = data['applies_to_vertical_id']
    if 'is_active' in data:
        promo.is_active = bool(data['is_active'])
    db.commit()
    return jsonify(promo.to_dict())


@admin_bp.route('/promo-codes/<int:pid>', methods=['DELETE'])
@require_internal_key
def delete_promo_code(pid):
    db = get_db()
    promo = db.query(PromoCode).filter_by(id=pid).first()
    if not promo:
        return _err('promo not found', 404)
    db.delete(promo)
    db.commit()
    return jsonify({'ok': True})


# ─────────────────────────────────────────────────────────────────────────
# Promo banner — admin push for the dashboard hero
# ─────────────────────────────────────────────────────────────────────────

@admin_bp.route('/banners', methods=['GET'])
@require_internal_key
def list_banners():
    db = get_db()
    rows = db.query(PromoBanner).order_by(PromoBanner.id.desc()).all()
    return jsonify([r.to_dict() for r in rows])


@admin_bp.route('/banners', methods=['POST'])
@require_internal_key
def create_banner():
    data = request.get_json(silent=True) or {}
    title = (data.get('title') or '').strip()
    if not title:
        return _err('title is required')

    db = get_db()
    # If activate=true (default), deactivate all current banners first so
    # only one shows at a time — that matches the dashboard's contract.
    activate = bool(data.get('activate', True))
    if activate:
        for b in db.query(PromoBanner).filter_by(is_active=True).all():
            b.is_active = False

    banner = PromoBanner(
        tag=data.get('tag'),
        title=title,
        body=data.get('body'),
        cta_label=data.get('cta_label'),
        cta_url=data.get('cta_url'),
        icon=data.get('icon'),
        is_active=activate,
        starts_at=_parse_dt(data.get('starts_at')),
        ends_at=_parse_dt(data.get('ends_at')),
    )
    db.add(banner)
    db.commit()
    return jsonify(banner.to_dict()), 201


@admin_bp.route('/banners/<int:bid>', methods=['PUT'])
@require_internal_key
def update_banner(bid):
    data = request.get_json(silent=True) or {}
    db = get_db()
    banner = db.query(PromoBanner).filter_by(id=bid).first()
    if not banner:
        return _err('banner not found', 404)

    for k in ('tag', 'title', 'body', 'cta_label', 'cta_url', 'icon'):
        if k in data:
            setattr(banner, k, data[k])
    if 'is_active' in data:
        wants_active = bool(data['is_active'])
        if wants_active and not banner.is_active:
            for b in db.query(PromoBanner).filter_by(is_active=True).all():
                b.is_active = False
        banner.is_active = wants_active
    if 'starts_at' in data:
        banner.starts_at = _parse_dt(data['starts_at'])
    if 'ends_at' in data:
        banner.ends_at = _parse_dt(data['ends_at'])
    db.commit()
    return jsonify(banner.to_dict())


@admin_bp.route('/banners/<int:bid>', methods=['DELETE'])
@require_internal_key
def delete_banner(bid):
    db = get_db()
    banner = db.query(PromoBanner).filter_by(id=bid).first()
    if not banner:
        return _err('banner not found', 404)
    db.delete(banner)
    db.commit()
    return jsonify({'ok': True})


@admin_bp.route('/banners/<int:bid>/activate', methods=['POST'])
@require_internal_key
def activate_banner(bid):
    """Atomic: deactivate every other banner, activate this one. Use this to
    "push a notification that updates the dashboard banner" — mailers pulling
    /api/promo-banner will see the new content on their next dashboard view."""
    db = get_db()
    target = db.query(PromoBanner).filter_by(id=bid).first()
    if not target:
        return _err('banner not found', 404)
    for b in db.query(PromoBanner).filter(PromoBanner.id != bid,
                                          PromoBanner.is_active == True).all():
        b.is_active = False
    target.is_active = True
    db.commit()
    return jsonify(target.to_dict())


# ─────────────────────────────────────────────────────────────────────────
# Reports — platform-wide activity (purchases, scrubs, downloads, summary)
# ─────────────────────────────────────────────────────────────────────────

@admin_bp.route('/reports/summary', methods=['GET'])
@require_internal_key
def report_summary():
    """Top-line counts for the window. Defaults to last 30 days unless
    ?days=N or ?since=ISO is supplied (see _parse_since)."""
    db = get_db()
    since = _parse_since()

    total_companies = db.query(func.count(MailerCompany.id)).scalar() or 0
    new_companies = (db.query(func.count(MailerCompany.id))
                     .filter(MailerCompany.created_at >= since).scalar() or 0)
    total_users = db.query(func.count(MailerUser.id)).scalar() or 0

    purchases = (db.query(PurchaseJob)
                 .filter(PurchaseJob.status.in_(('paid', 'complete')),
                         PurchaseJob.paid_at >= since).all())
    scrubs = (db.query(ScrubJob)
              .filter(ScrubJob.status.in_(('paid', 'complete')),
                      ScrubJob.paid_at >= since).all())

    purchase_revenue = sum(int(p.price_cents or 0) for p in purchases)
    purchase_volume = sum(int(p.volume or 0) for p in purchases)
    scrub_revenue = sum(int(s.price_cents or 0) for s in scrubs)
    scrub_records = sum(int(s.uploaded_count or 0) for s in scrubs)
    scrub_unique = sum(int(s.unique_count or 0) for s in scrubs)

    activity_count = (db.query(func.count(ActivityLog.id))
                      .filter(ActivityLog.created_at >= since).scalar() or 0)
    download_count = (db.query(func.count(ActivityLog.id))
                      .filter(ActivityLog.action == ACTION_DOWNLOAD,
                              ActivityLog.created_at >= since).scalar() or 0)
    signup_count = (db.query(func.count(ActivityLog.id))
                    .filter(ActivityLog.action == ACTION_SIGNUP,
                            ActivityLog.created_at >= since).scalar() or 0)
    login_count = (db.query(func.count(ActivityLog.id))
                   .filter(ActivityLog.action == ACTION_LOGIN,
                           ActivityLog.created_at >= since).scalar() or 0)
    promo_apply_count = (db.query(func.count(ActivityLog.id))
                         .filter(ActivityLog.action == ACTION_APPLY_PROMO,
                                 ActivityLog.created_at >= since).scalar() or 0)

    return jsonify({
        'window': {
            'since': since.isoformat(),
            'generated_at': datetime.utcnow().isoformat(),
        },
        'companies': {
            'total': int(total_companies),
            'new_in_window': int(new_companies),
            'total_users': int(total_users),
        },
        'purchases': {
            'count': len(purchases),
            'revenue_cents': purchase_revenue,
            'revenue_dollars': round(purchase_revenue / 100, 2),
            'records_sold': purchase_volume,
        },
        'scrubs': {
            'count': len(scrubs),
            'revenue_cents': scrub_revenue,
            'revenue_dollars': round(scrub_revenue / 100, 2),
            'records_uploaded': scrub_records,
            'unique_records_found': scrub_unique,
        },
        'activity_totals': {
            'all_events': int(activity_count),
            'signups': int(signup_count),
            'logins': int(login_count),
            'downloads': int(download_count),
            'promo_applies': int(promo_apply_count),
        },
        'combined_revenue_cents': purchase_revenue + scrub_revenue,
        'combined_revenue_dollars': round((purchase_revenue + scrub_revenue) / 100, 2),
    })


@admin_bp.route('/reports/daily-sales', methods=['GET'])
@require_internal_key
def report_daily_sales():
    """Per-day sales — purchase + scrub revenue bucketed by UTC date.

    Query params:
      days=N         Window length, default 30 (works with ?since=... too)
      company_id / company_name   Optional scope to one mailer
      fill=true|false  Default true. Insert zero rows for days inside the
                       window with no sales — keeps charts contiguous.

    Returns days newest-first.
    """
    db = get_db()
    since = _parse_since()
    today = datetime.utcnow().date()
    since_date = since.date()
    fill = request.args.get('fill', 'true').lower() not in ('false', '0', 'no')

    cid = _opt_company_filter()
    if cid == -1:
        return jsonify({'window': {'since': since.isoformat()}, 'days': [], 'totals': {}})

    purchases_q = (db.query(PurchaseJob)
                   .filter(PurchaseJob.status.in_(('paid', 'complete')),
                           PurchaseJob.paid_at >= since))
    scrubs_q = (db.query(ScrubJob)
                .filter(ScrubJob.status.in_(('paid', 'complete')),
                        ScrubJob.paid_at >= since))
    if cid:
        purchases_q = purchases_q.filter(PurchaseJob.company_id == cid)
        scrubs_q = scrubs_q.filter(ScrubJob.company_id == cid)

    by_day = defaultdict(lambda: {
        'purchase_count': 0, 'purchase_revenue_cents': 0, 'purchase_records': 0,
        'scrub_count': 0, 'scrub_revenue_cents': 0,
        'scrub_records_uploaded': 0, 'scrub_unique_records': 0,
    })

    for p in purchases_q.all():
        if not p.paid_at:
            continue
        d = p.paid_at.date()
        b = by_day[d]
        b['purchase_count'] += 1
        b['purchase_revenue_cents'] += int(p.price_cents or 0)
        b['purchase_records'] += int(p.volume or 0)

    for s in scrubs_q.all():
        if not s.paid_at:
            continue
        d = s.paid_at.date()
        b = by_day[d]
        b['scrub_count'] += 1
        b['scrub_revenue_cents'] += int(s.price_cents or 0)
        b['scrub_records_uploaded'] += int(s.uploaded_count or 0)
        b['scrub_unique_records'] += int(s.unique_count or 0)

    # Build the date axis. With fill=true, walk every day in [since_date, today]
    # so charts don't have gaps. Without it, only emit days that had sales.
    if fill:
        n_days = (today - since_date).days + 1
        all_days = [today - timedelta(days=i) for i in range(n_days)]
    else:
        all_days = sorted(by_day.keys(), reverse=True)

    rows = []
    total_p_rev = total_s_rev = total_p_count = total_s_count = 0
    total_records_sold = total_records_scrubbed = total_unique = 0
    days_with_sales = 0
    for d in all_days:
        b = by_day.get(d) or by_day[d]   # default-dict trick gives zeros
        combined = b['purchase_revenue_cents'] + b['scrub_revenue_cents']
        if b['purchase_count'] or b['scrub_count']:
            days_with_sales += 1
        rows.append({
            'date': d.isoformat(),
            'purchase_count': b['purchase_count'],
            'purchase_revenue_cents': b['purchase_revenue_cents'],
            'purchase_revenue_dollars': round(b['purchase_revenue_cents'] / 100, 2),
            'purchase_records': b['purchase_records'],
            'scrub_count': b['scrub_count'],
            'scrub_revenue_cents': b['scrub_revenue_cents'],
            'scrub_revenue_dollars': round(b['scrub_revenue_cents'] / 100, 2),
            'scrub_records_uploaded': b['scrub_records_uploaded'],
            'scrub_unique_records': b['scrub_unique_records'],
            'combined_revenue_cents': combined,
            'combined_revenue_dollars': round(combined / 100, 2),
        })
        total_p_rev += b['purchase_revenue_cents']
        total_s_rev += b['scrub_revenue_cents']
        total_p_count += b['purchase_count']
        total_s_count += b['scrub_count']
        total_records_sold += b['purchase_records']
        total_records_scrubbed += b['scrub_records_uploaded']
        total_unique += b['scrub_unique_records']

    combined_total = total_p_rev + total_s_rev
    avg_per_day = round(combined_total / 100 / len(rows), 2) if rows else 0.0
    avg_per_active_day = round(combined_total / 100 / days_with_sales, 2) if days_with_sales else 0.0

    return jsonify({
        'window': {
            'since': since.isoformat(),
            'generated_at': datetime.utcnow().isoformat(),
            'days_in_window': len(rows),
            'fill': fill,
        },
        'filter': {'company_id': cid},
        'days': rows,
        'totals': {
            'purchase_count': total_p_count,
            'purchase_revenue_cents': total_p_rev,
            'purchase_revenue_dollars': round(total_p_rev / 100, 2),
            'records_sold': total_records_sold,
            'scrub_count': total_s_count,
            'scrub_revenue_cents': total_s_rev,
            'scrub_revenue_dollars': round(total_s_rev / 100, 2),
            'records_scrubbed': total_records_scrubbed,
            'unique_records_found': total_unique,
            'combined_revenue_cents': combined_total,
            'combined_revenue_dollars': round(combined_total / 100, 2),
            'days_with_sales': days_with_sales,
            'avg_per_day_dollars': avg_per_day,
            'avg_per_active_day_dollars': avg_per_active_day,
        },
    })


def _company_lookup(db):
    return {c.id: c for c in db.query(MailerCompany).all()}


def _decorate_job(job, companies, kind):
    d = job.to_dict()
    c = companies.get(job.company_id)
    d['company_name'] = c.company_name if c else None
    d['kind'] = kind
    return d


def _opt_company_filter():
    cid = request.args.get('company_id')
    cname = request.args.get('company_name')
    db = get_db()
    if cid:
        try:
            return int(cid)
        except ValueError:
            return None
    if cname:
        c = db.query(MailerCompany).filter_by(company_name=cname).first()
        return c.id if c else -1   # -1 = "filter applied, no match"
    return None


@admin_bp.route('/reports/purchases', methods=['GET'])
@require_internal_key
def report_purchases():
    db = get_db()
    since = _parse_since()
    limit = _bounded(request.args.get('limit'), 100, 1, 500)
    offset = _bounded(request.args.get('offset'), 0, 0, 10**7)
    cid = _opt_company_filter()
    if cid == -1:
        return jsonify({'count': 0, 'rows': []})

    q = (db.query(PurchaseJob)
         .filter(PurchaseJob.created_at >= since))
    if cid:
        q = q.filter(PurchaseJob.company_id == cid)
    total = q.count()
    rows = (q.order_by(PurchaseJob.created_at.desc())
            .offset(offset).limit(limit).all())
    companies = _company_lookup(db)
    return jsonify({
        'window': {'since': since.isoformat()},
        'count': total,
        'returned': len(rows),
        'offset': offset, 'limit': limit,
        'rows': [_decorate_job(r, companies, 'purchase') for r in rows],
    })


@admin_bp.route('/reports/scrubs', methods=['GET'])
@require_internal_key
def report_scrubs():
    db = get_db()
    since = _parse_since()
    limit = _bounded(request.args.get('limit'), 100, 1, 500)
    offset = _bounded(request.args.get('offset'), 0, 0, 10**7)
    cid = _opt_company_filter()
    if cid == -1:
        return jsonify({'count': 0, 'rows': []})

    q = (db.query(ScrubJob)
         .filter(ScrubJob.created_at >= since))
    if cid:
        q = q.filter(ScrubJob.company_id == cid)
    total = q.count()
    rows = (q.order_by(ScrubJob.created_at.desc())
            .offset(offset).limit(limit).all())
    companies = _company_lookup(db)
    return jsonify({
        'window': {'since': since.isoformat()},
        'count': total,
        'returned': len(rows),
        'offset': offset, 'limit': limit,
        'rows': [_decorate_job(r, companies, 'scrub') for r in rows],
    })


@admin_bp.route('/reports/jobs', methods=['GET'])
@require_internal_key
def report_jobs():
    """Unified job history: every scrub + purchase across all companies,
    sorted by created_at desc. Useful for 'show me everything that happened.'"""
    db = get_db()
    since = _parse_since()
    cid = _opt_company_filter()
    if cid == -1:
        return jsonify({'count': 0, 'rows': []})
    limit = _bounded(request.args.get('limit'), 100, 1, 500)

    pq = db.query(PurchaseJob).filter(PurchaseJob.created_at >= since)
    sq = db.query(ScrubJob).filter(ScrubJob.created_at >= since)
    if cid:
        pq = pq.filter(PurchaseJob.company_id == cid)
        sq = sq.filter(ScrubJob.company_id == cid)
    p_rows = pq.order_by(PurchaseJob.created_at.desc()).limit(limit).all()
    s_rows = sq.order_by(ScrubJob.created_at.desc()).limit(limit).all()
    companies = _company_lookup(db)
    merged = ([_decorate_job(r, companies, 'purchase') for r in p_rows] +
              [_decorate_job(r, companies, 'scrub') for r in s_rows])
    merged.sort(key=lambda r: r.get('created_at') or '', reverse=True)
    merged = merged[:limit]
    return jsonify({
        'window': {'since': since.isoformat()},
        'count': len(merged),
        'rows': merged,
    })


@admin_bp.route('/reports/downloads', methods=['GET'])
@require_internal_key
def report_downloads():
    """Every download event (file actually fetched), decorated with company
    + originating job kind."""
    db = get_db()
    since = _parse_since()
    limit = _bounded(request.args.get('limit'), 100, 1, 500)
    cid = _opt_company_filter()
    if cid == -1:
        return jsonify({'count': 0, 'rows': []})

    q = (db.query(ActivityLog)
         .filter(ActivityLog.action == ACTION_DOWNLOAD,
                 ActivityLog.created_at >= since))
    if cid:
        q = q.filter(ActivityLog.company_id == cid)
    total = q.count()
    rows = (q.order_by(ActivityLog.created_at.desc()).limit(limit).all())
    companies = _company_lookup(db)
    out = []
    for r in rows:
        d = r.to_dict()
        c = companies.get(r.company_id)
        d['company_name'] = c.company_name if c else None
        out.append(d)
    return jsonify({
        'window': {'since': since.isoformat()},
        'count': total,
        'returned': len(rows),
        'rows': out,
    })


@admin_bp.route('/reports/activity', methods=['GET'])
@require_internal_key
def report_activity():
    """Raw activity_log filtered by action + company + window.
    Same shape as /activity/raw but works without a company filter."""
    db = get_db()
    since = _parse_since()
    limit = _bounded(request.args.get('limit'), 200, 1, 1000)
    action = request.args.get('action')
    cid = _opt_company_filter()
    if cid == -1:
        return jsonify({'count': 0, 'rows': []})

    q = db.query(ActivityLog).filter(ActivityLog.created_at >= since)
    if action:
        q = q.filter(ActivityLog.action == action)
    if cid:
        q = q.filter(ActivityLog.company_id == cid)
    total = q.count()
    rows = q.order_by(ActivityLog.created_at.desc()).limit(limit).all()
    companies = _company_lookup(db)
    out = []
    for r in rows:
        d = r.to_dict()
        c = companies.get(r.company_id)
        d['company_name'] = c.company_name if c else None
        out.append(d)
    return jsonify({
        'window': {'since': since.isoformat()},
        'filter': {'action': action, 'company_id': cid},
        'count': total,
        'returned': len(rows),
        'rows': out,
    })


# ─────────────────────────────────────────────────────────────────────────
# Overview — one-call "everything happening in the app" snapshot
# ─────────────────────────────────────────────────────────────────────────

@admin_bp.route('/overview', methods=['GET'])
@require_internal_key
def platform_overview():
    """Single cross-company snapshot for an admin: totals + revenue + the most
    recent jobs and activity across every mailer. Window via ?days=N (default
    30) or ?since=ISO. Returns enough to render an admin home screen in one call.
    """
    db = get_db()
    since = _parse_since()
    companies = _company_lookup(db)

    total_companies = db.query(func.count(MailerCompany.id)).scalar() or 0
    total_users = db.query(func.count(MailerUser.id)).scalar() or 0
    new_companies = (db.query(func.count(MailerCompany.id))
                     .filter(MailerCompany.created_at >= since).scalar() or 0)

    purchases = (db.query(PurchaseJob)
                 .filter(PurchaseJob.status.in_(('paid', 'complete')),
                         PurchaseJob.paid_at >= since).all())
    scrubs = (db.query(ScrubJob)
              .filter(ScrubJob.status.in_(('paid', 'complete')),
                      ScrubJob.paid_at >= since).all())
    purchase_rev = sum(int(p.price_cents or 0) for p in purchases)
    scrub_rev = sum(int(s.price_cents or 0) for s in scrubs)

    # Most recent activity across the whole platform, in the window.
    recent_jobs = ([_decorate_job(r, companies, 'purchase') for r in
                    db.query(PurchaseJob).filter(PurchaseJob.created_at >= since)
                      .order_by(PurchaseJob.created_at.desc()).limit(20).all()] +
                   [_decorate_job(r, companies, 'scrub') for r in
                    db.query(ScrubJob).filter(ScrubJob.created_at >= since)
                      .order_by(ScrubJob.created_at.desc()).limit(20).all()])
    recent_jobs.sort(key=lambda r: r.get('created_at') or '', reverse=True)
    recent_jobs = recent_jobs[:20]

    recent_activity = []
    for r in (db.query(ActivityLog).filter(ActivityLog.created_at >= since)
              .order_by(ActivityLog.created_at.desc()).limit(25).all()):
        d = r.to_dict()
        c = companies.get(r.company_id)
        d['company_name'] = c.company_name if c else None
        recent_activity.append(d)

    return jsonify({
        'window': {'since': since.isoformat(), 'generated_at': datetime.utcnow().isoformat()},
        'companies': {'total': int(total_companies), 'new_in_window': int(new_companies),
                      'total_users': int(total_users)},
        'jobs': {
            'purchases': {'count': len(purchases), 'revenue_cents': purchase_rev,
                          'records_sold': sum(int(p.volume or 0) for p in purchases)},
            'scrubs': {'count': len(scrubs), 'revenue_cents': scrub_rev,
                       'records_uploaded': sum(int(s.uploaded_count or 0) for s in scrubs),
                       'unique_found': sum(int(s.unique_count or 0) for s in scrubs)},
        },
        'revenue': {'purchase_cents': purchase_rev, 'scrub_cents': scrub_rev,
                    'combined_cents': purchase_rev + scrub_rev,
                    'combined_dollars': round((purchase_rev + scrub_rev) / 100, 2)},
        'recent_jobs': recent_jobs,
        'recent_activity': recent_activity,
    })
