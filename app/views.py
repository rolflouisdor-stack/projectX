"""Server-rendered HTML pages for the Mailer Portal."""
from datetime import datetime, timedelta
from flask import Blueprint, render_template, redirect, g, current_app, request, url_for
from app.auth.decorators import mailer_login_required, current_user_or_none

views_bp = Blueprint('views', __name__)

VERIFICATION_TTL = timedelta(hours=24)

# Hosts that should serve the public marketing landing page at `/` instead of the
# portal redirect. The portal lives at mailer.gravitasleads.io; the apex (and www)
# show the landing page. Everything else (the DO app URL, localhost) keeps the
# portal behavior — preview the landing locally with `curl -H 'Host: gravitasleads.io'`.
LANDING_HOSTS = {'gravitasleads.io', 'www.gravitasleads.io'}


def _common_ctx(user=None, company=None):
    return {'partner': company.to_dict() if company else None,
            'user': user.to_dict() if user else None}


def _stripe_ctx():
    """Stripe flags for payment pages. publishable key is safe to expose."""
    return {
        'stripe_enabled': bool(current_app.config.get('STRIPE_ENABLED')),
        'stripe_publishable_key': current_app.config.get('STRIPE_PUBLISHABLE_KEY') or '',
        'stripe_pass_fee': bool(current_app.config.get('STRIPE_PASS_FEE')),
        'stripe_fee_percent': float(current_app.config.get('STRIPE_FEE_PERCENT') or 0),
        'stripe_fee_fixed_cents': int(current_app.config.get('STRIPE_FEE_FIXED_CENTS') or 0),
    }


@views_bp.route('/')
def root():
    host = (request.host or '').split(':')[0].lower()
    if host in LANDING_HOSTS:
        return render_template('landing.html', L=url_for('static', filename='landing'))
    user, company = current_user_or_none()
    if user:
        return redirect('/dashboard')
    return redirect('/login')


@views_bp.route('/terms-of-service')
def terms_page():
    return render_template('terms.html', L=url_for('static', filename='landing'))


@views_bp.route('/privacy-policy')
def privacy_page():
    return render_template('privacy.html', L=url_for('static', filename='landing'))


@views_bp.route('/login')
def login_page():
    return render_template('auth/login.html')


@views_bp.route('/signup')
def signup_page():
    return render_template('auth/signup.html')


@views_bp.route('/verify')
def verify_email():
    """Activate an account from the emailed link, then log the user straight in.
    Single-use token; expires after VERIFICATION_TTL. On any failure we bounce to
    /login with a flag the page can surface (expired/invalid)."""
    from app.extensions import get_db
    from app.models.mailer_user import MailerUser
    from app.auth.jwt_utils import issue_token, set_session_cookie

    token = (request.args.get('token') or '').strip()
    if not token:
        return redirect('/login?verify=invalid')
    db = get_db()
    if db is None:
        return redirect('/login?verify=error')
    user = db.query(MailerUser).filter_by(verification_token=token).first()
    if not user:
        # Either a bad token or one already consumed (e.g. link clicked twice).
        return redirect('/login?verify=invalid')
    if user.email_verified:
        return redirect('/login?verify=already')
    sent = user.verification_sent_at
    if sent and (datetime.utcnow() - sent) > VERIFICATION_TTL:
        return redirect('/login?verify=expired')

    user.email_verified = True
    user.verification_token = None            # single use
    user.last_login_at = datetime.utcnow()
    db.commit()

    resp = redirect('/dashboard')
    set_session_cookie(resp, issue_token(user.id, user.company_id))
    return resp


@views_bp.route('/dashboard')
@mailer_login_required
def dashboard():
    return render_template('dashboard.html', **_common_ctx(g.current_user, g.current_company))


@views_bp.route('/scrub')
@mailer_login_required
def scrub_page():
    # EO flow (clean via EmailOversight) vs legacy mock-scrub flow drives which
    # wizard steps render. Off until EO_FTP_ENABLED is set.
    return render_template('scrub.html', eo_enabled=bool(current_app.config.get('EO_FTP_ENABLED')),
                           **_stripe_ctx(), **_common_ctx(g.current_user, g.current_company))


@views_bp.route('/buy')
@mailer_login_required
def buy_page():
    return render_template('buy.html', **_stripe_ctx(), **_common_ctx(g.current_user, g.current_company))


@views_bp.route('/jobs')
@mailer_login_required
def jobs_page():
    return render_template('jobs.html', **_common_ctx(g.current_user, g.current_company))


@views_bp.route('/account')
@mailer_login_required
def account_page():
    return render_template('account.html', **_stripe_ctx(), **_common_ctx(g.current_user, g.current_company))
