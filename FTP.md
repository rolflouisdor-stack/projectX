# EmailOversight FTP (Bulk Validation) Integration

How the mailer connects to EmailOversight's FTP dropbox for email validation,
and how we automate the round trip. **Every** scrub job goes through this
path — the real-time `/api/emailvalidation` REST endpoint was evaluated and
dropped (see "Why FTP-only" below).

> **Status (2026-06-03): EO ANSWERED — unblocked, building.** All 5 questions
> resolved (see §10) and the `ValidationStatusId` code table is in §12. Product
> decision: the scrub pipeline is being refactored to upload → confirm-email-col
> → quote (per-record × margin) → **pay (stub for now)** → submit to EO → poll →
> retrieve cleaned file → presigned download. The mock-scrub engine
> (`scrub_engine.run_mock_scrub_on_records`) + per-unique pricing + DB-record
> import are being removed.
>
> **EO answers, summarized:**
> 1. **SFTP** = same host, but EO must whitelist OUR egress IP(s) to enable it.
>    DO App Platform egress is **not static**, so SFTP needs a static egress
>    first (dedicated egress IP / NAT droplet). **Use plain FTP (port 21) for
>    now** — cleartext PII; matches the existing CX3-ops usage on the same
>    `cx3ads` account. Code stays config-switchable (`EO_FTP_TLS`).
> 2. **No completion signal.** Poll `processed/`, match our filename, confirm via
>    size-stable / mtime — exactly the §8b design.
> 3. **ValidationStatusId table → §12.** Keep/drop: DO-NOT-SEND = 2, 5, 6, 11;
>    SEND = 1; discretion = 3, 4, 7, 9, 10, 13. **Code 11 (Unknown) is NOT charged
>    by EO** (pricing nuance — billable count excludes code 11).
> 4. **Turnaround:** ~30 min / 100k, 3–4 h / 1M, 24–30 h / 10M → poller every few
>    min; `awaiting_ftp_result` timeout generous (≥ ~36 h).
> 5. **No max size / row count.** But EO processes **FIFO** — a large file posted
>    first blocks smaller files queued behind it. No split logic needed.
>
> **Still needed before go-live:** EO per-record **price** (for the quote math)
> and the **FTP password** (add as encrypted `EO_FTP_PASSWORD` in DO).

---

## 1. Verified facts (probed 2026-05-29)

| Thing | Value |
|---|---|
| Host | `ftp.emailoversight.com` (resolves `8.45.59.12`) |
| Protocol | **Plain FTP, port 21.** Explicit FTPS (`AUTH TLS`) **rejected** — server has no TLS. |
| Mode | Passive (PASV/EPSV) — required from behind NAT / DO App Platform. |
| Username | `cx3ads` |
| Upload (RAW) dir | `/cx3ads/` (the account root folder) |
| Results dir | `/cx3ads/processed/` |
| Result naming | `{uploaded-name-without-ext}-processed.csv` |
| Result columns | `Email,ValidationStatusId,ValidationStatus,EmailDomainGroupId,EmailDomainGroup,BounceMessage` (+ any original columns preserved) |

**The `processed/` folder is already in active production use by another
system** — it is full of `emailoversight_tovalidate{YYYYMMDDHHMMSS}_{id}-processed.csv`
files dropping ~every 30 min (almost certainly the CX3 ops system on :5050,
which shares this same `cx3ads` FTP account). **Consequence: this is a shared
dropbox.** The mailer must:
- use filenames that **cannot collide** with the existing `emailoversight_tovalidate*` pattern,
- **only ever read/download files it created**, identified from our DB,
- **never bulk-delete** or touch files it doesn't own.

---

## 2. Why FTP-only (no REST API path)

