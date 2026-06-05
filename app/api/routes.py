"""Mailer-facing API.

All endpoints assume an authenticated mailer (via the @mailer_login_required
decorator). They write activity_log rows for every meaningful action.
"""
import math
import logging
from datetime import datetime
from flask import Blueprint, jsonify, request, g, redirect, abort

from app.extensions import get_db
from app.auth.decorators import mailer_login_required
from app.models.vertical import Vertical
from app.models.subcategory import Subcategory
from app.models.scrub_job import ScrubJob
from app.models.scrub_job_field_mapping import ScrubJobFieldMapping, STANDARD_FIELDS
from app.models.purchase_job import PurchaseJob
from app.models.promo_banner import PromoBanner
from app.models.activity_log import (
    ACTION_TRACK_VERTICAL, ACTION_VIEW_DASHBOARD,
    ACTION_UPLOAD_LIST, ACTION_RUN_SCRUB, ACTION_BUY_INIT,
    ACTION_PURCHASE, ACTION_DOWNLOAD, ACTION_APPLY_PROMO, ACTION_DELETE_JOB,
)
from app.services.activity_logger import log_activity
from app.services.pricing import calc_purchase_quote
from app.services.xlsx_generator import generate_purchase_artifact
from app.services import stripe_service
from app.services.stripe_service import create_payment_intent
from app.services import storage
from app.services.header_detector import detect_from_s3
from app.jobs.queue import (
    enqueue_import, enqueue_generate_scrub_artifact,
    enqueue_eo_quote, enqueue_eo_submit,
)

logger = logging.getLogger(__name__)
api_bp = Blueprint('api', __name__, url_prefix='/api')


# ── Verticals + subcategories ─────────────────────────────────────────────

@api_bp.route('/verticals', methods=['GET'])
@mailer_login_required
def list_verticals():
    db = get_db()
    verticals = db.query(Vertical).filter_by(is_active=True).order_by(Vertical.sort_order).all()
    subs = db.query(Subcategory).filter_by(is_active=True).order_by(Subcategory.sort_order).all()
    by_vert = {}
    for s in subs:
        by_vert.setdefault(s.vertical_id, []).append(s.to_dict())

    out = []
    for v in verticals:
        d = v.to_dict()
        d['subcategories'] = by_vert.get(v.id, [])
        out.append(d)
    return jsonify(out)


@api_bp.route('/verticals/<int:vertical_id>/track', methods=['POST'])
@mailer_login_required
def track_vertical(vertical_id):
    """User toggled-on this vertical in the UI — note interest in activity log."""
    db = get_db()
    v = db.query(Vertical).filter_by(id=vertical_id).first()
    if not v:
        return jsonify({'error': 'unknown vertical'}), 404
    log_activity(
        g.current_company.id, ACTION_TRACK_VERTICAL,
        user_id=g.current_user.id, vertical_id=v.id,
        meta={'vertical_slug': v.slug, 'display_name': v.display_name},
    )
    return jsonify({'ok': True})


# ── Promo banner ──────────────────────────────────────────────────────────

@api_bp.route('/promo-banner', methods=['GET'])
@mailer_login_required
def current_banner():
    db = get_db()
    now = datetime.utcnow()
    q = db.query(PromoBanner).filter_by(is_active=True).order_by(PromoBanner.id.desc()).all()
    active = next(
        (b for b in q if (not b.starts_at or b.starts_at <= now)
                       and (not b.ends_at or b.ends_at >= now)),
        None,
    )
    return jsonify(active.to_dict() if active else None)


# ── Dashboard summary ─────────────────────────────────────────────────────

@api_bp.route('/dashboard/summary', methods=['GET'])
@mailer_login_required
def dashboard_summary():
    """Recent activity for the dashboard's right-hand card."""
    db = get_db()
    cid = g.current_company.id
    scrubs = (db.query(ScrubJob).filter_by(company_id=cid)
              .order_by(ScrubJob.created_at.desc()).limit(3).all())
    purchases = (db.query(PurchaseJob).filter_by(company_id=cid)
                 .order_by(PurchaseJob.created_at.desc()).limit(3).all())

    recent = []
    for s in scrubs:
        recent.append({
            'kind': 'scrub', 'date': s.created_at.isoformat() if s.created_at else None,
            'records': s.unique_count or 0, 'price_cents': s.price_cents or 0,
            'status': s.status,
        })
    for p in purchases:
        recent.append({
            'kind': 'purchase', 'date': p.created_at.isoformat() if p.created_at else None,
            'records': p.volume or 0, 'price_cents': p.price_cents or 0,
            'status': p.status,
        })
    recent.sort(key=lambda r: r['date'] or '', reverse=True)

    total_spent = sum((s.price_cents or 0) for s in scrubs) + sum((p.price_cents or 0) for p in purchases)
    log_activity(cid, ACTION_VIEW_DASHBOARD, user_id=g.current_user.id)
    return jsonify({
        'recent': recent[:6],
        'total_spent_cents': total_spent,
    })


