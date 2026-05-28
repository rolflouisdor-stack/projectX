"""Mailer Portal config.

Every environment-specific knob comes from an env var. The deployment story
("one source of truth to change when the IP/host changes") is:

  - PUBLIC_BASE_URL  — the public origin this mailer is reachable at.
                       The ONLY value that has to change when you move from
                       127.0.0.1:5070 → DigitalOcean. Everything that needs
                       an absolute URL (Stripe webhooks, callback URLs in
                       emails, social previews) reads from here.
  - CORS_ORIGINS     — comma-separated list of origins allowed to call the
                       mailer's APIs from a browser. The CX3 Dashboard's
                       public origin goes here once it has one.
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _csv(name, default=''):
    raw = os.getenv(name, default) or ''
    return [s.strip() for s in raw.split(',') if s.strip()]


class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-key')
    PORT = int(os.getenv('PORT', 5070))

    # The single host knob. Local dev: http://127.0.0.1:5070.
    # Production: https://mailer.yourdomain.com  (or the App Platform URL).
    PUBLIC_BASE_URL = os.getenv('PUBLIC_BASE_URL', 'http://127.0.0.1:5070').rstrip('/')

    # Browser origins allowed to hit /api/* from JS in another tab. Same-origin
    # calls (mailer's own templates → mailer's own /api/) don't need an entry.
    # Add the CX3 Dashboard's public origin once it's hosted.
    CORS_ORIGINS = _csv('CORS_ORIGINS')

    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    JWT_TOKEN_EXPIRY_SECONDS = int(os.getenv('JWT_TOKEN_EXPIRY_SECONDS', 86400))
    JWT_COOKIE_SECURE = os.getenv('JWT_COOKIE_SECURE', 'false').lower() in ('true', '1', 'yes')

    INTERNAL_API_KEY = os.getenv('INTERNAL_API_KEY', '')

    STRIPE_ENABLED = os.getenv('STRIPE_ENABLED', 'false').lower() in ('true', '1', 'yes')
    STRIPE_SECRET_KEY = os.getenv('STRIPE_SECRET_KEY', '')
    STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET', '')

    EMAIL_VALIDATOR_ENABLED = os.getenv('EMAIL_VALIDATOR_ENABLED', 'false').lower() in ('true', '1', 'yes')
    EMAIL_VALIDATOR_PROVIDER = os.getenv('EMAIL_VALIDATOR_PROVIDER', 'neverbounce')
    EMAIL_VALIDATOR_API_KEY = os.getenv('EMAIL_VALIDATOR_API_KEY', '')

    ARTIFACT_DIR = os.getenv('ARTIFACT_DIR', '/tmp/gravitas_mailer_artifacts')
    ARTIFACT_TTL_DAYS = int(os.getenv('ARTIFACT_TTL_DAYS', 30))

    # Object storage (S3 / DO Spaces) — uploads land here, result xlsx lives here.
    S3_ENDPOINT_URL = os.getenv('S3_ENDPOINT_URL', '')
    S3_REGION = os.getenv('S3_REGION', 'nyc3')
    S3_BUCKET = os.getenv('S3_BUCKET', '')
    S3_ACCESS_KEY = os.getenv('S3_ACCESS_KEY', '')
    S3_SECRET_KEY = os.getenv('S3_SECRET_KEY', '')
    S3_MULTIPART_PART_SIZE_BYTES = int(os.getenv('S3_MULTIPART_PART_SIZE_BYTES', 10 * 1024 * 1024))
    S3_UPLOAD_URL_TTL = int(os.getenv('S3_UPLOAD_URL_TTL', 600))
    S3_DOWNLOAD_URL_TTL = int(os.getenv('S3_DOWNLOAD_URL_TTL', 3600))
    S3_ADDRESSING_STYLE = os.getenv('S3_ADDRESSING_STYLE', 'virtual')

    # Background job queue
    REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
    RQ_QUEUE = os.getenv('RQ_QUEUE', 'mailer-default')


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False
    # Force the cookie's Secure flag on in prod so the session cookie is
    # never sent over plain HTTP. JWT_COOKIE_SECURE in .env can override
    # back to false if someone explicitly needs to test with non-TLS.
    JWT_COOKIE_SECURE = os.getenv('JWT_COOKIE_SECURE', 'true').lower() in ('true', '1', 'yes')