The product shape is upload-a-file / get-a-cleaned-file, so async submit-and-
poll fits every job regardless of size. The real-time API would only have
served a hypothetical small-list path that doesn't exist in the product, and it
would have introduced a per-account ListId provisioning step (manual or via an
EO endpoint EO doesn't publicly expose). Cutting it removes a whole integration
surface — no API token to manage, no per-customer ListId scheme, no dual
codepath in the worker. One way in, one way out.

---

## 3. Security note (read before shipping)

Plain FTP sends **credentials and the full email list in cleartext**. The list
files are consumer PII. The existing CX3 process already accepts this, but for
the mailer we should:

1. **Ask EmailOversight whether SFTP (port 22) or implicit FTPS (990) is
   available.** Prefer it if so — the integration code should make the protocol
   a config switch so we can flip without a rewrite.
2. Keep the password **only in `.env` / platform secrets**, never in git, never
   in this doc, never in logs.
3. Restrict outbound: only the worker/poller dynos need FTP egress.

**Open ask to EO:** SFTP/FTPS availability. Until then we proceed on plain FTP.

---

## 4. Credentials & config

Add to `.env` (and `.env.example` with blanks), following the existing `S3_*`
style:

```ini
# EmailOversight bulk FTP
EO_FTP_ENABLED=false                       # master switch
EO_FTP_HOST=ftp.emailoversight.com
EO_FTP_PORT=21
EO_FTP_USER=cx3ads
EO_FTP_PASSWORD=                           # value from EO; secret; never commit
EO_FTP_TLS=false                           # flip to true if EO enables FTPS
EO_FTP_ROOT=cx3ads                         # upload (RAW) folder
EO_FTP_PROCESSED_DIR=processed             # results subfolder under root
EO_FTP_FILENAME_PREFIX=mailer-             # our namespace in the shared folder
```

Wire these into `config.py` next to the existing `S3_*` settings. The
pre-existing `EMAIL_VALIDATOR_*` env scaffold (`EMAIL_VALIDATOR_API_KEY`,
`EMAIL_VALIDATOR_PROVIDER=neverbounce`) is no longer needed and can be removed.

---

## 5. EmailOversight FTP contract (from EO + probe)

Upload rules:
- **RAW files → `/cx3ads/` (root).** No limit on count/frequency.
- **CSV / Excel must contain a header literally named `email`.** All other
  columns are preserved into the processed file.
- **TXT must be a single column of emails with NO header.** TXT comes back as
  CSV (EO appends columns).
- Uploaded RAW file is **deleted by EO as soon as it's processed.**

Result rules:
- Processed file appears at **`/cx3ads/processed/{name}-processed.csv`**, always
  CSV regardless of input format.
- Output columns: `Email, ValidationStatusId, ValidationStatus,
  EmailDomainGroupId, EmailDomainGroup, BounceMessage` (original columns
  preserved alongside).
- `ValidationStatusId` is the numeric verdict; `1 = Verified`. We need EO's full
  code table to decide which IDs are "keep" vs "drop" — see Open Questions.
- Results also appear in the EO web UI under the single list **labeled "FTP"**,
  retained **6+ months** (but not forever).

The FTP path is **list-less from our side** — we do NOT pass a ListId,
everything lands under EO's single "FTP" list. **Correlation is done entirely
by filename.**

---

## 6. Filename = correlation key

We control the uploaded filename, so it carries our identity. Format:

```
{EO_FTP_FILENAME_PREFIX}sj{scrub_job_id}-{brand_slug}-{YYYYMMDD-HHMMSS}.csv
  e.g.  mailer-sj4821-brandface-20260601-153022.csv
```

EO then produces:

```
mailer-sj4821-brandface-20260601-153022-processed.csv     (in /cx3ads/processed/)
```

Why each piece is there:
- **`mailer-` prefix** guarantees no collision with the existing
  `emailoversight_tovalidate*` files in the shared folder.
- **`sj{job_id}`** is the durable, machine-stable correlation key — `scrub_jobs.id`
  is immutable and unique, so the poller maps a processed file straight back to
  its row by equality, no fuzzy matching.
- **`{brand_slug}`** is a slugified `mailer_company.name`
  (`[a-z0-9-]`, max ~32 chars, fallback `co{company_id}` if empty/unmappable).
  This makes the file human-scannable in EO's shared `processed/` folder and the
  "FTP" list in their UI — useful for support and debugging.
- **`{YYYYMMDD-HHMMSS}`** is the submit time, so chronological order is obvious
  at a glance.

Store the exact submitted name on the job (see schema below) so matching is an
equality check, not a guess.

---

## 7. Schema additions (`scrub_jobs`)

```
ftp_submitted_filename   VARCHAR(255)   -- exact RAW name we PUT to /cx3ads/
ftp_processed_filename   VARCHAR(255)   -- name we expect/found in processed/
ftp_submitted_at         DATETIME
ftp_result_ingested_at   DATETIME       -- idempotency guard
```

New `status` enum values inserted into the existing lifecycle:

```
uploaded -> awaiting_mapping -> submitting_ftp -> awaiting_ftp_result
         -> ingesting_result -> scrubbing -> priced -> awaiting_payment
         -> paid -> complete
```

The `importing` / `validating` states from the old single-path lifecycle are
gone — we no longer import the *raw* upload into the DB; the DB import happens
on the *processed* file (see 8c).

---

## 8. End-to-end automation

```
browser ──presigned multipart PUT──▶ Spaces (uploads/{company}/{job}/…)   [EXISTS]
                                        │
                                        ▼
   [WORKER A: submit]  Spaces ──stream──▶ FTP /cx3ads/mailer-sj{job}-{brand}-{ts}.csv
                                        │   (ensure `email` header on the way)
                                        │   status: submitting_ftp -> awaiting_ftp_result
                                        ▼
                       EO processes, deletes RAW, writes
                       /cx3ads/processed/mailer-sj{job}-{brand}-{ts}-processed.csv
                                        │
   [POLLER: scheduled every N min] ─────┤ list processed/, match OUR expected names
                                        ▼
                       FTP processed file ──stream──▶ Spaces results/{company}/{job}/
                                        │   status: ingesting_result
                                        ▼
   [WORKER B: ingest]  parse processed CSV → scrub_job_records
                       (ValidationStatusId → is_valid / invalid_reason)
                                        ▼
                       overlap check + pricing (existing scrub_engine steps 3–5)
                                        ▼
                       xlsx_generator → Spaces → status: priced -> … -> complete
```

The pipeline **imports the *processed* file, not the raw file** — one import,
and the records arrive already carrying EO's verdict.

### 8a. Submit worker (Spaces → FTP)

Responsibilities:
1. Build the upload name (`mailer-sj{job_id}-{brand_slug}-{ts}.csv`), persist it
   to `ftp_submitted_filename`, derive and persist
   `ftp_processed_filename = {stem}-processed.csv`.
2. Stream the file from Spaces to FTP **ensuring the `email` header rule**:
   - CSV/TSV: read the header row, find the email column (auto-detected via
     `header_detector`, customer-confirmed only if ambiguous), rename it to
     exactly `email`, stream the rest unchanged.
   - XLSX: convert to CSV on the fly (reuse the `openpyxl read_only` row
     streaming already in `import_worker._row_iter_xlsx`), writing `email` as the
     email column header.
   - TXT: strip any header; emit single email column.
3. Use **passive mode**; large files stream (no full buffering).
4. Set status `awaiting_ftp_result`, stamp `ftp_submitted_at`.

Reference (illustrative — not yet added to the app):

```python
import ftplib
from app.services import storage

def _connect(cfg):
    if cfg["tls"]:
        ftp = ftplib.FTP_TLS(); ftp.connect(cfg["host"], cfg["port"], timeout=60)
        ftp.login(cfg["user"], cfg["password"]); ftp.prot_p()
    else:
        ftp = ftplib.FTP(); ftp.connect(cfg["host"], cfg["port"], timeout=60)
        ftp.login(cfg["user"], cfg["password"])
    ftp.set_pasv(True)
    return ftp

def submit_to_ftp(job, cfg):
    body = storage.get_object_stream(job.s3_key)        # botocore StreamingBody
    src = _email_header_normalizer(body, job)           # generator → file-like of bytes
    ftp = _connect(cfg)
    try:
        ftp.cwd(cfg["root"])                            # /cx3ads
        ftp.storbinary(f"STOR {job.ftp_submitted_filename}", src, blocksize=1 << 20)
    finally:
        ftp.quit()
```

### 8b. Poller (scheduled)

Runs every few minutes — **not** a per-job blocking wait (turnaround is minutes
to hours). Add as an rq-scheduler periodic job or a cron component in
`.do/app.yaml`.

```python
def poll_ftp_results(cfg):
    db = get_db()
    waiting = db.query(ScrubJob).filter(
        ScrubJob.status == "awaiting_ftp_result",
        ScrubJob.ftp_result_ingested_at.is_(None),
    ).all()
    if not waiting:
        return
    expected = {j.ftp_processed_filename: j for j in waiting}

    ftp = _connect(cfg)
    try:
        ftp.cwd(f"{cfg['root']}/{cfg['processed_dir']}")
        names = set(ftp.nlst())                      # only OUR names matter
        for fname in expected.keys() & names:
            job = expected[fname]
            if not _is_settled(ftp, fname):          # partial-write guard
                continue
            result_key = storage.result_key(job.company_id, job.id, fname)
            _stream_ftp_to_spaces(ftp, fname, result_key)   # download → Spaces
            job.result_s3_key = result_key
            job.status = "ingesting_result"
            job.ftp_result_ingested_at = datetime.utcnow()
            db.commit()
            enqueue_ingest(job.id)                    # hand to Worker B
    finally:
        ftp.quit()
```

**Completion / partial-write guard (`_is_settled`):** EO gives no `.done`
marker, so a processed file could be mid-write when we list it. Treat a file as
ready only if its `SIZE` is unchanged across two consecutive polls (or its mtime
is older than ~60s). Confirm with EO whether there's a safer completion signal.

### 8c. Ingest worker (processed CSV → records → price)

- Stream the processed CSV from Spaces.
- Map `ValidationStatusId` → `is_valid` / `invalid_reason` using EO's code table
  (drop invalid/disposable/role/etc.). This **replaces the stubbed validation**
  in `scrub_engine.run_mock_scrub_on_records`.
- Run the existing overlap check + pricing, generate the xlsx, flip to `priced`.

---

## 9. Idempotency & shared-folder etiquette

- **Match only our files** (`ftp_processed_filename` equality). Never act on the
  `emailoversight_tovalidate*` files — they belong to the other system.
- **Do not delete** anything in `processed/`. EO retains results 6+ months and
  the other system + EO UI rely on them. Idempotency comes from
  `ftp_result_ingested_at`, not from deleting the source.
- Re-running the poller after a crash is safe: already-ingested jobs are filtered
  out by the `is_(None)` guard.
- Our RAW upload is auto-deleted by EO after processing, so no upload cleanup
  needed. If a job sits in `awaiting_ftp_result` past a timeout (e.g. 12h) with
  no processed file, mark it `failed` / alert and allow resubmit.

---

## 10. Open questions for EmailOversight

(These mirror the 5 questions in the outbound email to EO.)

1. **SFTP or FTPS available?** (We're on cleartext FTP today.)
2. **Completion signal** — any marker file / manifest / webhook, or is "appears
   in `processed/` and size is stable" the only signal?
3. **Full `ValidationStatusId` code table** — which IDs count as deliverable
   (keep) vs drop? (We have `1 = Verified`; need the rest, incl. how `0 = Retry`
   should be handled in a bulk workflow.)
4. **Turnaround SLA** for a file of N records (sets the poller cadence + the
   `awaiting_ftp_result` timeout).
5. **Max file size** / any split requirement.

---

## 11. Safe testing plan

- Use a **tiny** CSV with a unique throwaway name
  (`mailer-selftest-{uuid}.csv`, a handful of test emails) so we never disturb
  the shared folder or the other system's files.
- Verify: upload lands in `/cx3ads/`, disappears after processing, and
  `mailer-selftest-{uuid}-processed.csv` shows up in `processed/` with the
  expected columns.
- Confirm passive mode works from the DO worker environment (egress to port 21
  + the passive port range).
- Keep `EO_FTP_ENABLED=false` in prod until the poller + ingest are validated end
  to end in dev.

---

## 12. ValidationStatusId code table (from EO, 2026-06-03)

Source: EmailOversight "Status Definitions and Best Practices.pdf". The processed
file's `ValidationStatusId` column maps to:

| Code | Status | EO guidance | Keep/drop (our default) |
|---|---|---|---|
| 1  | Verified      | SEND                | **keep** |
| 2  | Undeliverable | DO NOT SEND         | **drop** |
| 3  | Catch-All     | send at discretion  | keep (discretion) |
| 4  | Role          | send at discretion  | keep (discretion) |
| 5  | Malformed     | DO NOT SEND         | **drop** |
| 6  | Spam Trap     | DO NOT SEND         | **drop** |
| 7  | Complainer    | send at discretion  | keep (discretion) |
| 9  | Bot           | send at discretion  | keep (discretion) |
| 10 | Seed Account  | send at discretion  | keep (discretion) |
| 11 | Unknown       | DO NOT SEND         | **drop** — *EO does not charge for this code; reverify after 72h* |
| 13 | Disposable    | send at discretion  | keep (discretion) |

Notes:
- **Hard drops:** 2, 5, 6, 11. **Always keep:** 1. **Discretion** (3, 4, 7, 9,
  10, 13) — keep by default; could be a user-configurable toggle later.
- **Billing:** EO doesn't charge for code 11 (Unknown). We charge the user
  upfront on total record count, so unknowns are effectively extra margin / a
  reconciliation line — note when wiring real billing.
- **Filtering is a product choice (TBD):** hand EO's annotated `-processed.csv`
  to the user as-is (they get every row + the status columns), OR drop the
  hard-drop codes before serving. Default leaning: deliver as-is for v1 (simplest,
  fully transparent), add an optional "remove undeliverable" toggle later.