# ── Scrub jobs: presigned upload + mapping + import ──────────────────────

@api_bp.route('/scrub-jobs/upload-init', methods=['POST'])
@mailer_login_required
def scrub_upload_init():
    """Create a draft job + multipart upload; return per-part presigned URLs.

    Browser flow:
      1. POST { filename, size, content_type, cleaning }
      2. Get { job_id, upload_id, s3_key, part_size, parts: [{PartNumber, url}] }
      3. PUT each part directly to Spaces (in parallel is fine)
      4. POST /scrub-jobs/{id}/upload-complete with collected ETags
    """
    data = request.get_json(silent=True) or {}
    filename = (data.get('filename') or '').strip()
    size = int(data.get('size') or 0)
    content_type = data.get('content_type') or 'application/octet-stream'
    cleaning = bool(data.get('cleaning', True))

    if not filename:
        return jsonify({'error': 'filename is required'}), 400
    if size <= 0:
        return jsonify({'error': 'size must be > 0'}), 400

    db = get_db()
    company = g.current_company
    user = g.current_user

    job = ScrubJob(
        company_id=company.id,
        user_id=user.id,
        status='uploading',
        cleaning_opted_in=cleaning,
        original_filename=filename,
        file_size_bytes=size,
        content_type=content_type,
    )
    db.add(job)
    db.flush()    # need job.id for the S3 key

    from flask import current_app
    s3_key = storage.upload_key(company.id, job.id, filename)
    upload_id = storage.initiate_multipart_upload(s3_key, content_type=content_type)
    job.s3_bucket = current_app.config.get('S3_BUCKET')
    job.s3_key = s3_key
    job.multipart_upload_id = upload_id
    db.commit()

    part_size = storage.part_size()
    num_parts = max(1, math.ceil(size / part_size))
    parts = [
        {'PartNumber': i + 1,
         'url': storage.presign_part_url(s3_key, upload_id, i + 1)}
        for i in range(num_parts)
    ]

    return jsonify({
        'job_id': job.id,
        'upload_id': upload_id,
        's3_key': s3_key,
        'part_size': part_size,
        'parts': parts,
    })


@api_bp.route('/scrub-jobs/<int:job_id>/upload-complete', methods=['POST'])
@mailer_login_required
def scrub_upload_complete(job_id):
    """Finalize the multipart upload after browser PUTs all parts."""
    data = request.get_json(silent=True) or {}
    parts = data.get('parts') or []
    if not parts:
        return jsonify({'error': 'parts is required'}), 400

    db = get_db()
    job = db.query(ScrubJob).filter_by(id=job_id, company_id=g.current_company.id).first()
    if not job:
        return jsonify({'error': 'not found'}), 404
    if job.status != 'uploading' or not job.multipart_upload_id:
        return jsonify({'error': f'job is in status {job.status}'}), 409

    try:
        storage.complete_multipart_upload(job.s3_key, job.multipart_upload_id, parts)
    except Exception as e:
        logger.exception("complete_multipart_upload failed for job %s", job.id)
        storage.abort_multipart_upload(job.s3_key, job.multipart_upload_id)
        job.status = 'failed'
        job.failure_reason = f'upload finalize failed: {e}'[:500]
        db.commit()
        return jsonify({'error': 'upload finalize failed'}), 500

    job.status = 'awaiting_mapping'
    db.commit()

    log_activity(
        g.current_company.id, ACTION_UPLOAD_LIST,
        user_id=g.current_user.id, scrub_job_id=job.id,
        meta={'filename': job.original_filename, 'size_bytes': int(job.file_size_bytes or 0),
              'cleaning': bool(job.cleaning_opted_in), 's3_key': job.s3_key},
    )
    return jsonify(job.to_dict())


