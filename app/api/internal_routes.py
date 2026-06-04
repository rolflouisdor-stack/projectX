"""Internal cross-system API — read-only activity for CX3 Dashboard.

Authenticates via the static INTERNAL_API_KEY sent in the X-Internal-Api-Key
header (or ?api_key=...). NEVER expose this on the public mailer-facing
domain — gate it by host or VPN in production.
"""
import logging
from functools import wraps
from datetime import datetime, timedelta
from collections import Counter
from flask import Blueprint, jsonify, request, current_app, abort, g
from sqlalchemy import func

from app.extensions import get_db
from app.models.mailer_company import MailerCompany
from app.models.activity_log import (
    ActivityLog,
    ACTION_TRACK_VERTICAL, ACTION_VIEW_VERTICAL,
    ACTION_PURCHASE, ACTION_DOWNLOAD, ACTION_BUY_INIT,
)
from app.models.vertical import Vertical
from app.models.scrub_job import ScrubJob
from app.models.purchase_job import PurchaseJob

logger = logging.getLogger(__name__)
internal_bp = Blueprint('internal', __name__, url_prefix='/api/internal')


def require_internal_key(fn):
    """Authorize internal/admin endpoints via EITHER:
      1. the static X-Internal-Api-Key header (server-to-server, e.g. CX3 Dashboard), OR
      2. a logged-in platform-admin session (ADMIN_EMAILS) — lets an admin hit
         these cross-company endpoints straight from the browser.
    """
    @wraps(fn)
    def inner(*args, **kwargs):
        sent = request.headers.get('X-Internal-Api-Key') or request.args.get('api_key')
        expected = current_app.config.get('INTERNAL_API_KEY')
        if expected and sent and sent == expected:
            return fn(*args, **kwargs)

        # Fall back to an admin session (cookie-based).
        from app.auth.decorators import current_user_or_none, is_admin_user
        user, company = current_user_or_none()
        if is_admin_user(user):
            g.current_user = user
            g.current_company = company
            return fn(*args, **kwargs)

        if not expected:
            return jsonify({'error': 'internal API key not configured'}), 503
        return jsonify({'error': 'admin access required'}), 401
    return inner


def _parse_since():
    """?since=ISO8601 or ?days=N. Defaults to last 90 days."""
    since_iso = request.args.get('since')
    if since_iso:
        try:
            return datetime.fromisoformat(since_iso.replace('Z', '+00:00')).replace(tzinfo=None)
        except ValueError:
            pass
    days = int(request.args.get('days') or 90)
    return datetime.utcnow() - timedelta(days=max(1, days))


def _resolve_company():
    """Look up the company from ?company_name=... or ?company_id=..."""
    db = get_db()
    company_id = request.args.get('company_id')
    company_name = request.args.get('company_name')
    if company_id:
        return db.query(MailerCompany).filter_by(id=int(company_id)).first()
    if company_name:
        return db.query(MailerCompany).filter_by(company_name=company_name).first()
    return None


# ── Endpoints ─────────────────────────────────────────────────────────────

@internal_bp.route('/companies', methods=['GET'])
@require_internal_key
def list_companies():
    """Lookup table for CX3 Dashboard — see who's registered."""
    db = get_db()
    rows = db.query(MailerCompany).order_by(MailerCompany.created_at.desc()).all()
    return jsonify([c.to_dict() for c in rows])


