"""Background job queue (RQ + Redis).

The web process enqueues import jobs via `enqueue_import`. A separate worker
process (Procfile: `worker`) drains the queue and executes the import
function inside a full Flask app context so SQLAlchemy + boto3 work as they
would in a request.
"""
