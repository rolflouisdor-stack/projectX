"""SQLAlchemy session + cache extensions for the Mailer Portal."""
import ssl
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, scoped_session, sessionmaker


class Base(DeclarativeBase):
    pass


db_session = None
engine = None


def _prepare_db_url(url):
    """Normalize DATABASE_URL for SQLAlchemy + PyMySQL.

    DigitalOcean's managed MySQL binds a `mysql://...?ssl-mode=REQUIRED` URL.
    Bare `mysql://` makes SQLAlchemy pick the uninstalled mysqlclient driver,
    and PyMySQL doesn't understand the `ssl-mode` query param — so rewrite the
    scheme to pymysql, drop the query string, and require TLS via an explicit
    context for DO-hosted clusters. Local dev URLs (already +pymysql, not a DO
    host) pass through untouched with empty connect_args.
    """
    connect_args = {}
    if not url:
        return url, connect_args
    if url.startswith('mysql://'):
        url = 'mysql+pymysql://' + url[len('mysql://'):]
    if 'ondigitalocean.com' in url and url.startswith('mysql+pymysql://'):
        url = url.split('?', 1)[0]
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        connect_args['ssl'] = ctx
    return url, connect_args


def init_db(app):
    """Initialise the database engine and session."""
    global engine, db_session
    url, connect_args = _prepare_db_url(app.config['SQLALCHEMY_DATABASE_URI'])
    engine = create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=3600,
        connect_args=connect_args,
    )
    session_factory = sessionmaker(bind=engine)
    db_session = scoped_session(session_factory)

    # Import every model so Base.metadata sees them before create_all
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=engine)

    # Apply boot-time schema patches that create_all can't (alter columns,
    # widen enums). Safe to re-run.
    from app.services.migrations import run_migrations
    run_migrations(engine)

    @app.teardown_appcontext
    def shutdown_session(exception=None):
        if db_session:
            db_session.remove()


def get_db():
    return db_session