@api_bp.route('/scrub-jobs/<int:job_id>/detect-headers', methods=['POST'])
@mailer_login_required
def scrub_detect_headers(job_id):
    """Peek at the uploaded file's first ~5 rows + return suggested mapping."""
    db = get_db()
    job = db.query(ScrubJob).filter_by(id=job_id, company_id=g.current_company.id).first()
    if not job:
        return jsonify({'error': 'not found'}), 404
    if job.status != 'awaiting_mapping':
        return jsonify({'error': f'job is in status {job.status}'}), 409

    try:
        result = detect_from_s3(job.s3_key, filename=job.original_filename)
    except Exception as e:
        logger.exception("detect_headers failed for job %s", job.id)
        return jsonify({'error': f'could not parse file: {e}'}), 422

    job.detected_headers_json = result['headers']
    job.delimiter = result.get('delimiter')
    db.commit()

    return jsonify({
        'job_id': job.id,
        'format': result['format'],
        'delimiter': result.get('delimiter'),
        'headers': result['headers'],
        'sample_rows': result['sample_rows'],
        'suggested_mapping': result['suggested_mapping'],
        'standard_fields': list(STANDARD_FIELDS),
    })


@api_bp.route('/scrub-jobs/<int:job_id>/confirm-email', methods=['POST'])
@mailer_login_required
def scrub_confirm_email(job_id):
    """EmailOversight flow: the user confirms which column holds the email; we
    count the records + price the clean job (async) and move to `priced`."""
    data = request.get_json(silent=True) or {}
    try:
        idx = int(data.get('email_column_index'))
    except (TypeError, ValueError):
        return jsonify({'error': 'email_column_index is required'}), 400
    db = get_db()
    job = db.query(ScrubJob).filter_by(id=job_id, company_id=g.current_company.id).first()
    if not job:
        return jsonify({'error': 'not found'}), 404
    if job.status != 'awaiting_mapping':
        return jsonify({'error': f'job is in status {job.status}'}), 409
    headers = job.detected_headers_json or []
    if idx < 0 or idx >= len(headers):
        return jsonify({'error': 'email_column_index out of range'}), 400

    job.email_column_index = idx
    job.status = 'importing'    # counting + pricing the file off the request path
    db.commit()
    log_activity(
        g.current_company.id, ACTION_UPLOAD_LIST,
        user_id=g.current_user.id, scrub_job_id=job.id,
        meta={'email_column_index': idx, 'kind': 'scrub-clean'},
    )
    try:
        enqueue_eo_quote(job.id)
    except Exception as e:
        logger.exception("could not enqueue EO quote for job %s", job.id)
        job.status = 'failed'
        job.failure_reason = f'could not start pricing: {e}'[:500]
        db.commit()
        return jsonify({'error': 'could not start pricing'}), 503
    return jsonify(job.to_dict())


@api_bp.route('/scrub-jobs/<int:job_id>/mapping', methods=['POST'])
@mailer_login_required
def scrub_save_mapping(job_id):
    """Persist the user's column mapping + enqueue the import worker."""
    data = request.get_json(silent=True) or {}
    mappings = data.get('mappings') or []
    if not isinstance(mappings, list) or not mappings:
        return jsonify({'error': 'mappings is required'}), 400

    db = get_db()
    job = db.query(ScrubJob).filter_by(id=job_id, company_id=g.current_company.id).first()
    if not job:
        return jsonify({'error': 'not found'}), 404
    if job.status != 'awaiting_mapping':
        return jsonify({'error': f'job is in status {job.status}'}), 409

    # Wipe any previous mapping rows for this job (user re-submitted).
    db.query(ScrubJobFieldMapping).filter_by(scrub_job_id=job.id).delete()

    seen_standard = set()
    for m in mappings:
        source = (m.get('source_header') or '').strip()
        if not source:
            return jsonify({'error': 'each mapping needs a source_header'}), 400
        try:
            col_idx = int(m.get('column_index'))
        except (TypeError, ValueError):
            return jsonify({'error': f'invalid column_index for {source}'}), 400
        target = (m.get('target_field') or '').strip()
        is_standard = bool(m.get('is_standard'))
        skip = bool(m.get('skip'))
        if not skip:
            if not target:
                return jsonify({'error': f'target_field required for {source}'}), 400
            if is_standard and target not in STANDARD_FIELDS:
                return jsonify({'error': f'unknown standard field: {target}'}), 400
            if is_standard:
                if target in seen_standard:
                    return jsonify({'error': f'standard field "{target}" mapped twice'}), 400
                seen_standard.add(target)
        db.add(ScrubJobFieldMapping(
            scrub_job_id=job.id,
            source_header=source,
            column_index=col_idx,
            target_field=target or source,
            is_standard=is_standard,
            skip=skip,
        ))

    if 'email' not in seen_standard:
        return jsonify({
            'error': 'At least one column must be mapped to the standard "Email" field. '
                     'This is an email-scrub service — without an email column there is '
                     'nothing to scrub.',
            'code': 'missing_email_mapping',
        }), 400

    job.status = 'importing'
    db.commit()

    try:
        rq_job = enqueue_import(job.id)
        logger.info("Enqueued import for scrub_job %s as rq_job %s", job.id, rq_job.id)
    except Exception as e:
        logger.exception("enqueue_import failed for job %s", job.id)
        job.status = 'failed'
        job.failure_reason = f'queue failure: {e}'[:500]
        db.commit()
        return jsonify({'error': 'could not enqueue import'}), 500

    return jsonify(job.to_dict())


