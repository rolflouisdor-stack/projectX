"""Lazy Redis connection + RQ queue factory.

Web process calls `enqueue_import(job_id)` after the user confirms their
column mapping. RQ serializes the function reference + args and a worker
process picks it up on the other side.
"""
import os
import logging

logger = logging.getLogger(__name__)

_redis_conn = None
_queue = None


def _redis_url() -> str:
    from flask import current_app
    try:
        return current_app.config['REDIS_URL']
    except RuntimeError:
        # Outside of an app context (e.g. the worker bootstrap reading env directly)
        return os.getenv('REDIS_URL', 'redis://localhost:6379/0')


def _queue_name() -> str:
    from flask import current_app
    try:
        return current_app.config['RQ_QUEUE']
    except RuntimeError:
        return os.getenv('RQ_QUEUE', 'mailer-default')


def get_connection():
    global _redis_conn
    if _redis_conn is None:
        import redis
        _redis_conn = redis.from_url(_redis_url())
    return _redis_conn


def get_queue():
    global _queue
    if _queue is None:
        from rq import Queue
        _queue = Queue(_queue_name(), connection=get_connection())
    return _queue


def enqueue_import(scrub_job_id: int):
    """Queue the import worker. Returns the RQ Job (has its own id)."""
    from app.jobs.import_worker import run_import
    q = get_queue()
    return q.enqueue(
        run_import,
        scrub_job_id,
        job_timeout=60 * 60,         # 1 hour max — plenty for a few hundred MB
        result_ttl=60 * 60 * 24,     # keep result info around for 24h
        failure_ttl=60 * 60 * 24 * 7,
    )
