"""Gravitas Leads — Mailer Portal Flask app factory."""
import logging
import os
from flask import Flask

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger(__name__)


def create_app(config_class):
    app = Flask(__name__,
                static_folder='static',
                template_folder='templates')
    app.config.from_object(config_class)

    from app.extensions import init_db
    init_db(app)

    # No-cache for static assets in dev — partner-portal session showed
    # how badly browsers/tunnels cache CSS otherwise.
    @app.after_request
    def _no_cache_static(response):
        from flask import request
        if request.path.startswith('/static/'):
            response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
        return response

    # CORS — only kicks in if CORS_ORIGINS is configured. Same-origin browser
    # calls (mailer's own templates → mailer's own /api/) don't need it; this
    # is for cross-origin callers like the CX3 Dashboard once it has its own
    # public host. Allowlist-driven, never `*`.
    cors_origins = app.config.get('CORS_ORIGINS') or []
    if cors_origins:
        @app.after_request
        def _apply_cors(response):
            from flask import request
            origin = request.headers.get('Origin')
            if origin and origin in cors_origins:
                response.headers['Access-Control-Allow-Origin'] = origin
                response.headers['Vary'] = 'Origin'
                response.headers['Access-Control-Allow-Credentials'] = 'true'
                response.headers['Access-Control-Allow-Headers'] = \
                    'Content-Type, X-Internal-Api-Key'
                response.headers['Access-Control-Allow-Methods'] = \
                    'GET, POST, PUT, DELETE, OPTIONS'
            return response

        @app.route('/<path:_any>', methods=['OPTIONS'])
        def _cors_preflight(_any):
            return ('', 204)

    # Blueprints
    from app.auth.routes import auth_bp
    from app.api.routes import api_bp
    from app.api.internal_routes import internal_bp
    from app.api.account_routes import account_bp
    from app.api.admin_routes import admin_bp
    from app.views import views_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(internal_bp)
    app.register_blueprint(account_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(views_bp)

    # One-time seeds (idempotent): verticals + a sample promo banner
    try:
        with app.app_context():
            from app.services.seeds import seed_defaults
            seed_defaults()
    except Exception as e:
        logger.warning("Seed step failed (non-fatal): %s", e)

    # Make sure the artifact directory exists for download stubs
    os.makedirs(app.config.get('ARTIFACT_DIR', '/tmp/gravitas_mailer_artifacts'),
                exist_ok=True)

    logger.info("Gravitas Mailer Portal ready on port %s (public=%s)",
                app.config.get('PORT', 5070),
                app.config.get('PUBLIC_BASE_URL'))
    return app