@api_bp.route('/scrub-jobs/<int:job_id>', methods=['GET'])
@mailer_login_required
def get_scrub_job(job_id):
    db = get_db()
    job = db.query(ScrubJob).filter_by(id=job_id, company_id=g.current_company.id).first()
    if not job:
        return jsonify({'error': 'not found'}), 404
    return jsonify(job.to_dict())


@api_bp.route('/scrub-jobs/<int:job_id>', methods=['DELETE'])
@mailer_login_required
def delete_scrub_job(job_id):
    """Permanently delete a scrub job: its Spaces objects (upload + result) AND
    the DB row (child rows cascade). Scoped to the caller's company, so a user
    can only delete their own jobs. Spaces cleanup is best-effort — a storage
    hiccup must not block removing the row (else it can never be retried)."""
    db = get_db()
    company = g.current_company
    job = db.query(ScrubJob).filter_by(id=job_id, company_id=company.id).first()
    if not job:
        return jsonify({'error': 'not found'}), 404

    # Wipe the whole per-job object tree (upload + result + any multipart
    # fragments), not just the two known keys.
    spaces_deleted = 0
    for prefix in (f'uploads/{company.id}/{job.id}/', f'results/{company.id}/{job.id}/'):
        try:
            spaces_deleted += storage.delete_prefix(prefix)
        except Exception:
            logger.exception("delete_scrub_job: Spaces cleanup failed for %s", prefix)

    fname = job.original_filename
    db.delete(job)          # FK ON DELETE CASCADE clears child rows
    db.commit()

    log_activity(
        company.id, ACTION_DELETE_JOB,
        user_id=g.current_user.id, scrub_job_id=job_id,
        meta={'filename': fname, 'spaces_objects_deleted': spaces_deleted},
    )
    logger.info("deleted scrub_job %s (company %s), %s Spaces object(s)",
                job_id, company.id, spaces_deleted)
    return jsonify({'deleted': True, 'id': job_id, 'spaces_objects_deleted': spaces_deleted})


def _advance_paid_scrub_job(db, job):
    """Move a PAID scrub job forward — EO submit (EO flow) or artifact build
    (legacy). Idempotent: a no-op if already advanced, so the sync stub path and
    the Stripe webhook can both call it safely. Uses job.* (no request context)."""
    from flask import current_app
    if job.status in ('submitting_ftp', 'awaiting_ftp_result', 'generating', 'complete'):
        return
    if not job.paid_at:
        job.paid_at = datetime.utcnow()

    if current_app.config.get('EO_FTP_ENABLED'):
        job.status = 'submitting_ftp'
        db.commit()
        log_activity(job.company_id, ACTION_PURCHASE, user_id=job.user_id, scrub_job_id=job.id,
                     meta={'price_cents': job.price_cents, 'records': job.uploaded_count,
                           'stripe_intent': job.stripe_payment_intent_id, 'kind': 'scrub-clean'})
        try:
            enqueue_eo_submit(job.id)
        except Exception as e:
            logger.exception("could not enqueue EO submit for job %s", job.id)
            job.status = 'failed'
            job.failure_reason = f'could not start cleaning: {e}'[:500]
            db.commit()
    else:
        job.status = 'generating'
        db.commit()
        log_activity(job.company_id, ACTION_PURCHASE, user_id=job.user_id, scrub_job_id=job.id,
                     meta={'price_cents': job.price_cents, 'unique': job.unique_count,
                           'stripe_intent': job.stripe_payment_intent_id, 'kind': 'scrub'})
        try:
            enqueue_generate_scrub_artifact(job.id)
        except Exception as e:
            logger.exception("could not enqueue artifact generation for job %s", job.id)
            job.status = 'failed'
            job.failure_reason = f'could not start file generation: {e}'[:500]
            db.commit()