@internal_bp.route('/activity', methods=['GET'])
@require_internal_key
def company_activity():
    """The headline endpoint — what's this company doing on the platform?

    Returns:
      {
        company: {...},
        window: {since, days, generated_at},
        summary: {
          total_events, login_count, dashboard_views, ...
        },
        purchases: {
          count, total_spent_cents, total_records, by_vertical: [{vertical, count, records, spend_cents}]
        },
        scrubs: { count, total_records, total_unique, total_spent_cents },
        downloads: { count, by_vertical: [{...}] },
        tracking: {                 # which verticals they look at / select most
          by_vertical: [{vertical_id, vertical_slug, vertical_name, view_count, track_count}]
        },
        recent_actions: [last 25 activity_log rows, decorated with vertical names]
      }
    """
    company = _resolve_company()
    if not company:
        return jsonify({'error': 'company_id or company_name required, and must exist'}), 404

    db = get_db()
    since = _parse_since()
    verts = {v.id: v for v in db.query(Vertical).all()}
    cid = company.id

    base_q = db.query(ActivityLog).filter(
        ActivityLog.company_id == cid,
        ActivityLog.created_at >= since,
    )
    rows = base_q.order_by(ActivityLog.created_at.desc()).all()

    # ── Summary counts by action
    action_counts = Counter(r.action for r in rows)

    # ── Purchases (joined to purchase_jobs for the dollar totals)
    purchases_q = (db.query(PurchaseJob)
                   .filter(PurchaseJob.company_id == cid,
                           PurchaseJob.status.in_(('paid', 'complete')),
                           PurchaseJob.paid_at >= since))
    purchase_jobs = purchases_q.all()

    by_vert_purchase = {}
    total_purchase_records = 0
    total_purchase_cents = 0
    for pj in purchase_jobs:
        total_purchase_cents += int(pj.price_cents or 0)
        total_purchase_records += int(pj.volume or 0)
        for sel in (pj.selected_verticals_json or []):
            vid = sel.get('vertical_id')
            if not vid:
                continue
            entry = by_vert_purchase.setdefault(vid, {
                'vertical_id': vid,
                'vertical_slug': verts[vid].slug if vid in verts else None,
                'vertical_name': verts[vid].display_name if vid in verts else None,
                'count': 0, 'records': 0, 'spend_cents': 0,
            })
            entry['count'] += 1
            entry['records'] += int(sel.get('records') or 0)
            entry['spend_cents'] += int(sel.get('line_cents') or 0)

    # ── Scrubs
    scrubs_q = (db.query(ScrubJob)
                .filter(ScrubJob.company_id == cid,
                        ScrubJob.status.in_(('paid', 'complete')),
                        ScrubJob.paid_at >= since))
    scrub_jobs = scrubs_q.all()
    scrub_total_records = sum(int(s.uploaded_count or 0) for s in scrub_jobs)
    scrub_total_unique = sum(int(s.unique_count or 0) for s in scrub_jobs)
    scrub_total_cents = sum(int(s.price_cents or 0) for s in scrub_jobs)

    # ── Downloads breakdown
    download_rows = [r for r in rows if r.action == ACTION_DOWNLOAD]
    by_vert_download = {}
    for r in download_rows:
        # Look up vertical via the linked job
        vid_list = []
        meta = r.meta or {}
        if r.purchase_job_id:
            pj = next((p for p in purchase_jobs if p.id == r.purchase_job_id), None)
            if pj:
                vid_list = [sel.get('vertical_id') for sel in (pj.selected_verticals_json or [])]
        for vid in vid_list:
            if not vid:
                continue
            entry = by_vert_download.setdefault(vid, {
                'vertical_id': vid,
                'vertical_slug': verts[vid].slug if vid in verts else None,
                'vertical_name': verts[vid].display_name if vid in verts else None,
                'download_count': 0,
            })
            entry['download_count'] += 1

    # ── Vertical tracking (views + track toggles)
    track_counter = Counter()
    view_counter = Counter()
    for r in rows:
        if r.vertical_id:
            if r.action == ACTION_TRACK_VERTICAL:
                track_counter[r.vertical_id] += 1
            elif r.action == ACTION_VIEW_VERTICAL:
                view_counter[r.vertical_id] += 1

    tracking = []
    for vid in set(list(track_counter.keys()) + list(view_counter.keys())):
        tracking.append({
            'vertical_id': vid,
            'vertical_slug': verts[vid].slug if vid in verts else None,
            'vertical_name': verts[vid].display_name if vid in verts else None,
            'view_count': view_counter[vid],
            'track_count': track_counter[vid],
        })
    tracking.sort(key=lambda x: (x['track_count'] + x['view_count']), reverse=True)

    # ── Recent actions (decorate vertical_id with names)
    recent = []
    for r in rows[:25]:
        d = r.to_dict()
        if d.get('vertical_id') and d['vertical_id'] in verts:
            d['vertical_name'] = verts[d['vertical_id']].display_name
        recent.append(d)

    return jsonify({
        'company': company.to_dict(),
        'window': {
            'since': since.isoformat(),
            'generated_at': datetime.utcnow().isoformat(),
        },
        'summary': {
            'total_events': len(rows),
            'login_count': action_counts.get('login', 0),
            'signup_count': action_counts.get('signup', 0),
            'dashboard_views': action_counts.get('view_dashboard', 0),
            'apply_promo_count': action_counts.get('apply_promo', 0),
        },
        'purchases': {
            'count': len(purchase_jobs),
            'total_records': total_purchase_records,
            'total_spent_cents': total_purchase_cents,
            'by_vertical': sorted(by_vert_purchase.values(),
                                  key=lambda x: x['spend_cents'], reverse=True),
        },
        'scrubs': {
            'count': len(scrub_jobs),
            'total_records': scrub_total_records,
            'total_unique': scrub_total_unique,
            'total_spent_cents': scrub_total_cents,
        },
        'downloads': {
            'count': len(download_rows),
            'by_vertical': sorted(by_vert_download.values(),
                                  key=lambda x: x['download_count'], reverse=True),
        },
        'tracking': {
            'by_vertical': tracking,
        },
        'recent_actions': recent,
    })


@internal_bp.route('/activity/raw', methods=['GET'])
@require_internal_key
def raw_activity():
    """Raw activity_log rows for a company in the window. Capped at 500."""
    company = _resolve_company()
    if not company:
        return jsonify({'error': 'company_id or company_name required'}), 404
    db = get_db()
    since = _parse_since()
    rows = (db.query(ActivityLog)
            .filter(ActivityLog.company_id == company.id,
                    ActivityLog.created_at >= since)
            .order_by(ActivityLog.created_at.desc())
            .limit(500).all())
    return jsonify({
        'company': company.to_dict(),
        'since': since.isoformat(),
        'count': len(rows),
        'rows': [r.to_dict() for r in rows],
    })


@internal_bp.route('/health', methods=['GET'])
def health():
    """Cheap liveness probe — no API key needed, just confirms the service is up."""
    return jsonify({'status': 'ok', 'service': 'gravitas-mailer'})
