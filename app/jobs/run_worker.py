"""Standalone RQ worker process entrypoint.

Started by the `worker:` line in Procfile (and the worker component in
.do/app.yaml). Builds a Flask app (so import_worker can use config + DB),
then enters RQ's blocking work loop.

Usage:
    python -m app.jobs.run_worker
"""
import logging
import os
import sys

# macOS-only: prevent the work-horse subprocess from crashing on fork after an
# Objective-C class (NSNumber etc., dragged in by boto3) has been initialized.
# Harmless on Linux; standard advice from RQ docs for darwin.
if sys.platform == 'darwin':
    os.environ.setdefault('OBJC_DISABLE_INITIALIZE_FORK_SAFETY', 'YES')

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger(__name__)


def main():
    # Add ./lib for local dev (matches run.py)
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    lib = os.path.join(here, 'lib')
    if os.path.isdir(lib) and lib not in sys.path:
        sys.path.insert(0, lib)

    from app import create_app
    from config import DevelopmentConfig, ProductionConfig

    cls = ProductionConfig if os.getenv('FLASK_ENV') == 'production' else DevelopmentConfig
    app = create_app(cls)
    with app.app_context():
        from app.jobs.queue import get_connection
        from rq import Queue, SimpleWorker

        queue_name = app.config.get('RQ_QUEUE', 'mailer-default')
        conn = get_connection()
        logger.info("RQ worker starting on queue=%s redis=%s",
                    queue_name, app.config.get('REDIS_URL'))
        # SimpleWorker runs jobs in-process (no fork). Trade-off: a job crash
        # takes down the worker, but the process supervisor restarts it. This
        # avoids the macOS fork+libobjc crash entirely, and is fine for the
        # current scale (one worker, one file-import job type).
        worker = SimpleWorker([Queue(queue_name, connection=conn)], connection=conn)
        # with_scheduler=True so RQ runs scheduled jobs (queue.enqueue_in) — the
        # EmailOversight result poller reschedules itself this way (eo_worker).
        worker.work(with_scheduler=True)


if __name__ == '__main__':
    main()