@api_bp.route('/scrub-jobs/<int:job_id>/pay', methods=['POST'])
@mailer_login_required
def pay_scrub_job(job_id):
    """Charge for a scrub. With Stripe ON: create a PaymentIntent and return its
    client_secret; the job only advances on the `payment_intent.succeeded`
    webhook. With Stripe OFF (stub): advance immediately."""
    from flask import current_app
    db = get_db()
    job = db.query(ScrubJob).filter_by(id=job_id, company_id=g.current_company.id).first()
    if not job:
        return jsonify({'error': 'not found'}), 404
    if job.status not in ('priced', 'awaiting_payment'):
        return jsonify({'error': f'job is in status {job.status}'}), 409

    # Guard *before* charging — nothing to clean / sell ⇒ fail fast, no charge.
    if current_app.config.get('EO_FTP_ENABLED'):
        if job.email_column_index is None or int(job.uploaded_count or 0) <= 0:
            return jsonify({'error': "This list has nothing to clean (no email column "
                            "confirmed, or zero records). Please start a new scrub."}), 400
        desc = f'Gravitas clean #{job.id}'
    else:
        kept_cols = (db.query(ScrubJobFieldMapping)
                     .filter_by(scrub_job_id=job.id, skip=False).count())
        if kept_cols == 0:
            return jsonify({'error': "This scrub can't be downloaded because no columns were "
                            "mapped (an email column is required). Please start a new scrub and "
                            "map your email column."}), 400
        if int(job.unique_count or 0) <= 0:
            return jsonify({'error': "No unique records were found for this list, so there's "
                            "nothing to download — you have not been charged."}), 400
        desc = f'Gravitas scrub #{job.id}'

    customer_id = None
    if stripe_service.enabled():
        customer_id = stripe_service.ensure_customer(g.current_company)
        g.current_company.stripe_customer_id = customer_id

    intent = create_payment_intent(
        int(job.price_cents or 0), description=desc,
        metadata={'kind': 'scrub', 'job_id': job.id, 'company_id': job.company_id},
        customer_id=customer_id,
    )
    job.stripe_payment_intent_id = intent['id']

    if stripe_service.enabled():
        # Async: the webhook advances the job once Stripe confirms the charge.
        job.status = 'awaiting_payment'
        db.commit()
        return jsonify({
            'requires_payment': True,
            'client_secret': intent['client_secret'],
            'publishable_key': stripe_service.publishable_key(),
            'amount_cents': int(job.price_cents or 0),
            'job': job.to_dict(),
        })

    # Stub: instant success → advance now.
    job.paid_at = datetime.utcnow()
    db.commit()
    _advance_paid_scrub_job(db, job)
    if job.status == 'failed':
        return jsonify({'error': job.failure_reason or 'could not start processing'}), 503
    return jsonify(job.to_dict())


@api_bp.route('/scrub-jobs/<int:job_id>/payment-confirm', methods=['POST'])
@mailer_login_required
def confirm_scrub_payment(job_id):
    """Called by the client right after Stripe confirms the card. We re-verify
    the PaymentIntent status server-side, then advance the job. Idempotent and
    safe alongside the webhook (whichever lands first wins; the other no-ops)."""
    db = get_db()
    job = db.query(ScrubJob).filter_by(id=job_id, company_id=g.current_company.id).first()
    if not job:
        return jsonify({'error': 'not found'}), 404
    if not stripe_service.enabled():
        return jsonify(job.to_dict())   # stub already advanced at /pay
    if not job.stripe_payment_intent_id:
        return jsonify({'error': 'no payment in progress'}), 409
    pi = stripe_service.retrieve_payment_intent(job.stripe_payment_intent_id)
    status = pi['status'] if isinstance(pi, dict) else getattr(pi, 'status', None)
    if status != 'succeeded':
        return jsonify({'error': f'payment not completed ({status})', 'status': status}), 402
    if not job.paid_at:
        job.paid_at = datetime.utcnow()
        db.commit()
    _advance_paid_scrub_job(db, job)
    return jsonify(job.to_dict())


