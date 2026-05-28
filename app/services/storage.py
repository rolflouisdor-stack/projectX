"""DigitalOcean Spaces / S3-compatible object storage.

All uploaded scrub lists and generated result xlsx files live in object
storage rather than the App Platform's ephemeral local filesystem.

Key layout (one Spaces bucket per environment):

    uploads/{company_id}/{job_id}/{uuid}-{filename}    ← original upload
    results/{company_id}/{job_id}/{filename}.xlsx      ← generated artifact

The browser uploads directly to Spaces via presigned multipart PUTs (so the
Flask process never proxies file bytes), and downloads via presigned GETs
with a short TTL. Ownership/permission checks happen in the API layer
before we hand out the URL.
"""
import logging
import uuid
from typing import Optional
from flask import current_app

logger = logging.getLogger(__name__)

_client = None


def _config():
    """Centralised access to the S3 config bag from Flask config."""
    cfg = current_app.config
    return {
        'endpoint_url': cfg.get('S3_ENDPOINT_URL') or '',
        'region': cfg.get('S3_REGION') or 'nyc3',
        'bucket': cfg.get('S3_BUCKET') or '',
        'access_key': cfg.get('S3_ACCESS_KEY') or '',
        'secret_key': cfg.get('S3_SECRET_KEY') or '',
        'part_size': int(cfg.get('S3_MULTIPART_PART_SIZE_BYTES') or 10 * 1024 * 1024),
        'upload_ttl': int(cfg.get('S3_UPLOAD_URL_TTL') or 600),
        'download_ttl': int(cfg.get('S3_DOWNLOAD_URL_TTL') or 3600),
        'addressing_style': cfg.get('S3_ADDRESSING_STYLE') or 'virtual',
    }


def is_configured() -> bool:
    """Soft check used by health/diagnostics; true once required keys exist."""
    c = _config()
    return all([c['endpoint_url'], c['bucket'], c['access_key'], c['secret_key']])


def get_client():
    """Lazily build and cache a boto3 S3 client pointed at Spaces.

    Cached on the Flask app context so unit tests can override config
    between calls.
    """
    import boto3
    from botocore.client import Config

    c = _config()
    if not c['endpoint_url']:
        raise RuntimeError("S3_ENDPOINT_URL is not set — cannot reach object storage")
    if not c['access_key'] or not c['secret_key']:
        raise RuntimeError("S3 credentials are not set — see .env.example")

    return boto3.client(
        's3',
        endpoint_url=c['endpoint_url'],
        region_name=c['region'],
        aws_access_key_id=c['access_key'],
        aws_secret_access_key=c['secret_key'],
        config=Config(signature_version='s3v4', s3={'addressing_style': c['addressing_style']}),
    )


# ── Key layout helpers ────────────────────────────────────────────────────

def upload_key(company_id: int, job_id: int, filename: str) -> str:
    """Where an uploaded list lives. UUID-prefixed so users can re-upload the
    same filename without collision."""
    return f'uploads/{company_id}/{job_id}/{uuid.uuid4().hex}-{filename}'


def result_key(company_id: int, job_id: int, filename: str) -> str:
    """Where the generated result xlsx lives."""
    return f'results/{company_id}/{job_id}/{filename}'


# ── Multipart upload (browser → Spaces) ───────────────────────────────────

def initiate_multipart_upload(key: str, content_type: str = 'application/octet-stream') -> str:
    """Server-side: open a multipart upload, return the UploadId."""
    s3 = get_client()
    r = s3.create_multipart_upload(
        Bucket=_config()['bucket'],
        Key=key,
        ContentType=content_type,
    )
    return r['UploadId']


def presign_part_url(key: str, upload_id: str, part_number: int) -> str:
    """Server-side: hand the browser a presigned URL for a single part PUT."""
    s3 = get_client()
    c = _config()
    return s3.generate_presigned_url(
        'upload_part',
        Params={
            'Bucket': c['bucket'],
            'Key': key,
            'UploadId': upload_id,
            'PartNumber': part_number,
        },
        ExpiresIn=c['upload_ttl'],
    )


def complete_multipart_upload(key: str, upload_id: str, parts: list) -> dict:
    """Server-side: finalize, given the part-number+ETag list the browser
    collected during its PUTs. `parts` is [{PartNumber, ETag}, ...]."""
    s3 = get_client()
    return s3.complete_multipart_upload(
        Bucket=_config()['bucket'],
        Key=key,
        UploadId=upload_id,
        MultipartUpload={'Parts': sorted(parts, key=lambda p: p['PartNumber'])},
    )


def abort_multipart_upload(key: str, upload_id: str) -> None:
    """Best-effort cleanup; safe to call on already-aborted uploads."""
    try:
        get_client().abort_multipart_upload(
            Bucket=_config()['bucket'],
            Key=key,
            UploadId=upload_id,
        )
    except Exception as e:
        logger.warning("abort_multipart_upload(%s) failed: %s", key, e)


# ── Single-shot helpers (used for the generated result xlsx) ──────────────

def put_object(key: str, body: bytes, content_type: str = 'application/octet-stream') -> None:
    """Direct PUT for server-generated artifacts (the result xlsx)."""
    get_client().put_object(
        Bucket=_config()['bucket'],
        Key=key,
        Body=body,
        ContentType=content_type,
    )


def get_object_stream(key: str):
    """Open a streaming body for reading. Caller is responsible for closing."""
    return get_client().get_object(Bucket=_config()['bucket'], Key=key)['Body']


def head_object(key: str) -> dict:
    return get_client().head_object(Bucket=_config()['bucket'], Key=key)


def presign_get_url(key: str, filename: Optional[str] = None) -> str:
    """Presigned GET; if filename is given, S3 sets Content-Disposition so the
    browser downloads with that name instead of the raw key."""
    s3 = get_client()
    c = _config()
    params = {'Bucket': c['bucket'], 'Key': key}
    if filename:
        params['ResponseContentDisposition'] = f'attachment; filename="{filename}"'
    return s3.generate_presigned_url('get_object', Params=params, ExpiresIn=c['download_ttl'])


def part_size() -> int:
    """Exposed so the API can tell the browser how big each chunk should be."""
    return _config()['part_size']
