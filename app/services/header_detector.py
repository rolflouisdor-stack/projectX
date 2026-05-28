"""Detect headers + format + suggest standard-field mapping from an upload.

Used by `POST /api/scrub-jobs/{id}/detect-headers` to peek at just the first
few rows of an uploaded file living in Spaces — without downloading the
entire file — and produce:

    {
        'format': 'csv' | 'tsv' | 'xlsx',
        'delimiter': ',',          # None for xlsx
        'headers': ['Email', 'First Name', ...],
        'sample_rows': [...up to 5 rows...],
        'suggested_mapping': {
            'Email': 'email',
            'First Name': 'first_name',
            ...
        }
    }

Heuristics only; the user confirms or overrides everything in the UI.
"""
import csv
import io
import logging
import re
from typing import Optional

from openpyxl import load_workbook

from app.models.scrub_job_field_mapping import STANDARD_FIELDS
from app.services import storage

logger = logging.getLogger(__name__)

SAMPLE_BYTES_FOR_SNIFF = 64 * 1024   # 64 KB is enough to nail down delimiter + headers
SAMPLE_ROWS = 5


# Synonyms for the suggested-mapping step. Keys are standard field names; values
# are the variations we recognise (post-normalize). Add liberally — false
# positives cost the user one click to fix, false negatives cost more.
STANDARD_FIELD_SYNONYMS = {
    'first_name': {
        'first name', 'firstname', 'fname', 'first', 'given name', 'givenname',
    },
    'last_name': {
        'last name', 'lastname', 'lname', 'last', 'surname', 'family name', 'familyname',
    },
    'email': {
        'email', 'email address', 'emailaddress', 'e-mail', 'e mail', 'mail',
        'emailaddr', 'address (email)',
    },
    'phone': {
        'phone', 'phone number', 'phonenumber', 'phone#', 'mobile', 'mobile number',
        'cell', 'cell phone', 'cellphone', 'telephone', 'tel', 'tel#',
    },
    'address': {
        'address', 'street', 'street address', 'streetaddress', 'addr',
        'address line 1', 'address1', 'addr1', 'mailing address',
    },
    'city': {
        'city', 'town', 'locality',
    },
    'state': {
        'state', 'province', 'region', 'st', 'state/province',
    },
    'zip': {
        'zip', 'zip code', 'zipcode', 'postal', 'postal code', 'postalcode',
        'postcode', 'post code',
    },
}


def normalize_header(h: str) -> str:
    """Lowercase, collapse whitespace + punctuation, used for synonym match."""
    if h is None:
        return ''
    s = str(h).strip().lower()
    s = re.sub(r'[_\-]+', ' ', s)
    s = re.sub(r'\s+', ' ', s)
    return s


def suggest_mapping(headers: list) -> dict:
    """Map each header → standard field name if any synonym matches."""
    out = {}
    used = set()
    for h in headers:
        n = normalize_header(h)
        match = None
        for std, synonyms in STANDARD_FIELD_SYNONYMS.items():
            if std in used:
                continue  # don't double-map two file columns to the same standard slot
            if n in synonyms:
                match = std
                break
        if match:
            out[h] = match
            used.add(match)
    return out


def _detect_xlsx_headers(stream) -> dict:
    """Read only enough of an xlsx to capture headers + first SAMPLE_ROWS rows.

    openpyxl(read_only=True) iterates without loading the full sheet.
    """
    # openpyxl needs a file-like with seek support; buffer just the bytes we
    # streamed so far. xlsx is a zip — we can't reasonably partial-read it
    # the way we can with CSV, so we accept the bandwidth cost here.
    raw = stream.read()
    wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    headers, sample_rows = [], []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            headers = ['' if v is None else str(v).strip() for v in row]
        else:
            sample_rows.append(['' if v is None else str(v) for v in row])
        if i >= SAMPLE_ROWS:
            break
    wb.close()
    return {
        'format': 'xlsx',
        'delimiter': None,
        'headers': headers,
        'sample_rows': sample_rows,
    }


def _detect_delimited_headers(sample_text: str) -> dict:
    """Sniff CSV/TSV/pipe-delimited, return headers + first rows."""
    try:
        dialect = csv.Sniffer().sniff(sample_text[:8192], delimiters=',\t|;')
        delimiter = dialect.delimiter
    except csv.Error:
        # Sniffer can't always decide. Vote with simple counts on the first line.
        first_line = sample_text.splitlines()[0] if sample_text else ''
        counts = {d: first_line.count(d) for d in ',\t|;'}
        delimiter = max(counts, key=counts.get) if any(counts.values()) else ','

    reader = csv.reader(io.StringIO(sample_text), delimiter=delimiter)
    rows = []
    for i, row in enumerate(reader):
        rows.append(row)
        if i >= SAMPLE_ROWS:
            break
    headers = [h.strip() for h in rows[0]] if rows else []
    sample_rows = rows[1:1 + SAMPLE_ROWS] if len(rows) > 1 else []

    fmt = {'\t': 'tsv', '|': 'pipe', ';': 'csv', ',': 'csv'}.get(delimiter, 'csv')
    return {
        'format': fmt,
        'delimiter': delimiter,
        'headers': headers,
        'sample_rows': sample_rows,
    }


def detect_from_s3(key: str, filename: Optional[str] = None) -> dict:
    """Read a small head of the file from Spaces, sniff format, return mapping.

    XLSX: needs the whole file (zip format), so we download it in full.
          For 100+ MB xlsx this is slow — recommend users send CSV/TSV when
          possible. We still cap memory by streaming into a BytesIO.
    CSV/TSV/TXT: only the first 64 KB is read.
    """
    name = (filename or key).lower()

    body = storage.get_object_stream(key)
    try:
        if name.endswith('.xlsx'):
            result = _detect_xlsx_headers(body)
        else:
            chunk = body.read(SAMPLE_BYTES_FOR_SNIFF)
            text = chunk.decode('utf-8', errors='replace')
            result = _detect_delimited_headers(text)
    finally:
        body.close()

    result['suggested_mapping'] = suggest_mapping(result['headers'])
    return result
