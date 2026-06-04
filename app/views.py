"""Server-rendered HTML pages for the Mailer Portal."""
from flask import Blueprint, render_template, redirect, g, current_app
from app.auth.decorators import mailer_login_required, current_user_or_none

views_bp = Blueprint('views', __name__)


def _common_ctx(user=None, company=None):
    return {'partner': company.to_dict() if company else None,
            'user': user.to_dict() if user else None}


@views_bp.route('/')
def root():
    user, company = current_user_or_none()
    if user:
        return redirect('/dashboard')
    return redirect('/login')


@views_bp.route('/login')
def login_page():
    return render_template('auth/login.html')


@views_bp.route('/signup')
def signup_page():
    return render_template('auth/signup.html')


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
                           **_common_ctx(g.current_user, g.current_company))


@views_bp.route('/buy')
@mailer_login_required
def buy_page():
    return render_template('buy.html', **_common_ctx(g.current_user, g.current_company))


@views_bp.route('/jobs')
@mailer_login_required
def jobs_page():
    return render_template('jobs.html', **_common_ctx(g.current_user, g.current_company))


@views_bp.route('/account')
@mailer_login_required
def account_page():
    return render_template('account.html', **_common_ctx(g.current_user, g.current_company))
