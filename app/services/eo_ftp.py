"""EmailOversight bulk-validation FTP client (see FTP.md).

Plain FTP (port 21, passive) to the shared `cx3ads` dropbox. We upload a
uniquely-named RAW file to `/cx3ads/`; EO validates the emails, annotates each
row (ValidationStatusId, ValidationStatus, …) and drops
`{name}-processed.csv` in `/cx3ads/processed/`. We poll for OUR file by name,
confirm it's finished (size stable across polls), retrieve it, and store it in
Spaces for the user to download as-is.

Shared-folder etiquette (FTP.md §9): only ever read/write our `mailer-*` files;
never delete or touch the CX3-ops `EmailOversight_TOVALIDATE*` files.
"""
import csv
import ftplib
import logging
import os
import re
import tempfile
from datetime import datetime

from flask import current_app

from app.services import storage

logger = logging.getLogger(__name__)


def is_enabled() -> bool:
    return bool(current_app.config.get('EO_FTP_ENABLED'))


def _cfg():
    c = current_app.config
    return {
        'host': c.get('EO_FTP_HOST') or 'ftp.emailoversight.com',
        'port': int(c.get('EO_FTP_PORT') or 21),
        'user': c.get('EO_FTP_USER') or 'cx3ads',
        'password': c.get('EO_FTP_PASSWORD') or '',
        'tls': bool(c.get('EO_FTP_TLS')),
        'root': (c.get('EO_FTP_ROOT') or 'cx3ads').strip('/'),
        'processed': (c.get('EO_FTP_PROCESSED_DIR') or 'processed').strip('/'),
        'prefix': c.get('EO_FTP_FILENAME_PREFIX') or 'mailer-',
    }


def _connect(cfg):
    if cfg['tls']:
        ftp = ftplib.FTP_TLS()
        ftp.connect(cfg['host'], cfg['port'], timeout=60)
        ftp.login(cfg['user'], cfg['password'])
        ftp.prot_p()
    else:
        ftp = ftplib.FTP()
        ftp.connect(cfg['host'], cfg['port'], timeout=60)
        ftp.login(cfg['user'], cfg['password'])
    ftp.set_pasv(True)
    return ftp


def brand_slug(name, company_id=None) -> str:
    """Slugify a company name for the filename: [a-z0-9-], max 32, fallback co{id}."""
    s = re.sub(r'[^a-z0-9]+', '-', (name or '').lower()).strip('-')[:32]
    return s or (f'co{company_id}' if company_id else 'co')


def build_raw_name(job, company_name=None) -> str:
    """The unique RAW filename we PUT to /cx3ads/, e.g.
    mailer-sj42-brandface-20260603-153022.csv. The sj{id}+timestamp make the
    stem globally unique, which is how we later find our result.

    NOTE (verified 2026-06-03 against the live FTP): EO does NOT name the result
    `{stem}-processed.csv`. It inserts its own internal id:
    `{stem}_{EO_id}-processed.csv` (e.g. ..._4064478-processed.csv). So we cannot
    predict the exact processed name — we match by our stem prefix in
    find_processed()."""
    cfg = _cfg()
    slug = brand_slug(company_name, job.company_id)
    ts = datetime.utcnow().strftime('%Y%m%d-%H%M%S')
    return f"{cfg['prefix']}sj{job.id}-{slug}-{ts}.csv"


def _rewrite_to_email_csv(src_path, dest_path, fmt, delimiter, email_col_index):
    """Copy the upload to a CSV whose chosen email column header is literally
    `email` (EO requires it). All other columns are preserved as-is."""
    def fix_header(row):
        out = list(row)
        if email_col_index is not None and 0 <= email_col_index < len(out):
            out[email_col_index] = 'email'
        return out

    with open(dest_path, 'w', newline='', encoding='utf-8') as out:
        w = csv.writer(out)
        if fmt == 'xlsx':
            from openpyxl import load_workbook
            wb = load_workbook(src_path, read_only=True, data_only=True)
            ws = wb[wb.sheetnames[0]]
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                row = ['' if c is None else c for c in row]
                w.writerow(fix_header(row) if i == 0 else row)
            wb.close()
        else:
            d = delimiter or ','
            with open(src_path, 'r', encoding='utf-8', errors='replace', newline='') as f:
                for i, row in enumerate(csv.reader(f, delimiter=d)):
                    w.writerow(fix_header(row) if i == 0 else row)


def submit(job, company_name, fmt, delimiter, email_col_index):
    """Download the upload from Spaces, ensure an `email` header, and STOR it to
    /cx3ads/. Returns the raw filename we uploaded."""
    cfg = _cfg()
    raw = build_raw_name(job, company_name)
    suffix = os.path.splitext(job.original_filename or '')[1] or '.dat'
    fd, src = tempfile.mkstemp(prefix=f'eo_src_{job.id}_', suffix=suffix); os.close(fd)
    fd2, dst = tempfile.mkstemp(prefix=f'eo_out_{job.id}_', suffix='.csv'); os.close(fd2)
    try:
        storage.download_to_file(job.s3_key, src)
        _rewrite_to_email_csv(src, dst, fmt, delimiter, email_col_index)
        ftp = _connect(cfg)
        try:
            ftp.cwd('/' + cfg['root'])
            with open(dst, 'rb') as fh:
                ftp.storbinary(f'STOR {raw}', fh, blocksize=1 << 20)
        finally:
            try:
                ftp.quit()
            except Exception:
                pass
        logger.info("eo_ftp: submitted %s for scrub_job %s", raw, job.id)
        return raw
    finally:
        for p in (src, dst):
            try:
                os.remove(p)
            except OSError:
                pass


def find_processed(raw_name):
    """Find OUR result in processed/. EO names it `{stem}_{EO_id}-processed.csv`,
    so match by our unique stem prefix. Returns (matched_name, size_bytes) or
    (None, None). size -1 = present but size unavailable."""
    cfg = _cfg()
    stem = raw_name[:-4] if raw_name.endswith('.csv') else raw_name
    ftp = _connect(cfg)
    try:
        ftp.cwd(f"/{cfg['root']}/{cfg['processed']}")
        matches = [str(n) for n in ftp.nlst()
                   if str(n).startswith(stem + '_') and str(n).endswith('-processed.csv')]
        if not matches:
            return None, None
        name = sorted(matches)[-1]   # if EO ever re-runs, take the latest
        try:
            return name, ftp.size(name)
        except Exception:
            return name, -1
    finally:
        try:
            ftp.quit()
        except Exception:
            pass


def retrieve_to_spaces(processed_name, dest_key):
    """Download EO's processed file to a temp file and upload it to Spaces under
    dest_key (streamed via temp file — safe for large results). Returns bytes."""
    cfg = _cfg()
    fd, tmp = tempfile.mkstemp(prefix='eo_proc_', suffix='.csv'); os.close(fd)
    try:
        ftp = _connect(cfg)
        try:
            ftp.cwd(f"/{cfg['root']}/{cfg['processed']}")
            with open(tmp, 'wb') as fh:
                ftp.retrbinary(f'RETR {processed_name}', fh.write, blocksize=1 << 20)
        finally:
            try:
                ftp.quit()
            except Exception:
                pass
        size = os.path.getsize(tmp)
        storage.upload_file(dest_key, tmp, content_type='text/csv')
        logger.info("eo_ftp: stored %s -> %s (%s bytes)", processed_name, dest_key, size)
        return size
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
