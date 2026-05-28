"""SQLAlchemy session + cache extensions for the Mailer Portal."""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, scoped_session, sessionmaker


class Base(DeclarativeBase):
    pass


db_session = None
engine = None


def init_db(app):
    """Initialise the database engine and session."""
    global engine, db_session
    engine = create_engine(
        app.config['SQLALCHEMY_DATABASE_URI'],
        pool_pre_ping=True,
        pool_recycle=3600,
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