# ── Purchase jobs ─────────────────────────────────────────────────────────

@api_bp.route('/purchase-jobs/quote', methods=['POST'])
@mailer_login_required
def purchase_quote():
    """Calculate a quote without creating a job. Used by the volume picker."""
    data = request.get_json(silent=True) or {}
    quote = calc_purchase_quote(
        data.get('selected_verticals') or [],
        int(data.get('volume') or 0),
        promo_code=data.get('promo_code'),
    )
    if data.get('promo_code'):
        log_activity(
            g.current_company.id, ACTION_APPLY_PROMO,
            user_id=g.current_user.id,
            meta={'code': data.get('promo_code'),
                  'matched': bool(quote.get('promo')),
                  'discount_cents': quote.get('promo_discount_cents', 0)},
        )
    return jsonify(quote)


@api_bp.route('/purchase-jobs', methods=['POST'])
@mailer_login_required
def create_purchase_job():
    """Persist a quote as a purchase job (awaiting payment)."""
    data = request.get_json(silent=True) or {}
    selected = data.get('selected_verticals') or []
    volume = int(data.get('volume') or 0)
    promo = data.get('promo_code')
    if not selected or volume <= 0:
        return jsonify({'error': 'selected_verticals + positive volume required'}), 400

    quote = calc_purchase_quote(selected, volume, promo_code=promo)
    if not quote['rows']:
        return jsonify({'error': 'No valid verticals selected'}), 400

    db = get_db()
    job = PurchaseJob(
        company_id=g.current_company.id,
        user_id=g.current_user.id,
        status='awaiting_payment',
        selected_verticals_json=quote['rows'],
        volume=volume,
        subtotal_cents=quote['subtotal_cents'],
        discount_cents=quote['discount_cents'],
        promo_code=(promo or None),
        price_cents=quote['price_cents'],
    )
    db.add(job)
    db.commit()

    log_activity(
        g.current_company.id, ACTION_BUY_INIT,
        user_id=g.current_user.id, purchase_job_id=job.id,
        meta={'volume': volume, 'verticals': [r['vertical_id'] for r in quote['rows']],
              'price_cents': quote['price_cents'],
              'promo_code': promo, 'tier_pct': quote['tier_pct']},
    )
    return jsonify(job.to_dict())


@api_bp.route('/purchase-jobs/<int:job_id>', methods=['GET'])
@mailer_login_required
def get_purchase_job(job_id):
    db = get_db()
    job = db.query(PurchaseJob).filter_by(id=job_id, company_id=g.current_company.id).first()
    if not job:
        return jsonify({'error': 'not found'}), 404
    return jsonify(job.to_dict())


@api_bp.route('/purchase-jobs/<int:job_id>/pay', methods=['POST'])
def _complete_paid_purchase_job(db, job):
    """Generate the result file + mark a PAID purchase complete. Idempotent, no
    request context — safe from the stub path or the Stripe webhook."""
    if job.status == 'complete':
        return
    if not job.paid_at:
        job.paid_at = datetime.utcnow()
    job.status = 'paid'
    db.flush()
    filename, s3_key = generate_purchase_artifact(job)
    job.result_filename = filename
    job.result_s3_key = s3_key
    job.status = 'complete'
    job.completed_at = datetime.utcnow()
    db.commit()
    log_activity(job.company_id, ACTION_PURCHASE, user_id=job.user_id, purchase_job_id=job.id,
                 meta={'price_cents': job.price_cents, 'volume': job.volume,
                       'verticals': [v.get('vertical_id') for v in (job.selected_verticals_json or [])],
                       'stripe_intent': job.stripe_payment_intent_id, 'kind': 'purchase'})


