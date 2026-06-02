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


def _keepalive_options():
    """Aggressive TCP keepalive so the socket stays warm under DO's proxy.

    DO managed Valkey sits behind a connection proxy that reaps TCP connections
    idle for ~5 min. During a long in-process import (SimpleWorker runs the job
    in the worker process, no fork) the worker issues no Redis commands for the
    whole job, so the socket goes idle and the proxy drops it — surfacing as
    "Could not connect to Redis: Connection closed by server" every ~5 min. TCP
    keepalive probes count as socket activity and reset that idle timer, so we
    probe well inside the window (first probe after 60s idle). Constant names
    differ by OS (Linux: TCP_KEEPIDLE; macOS: TCP_KEEPALIVE) — we set whichever
    exists and skip any the platform lacks.
    """
    import socket
    opts = {}
    idle = getattr(socket, 'TCP_KEEPIDLE', None) or getattr(socket, 'TCP_KEEPALIVE', None)
    if idle is not None:
        opts[idle] = 60          # seconds idle before the first keepalive probe
    if hasattr(socket, 'TCP_KEEPINTVL'):
        opts[socket.TCP_KEEPINTVL] = 30   # seconds between probes
    if hasattr(socket, 'TCP_KEEPCNT'):
        opts[socket.TCP_KEEPCNT] = 3      # drop after this many failed probes
    return opts


def get_connection():
    global _redis_conn
    if _redis_conn is None:
        import redis
        from redis.retry import Retry
        from redis.backoff import ExponentialBackoff
        from redis.exceptions import ConnectionError as RedisConnectionError
        from redis.exceptions import TimeoutError as RedisTimeoutError

        url = _redis_url()
        kwargs = {
            # Keep the connection from being idle-reaped by DO's Valkey proxy
            # during long in-process jobs (see _keepalive_options).
            'socket_keepalive': True,
            'socket_keepalive_options': _keepalive_options(),
            # Ping before a command if the socket has been idle > interval, so a
            # silently-dropped connection is detected and replaced before use.
            'health_check_interval': 30,
            'socket_connect_timeout': 10,
            # If a command still hits a dropped connection, reconnect + retry
            # transparently instead of bubbling up an error. (No socket_timeout
            # so RQ's blocking dequeue isn't interrupted.)
            'retry': Retry(ExponentialBackoff(cap=10, base=0.5), retries=5),
            'retry_on_error': [RedisConnectionError, RedisTimeoutError],
        }
        if url.startswith('rediss://'):
            # DO managed Valkey presents a cert signed by DO's private CA, not in
            # the system trust store, so default verification fails. Skip cert
            # verification (the connection is still TLS-encrypted). Harden with
            # the DO CA cert later if stricter verification is required.
            kwargs['ssl_cert_reqs'] = 'none'
            kwargs['ssl_check_hostname'] = False
        _redis_conn = redis.from_url(url, **kwargs)
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
        # Generous ceiling so a genuinely huge file never gets killed mid-import
        # (which, pre-fix, stranded the job in `importing`). With bulk inserts an
        # import is minutes, not hours — this is just a safety backstop.
        job_timeout=4 * 60 * 60,     # 4 hours
        result_ttl=60 * 60 * 24,     # keep result info around for 24h
        failure_ttl=60 * 60 * 24 * 7,
    )


def enqueue_generate_scrub_artifact(scrub_job_id: int):
    """Queue result-xlsx generation. Runs off the web request path so large
    result sets don't blow the gunicorn timeout."""
    from app.jobs.artifact_worker import generate_scrub_artifact_job
    q = get_queue()
    return q.enqueue(
        generate_scrub_artifact_job,
        scrub_job_id,
        job_timeout=60 * 60,
        result_ttl=60 * 60 * 24,
        failure_ttl=60 * 60 * 24 * 7,
    )
