"""One-off maintenance jobs, run on the RQ worker (which has Spaces creds).

`delete_spaces_prefixes` removes orphaned object trees — e.g. the upload/result
files left in Spaces after a scrub_job row is deleted (the DB cascade doesn't
touch object storage). Safe: each prefix must be non-empty (storage.delete_prefix
guards against wiping the bucket).
"""
import logging
import os

logger = logging.getLogger(__name__)


def _app():
    from app import create_app
    from config import DevelopmentConfig, ProductionConfig
    cls = ProductionConfig if os.getenv('FLASK_ENV') == 'production' else DevelopmentConfig
    return create_app(cls)


def delete_spaces_prefixes(prefixes):
    """Delete all Spaces objects under each given prefix. Returns total deleted."""
    app = _app()
    total = 0
    with app.app_context():
        from app.services import storage
        for prefix in prefixes:
            try:
                n = storage.delete_prefix(prefix)
                total += n
                logger.info("maintenance: deleted %s object(s) under %s", n, prefix)
            except Exception:
                logger.exception("maintenance: failed deleting under %s", prefix)
    logger.info("maintenance: delete_spaces_prefixes done, %s object(s) total", total)
    return total