@mailer_login_required
def pay_purchase_job(job_id):
    """Charge for a records purchase. Stripe ON: PaymentIntent → client_secret,
    completed on webhook. Stripe OFF (stub): generate + complete now."""
    db = get_db()
    job = db.query(PurchaseJob).filter_by(id=job_id, company_id=g.current_company.id).first()
    if not job:
        return jsonify({'error': 'not found'}), 404
    if job.status not in ('awaiting_payment', 'priced'):
        return jsonify({'error': f'job is in status {job.status}'}), 409

    customer_id = None
    if stripe_service.enabled():
        customer_id = stripe_service.ensure_customer(g.current_company)
        g.current_company.stripe_customer_id = customer_id

    intent = create_payment_intent(
        int(job.price_cents or 0), description=f'Gravitas purchase #{job.id}',
        metadata={'kind': 'purchase', 'job_id': job.id, 'company_id': job.company_id},
        customer_id=customer_id,
    )
    job.stripe_payment_intent_id = intent['id']

    if stripe_service.enabled():
        job.status = 'awaiting_payment'
        db.commit()
        return jsonify({
            'requires_payment': True,
            'client_secret': intent['client_secret'],
            'publishable_key': stripe_service.publishable_key(),
            'amount_cents': int(job.price_cents or 0),
            'job': job.to_dict(),
        })

    _complete_paid_purchase_job(db, job)
    return jsonify(job.to_dict())


@api_bp.route('/purchase-jobs/<int:job_id>/payment-confirm', methods=['POST'])
@mailer_login_required
def confirm_purchase_payment(job_id):
    """Verify the PaymentIntent server-side after the client confirms, then
    generate + complete the purchase. Idempotent; webhook is the backstop."""
    db = get_db()
    job = db.query(PurchaseJob).filter_by(id=job_id, company_id=g.current_company.id).first()
    if not job:
        return jsonify({'error': 'not found'}), 404
    if not stripe_service.enabled():
        return jsonify(job.to_dict())
    if not job.stripe_payment_intent_id:
        return jsonify({'error': 'no payment in progress'}), 409
    pi = stripe_service.retrieve_payment_intent(job.stripe_payment_intent_id)
    status = pi['status'] if isinstance(pi, dict) else getattr(pi, 'status', None)
    if status != 'succeeded':
        return jsonify({'error': f'payment not completed ({status})', 'status': status}), 402
    _complete_paid_purchase_job(db, job)
    return jsonify(job.to_dict())


# ── Download ──────────────────────────────────────────────────────────────
# Both /download (302 → presigned Spaces URL, browser-friendly) and
# /download-url (JSON { url, expires_in }) are exposed; the wizard uses the
# JSON variant so it can render the link in-page.

def _scrub_download_or_404(job_id):
    db = get_db()
    job = db.query(ScrubJob).filter_by(id=job_id, company_id=g.current_company.id).first()
    if not job or not job.result_s3_key:
        abort(404)
    return job


def _purchase_download_or_404(job_id):
    db = get_db()
    job = db.query(PurchaseJob).filter_by(id=job_id, company_id=g.current_company.id).first()
    if not job or not job.result_s3_key:
        abort(404)
    return job


@api_bp.route('/scrub-jobs/<int:job_id>/download', methods=['GET'])
@mailer_login_required
def download_scrub(job_id):
    job = _scrub_download_or_404(job_id)
    url = storage.presign_get_url(job.result_s3_key, filename=job.result_filename)
    log_activity(
        g.current_company.id, ACTION_DOWNLOAD,
        user_id=g.current_user.id, scrub_job_id=job.id,
        meta={'filename': job.result_filename, 'kind': 'scrub'},
    )
    return redirect(url, code=302)


@api_bp.route('/scrub-jobs/<int:job_id>/download-url', methods=['GET'])
@mailer_login_required
def download_scrub_url(job_id):
    from flask import current_app
    job = _scrub_download_or_404(job_id)
    url = storage.presign_get_url(job.result_s3_key, filename=job.result_filename)
    return jsonify({
        'url': url,
        'filename': job.result_filename,
        'expires_in': int(current_app.config.get('S3_DOWNLOAD_URL_TTL') or 3600),
    })


@api_bp.route('/purchase-jobs/<int:job_id>/download', methods=['GET'])
@mailer_login_required
def download_purchase(job_id):
    job = _purchase_download_or_404(job_id)
    url = storage.presign_get_url(job.result_s3_key, filename=job.result_filename)
    log_activity(
        g.current_company.id, ACTION_DOWNLOAD,
        user_id=g.current_user.id, purchase_job_id=job.id,
        meta={'filename': job.result_filename, 'kind': 'purchase'},
    )
    return redirect(url, code=302)


@api_bp.route('/purchase-jobs/<int:job_id>/download-url', methods=['GET'])
@mailer_login_required
def download_purchase_url(job_id):
    from flask import current_app
    job = _purchase_download_or_404(job_id)
    url = storage.presign_get_url(job.result_s3_key, filename=job.result_filename)
    return jsonify({
        'url': url,
        'filename': job.result_filename,
        'expires_in': int(current_app.config.get('S3_DOWNLOAD_URL_TTL') or 3600),
    })


# ── Job history ───────────────────────────────────────────────────────────

@api_bp.route('/jobs', methods=['GET'])
@mailer_login_required
def list_jobs():
    db = get_db()
    cid = g.current_company.id
    scrubs = db.query(ScrubJob).filter_by(company_id=cid).order_by(ScrubJob.created_at.desc()).limit(50).all()
    purchases = db.query(PurchaseJob).filter_by(company_id=cid).order_by(PurchaseJob.created_at.desc()).limit(50).all()
    return jsonify({
        'scrub_jobs': [s.to_dict() for s in scrubs],
        'purchase_jobs': [p.to_dict() for p in purchases],
    })


# ── Stripe config + webhook ─────────────────────────────────────────────────

@api_bp.route('/stripe/config', methods=['GET'])
@mailer_login_required
def stripe_config():
    """Tell the front-end whether real Stripe is on + the publishable key, so it
    knows to mount the Payment Element vs. the stub instant-pay."""
    return jsonify({
        'enabled': stripe_service.enabled(),
        'publishable_key': stripe_service.publishable_key(),
    })


@api_bp.route('/stripe/webhook', methods=['POST'])
def stripe_webhook():
    """Stripe's authoritative payment signal. Verifies the signature, then
    advances the matching job (idempotently). No session — Stripe calls this."""
    payload = request.get_data()
    sig = request.headers.get('Stripe-Signature', '')
    try:
        event = stripe_service.construct_event(payload, sig)
    except Exception as e:
        logger.warning("stripe webhook signature/parse failed: %s", e)
        return jsonify({'error': 'invalid signature'}), 400

    etype = event['type']
    obj = event['data']['object']
    db = get_db()
    try:
        if etype == 'payment_intent.succeeded':
            pid = obj['id']
            meta = obj.get('metadata') or {}
            kind = meta.get('kind')
            if kind == 'purchase':
                job = db.query(PurchaseJob).filter_by(stripe_payment_intent_id=pid).first()
                if job:
                    _complete_paid_purchase_job(db, job)
            else:  # scrub (EO or legacy — _advance decides by config)
                job = db.query(ScrubJob).filter_by(stripe_payment_intent_id=pid).first()
                if job:
                    if not job.paid_at:
                        job.paid_at = datetime.utcnow()
                        db.commit()
                    _advance_paid_scrub_job(db, job)
            logger.info("stripe webhook: payment_intent.succeeded %s (kind=%s)", pid, kind)

        elif etype == 'setup_intent.succeeded':
            _store_setup_intent_payment_method(db, obj)
            logger.info("stripe webhook: setup_intent.succeeded %s", obj.get('id'))

    except Exception:
        logger.exception("stripe webhook handling failed for %s", etype)
        return jsonify({'error': 'handler error'}), 500   # let Stripe retry

    return jsonify({'received': True})


def _store_setup_intent_payment_method(db, setup_intent):
    """Persist the card a customer just saved via a SetupIntent. Backstops the
    client confirm-step so a saved card is never lost."""
    from app.models.mailer_company import MailerCompany
    from app.models.payment_method import PaymentMethod
    pm_id = setup_intent.get('payment_method')
    customer_id = setup_intent.get('customer')
    if not pm_id or not customer_id:
        return
    company = db.query(MailerCompany).filter_by(stripe_customer_id=customer_id).first()
    if not company:
        return
    fields = stripe_service.pm_card_fields(stripe_service.retrieve_payment_method(pm_id))
    pm = db.query(PaymentMethod).filter_by(company_id=company.id).first()
    if pm is None:
        pm = PaymentMethod(company_id=company.id)
        db.add(pm)
    pm.brand = fields['brand']
    pm.last4 = fields['last4']
    pm.exp_month = fields['exp_month']
    pm.exp_year = fields['exp_year']
    pm.cardholder_name = fields.get('cardholder_name')
    pm.stripe_payment_method_id = pm_id
    pm.updated_at = datetime.utcnow()
    db.commit()
