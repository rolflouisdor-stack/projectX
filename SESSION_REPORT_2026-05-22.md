# Session Report — Mailer Portal

**Date:** 2026-05-22
**Project:** `/Users/rolf.louisdor/Desktop/mailer/`
**Related spec bump:** `Partner_portals_Spec.md` → V1.3

Captures every change made this session. Read top-to-bottom for the narrative, or jump to a section.

---

## Headline outcomes

1. **The scrub upload pipeline is real, end-to-end.** Files no longer touch the local filesystem. Browser → presigned multipart PUT → DigitalOcean Spaces (or any S3-compatible target) → background RQ worker → DB → Spaces → presigned GET. Verified with both curl and the live browser UI against a local MinIO instance.
2. **Files can have any header structure.** New "Map columns" step in the scrub wizard detects each header, suggests standard-field mappings via a synonym table, and lets the user assign unmapped headers as custom fields. Standard fields (`first_name, last_name, email, phone, address, city, state, zip`) become indexed columns; custom fields (`car_model`, `year`, anything else) land in an EAV table.
3. **No more `/tmp/` for artifacts.** Result `.xlsx` is generated from the real imported records and pushed to Spaces. Downloads are 302 → presigned GET with a 1-hour TTL. `Partner_portals_Spec.md` §6.4 flipped from TODO to done.
4. **Background worker process is live.** RQ + Redis. Drains a `mailer-default` queue. Procfile and `.do/app.yaml` updated with a second `worker` component. Local dev needs three processes (web, worker, Redis).
5. **Local dev parity with MinIO.** No DigitalOcean account needed to exercise the full path; MinIO on `:9000` substitutes for Spaces.

---

## 1. Data model — three new tables + scrub_jobs gets 9 columns

The single biggest piece of work. The original spec stubbed the scrub pipeline; this session made the data layer real without locking into a per-tenant-table anti-pattern.

### What was decided

Reviewed in conversation: per-user dynamic tables (the original idea) were rejected because they require `ALTER TABLE` on every upload, expose SQL-injection surface via user-controlled column names, and produce per-tenant schema sprawl. Instead, **one shared schema** holds parsed rows; standard fields are fixed indexed columns; custom fields live in an EAV (Entity-Attribute-Value) side table. Fast reads on the standard set, total flexibility for anything else.

### Schema changes (applied to `gravitas_mailer` MySQL on this machine via `migrations.py`)

```
scrub_jobs (EXISTING, extended)
  + s3_bucket VARCHAR(120)
  + s3_key VARCHAR(500)
  + multipart_upload_id VARCHAR(255)
  + original_filename VARCHAR(255)
  + file_size_bytes BIGINT
  + content_type VARCHAR(120)
  + detected_headers_json JSON
  + delimiter VARCHAR(8)
  + result_s3_key VARCHAR(500)
  status enum widened: + uploading + awaiting_mapping + importing  (12 values total)

scrub_job_field_mappings (NEW)
  scrub_job_id, source_header, column_index, target_field,
  is_standard, skip
  UNIQUE (scrub_job_id, source_header)

scrub_job_records (NEW)
  scrub_job_id, company_id, row_index,
  first_name, last_name, email, email_normalized,
  phone, address, city, state, zip,
  is_valid, invalid_reason, is_unique
  INDEX (scrub_job_id), INDEX (email_normalized), INDEX (company_id)

scrub_job_record_fields (NEW)         ← EAV
  record_id, scrub_job_id (denormalized), field_name, value_text
  INDEX (record_id), INDEX (scrub_job_id, field_name)
```

### How migrations apply automatically

`app/services/migrations.py` is called from `init_db()` after `Base.metadata.create_all`. It inspects the live DB and applies missing ALTERs idempotently. No Alembic/Flask-Migrate added — overkill for the current scope, but easy to layer on later if the project needs it.

### Verified

- `mysql ... DESCRIBE scrub_jobs;` shows all 9 new columns and the 12-value status enum.
- `SHOW TABLES;` includes the three new tables.
- Re-running `init_db()` is a no-op on the second boot (inspector finds the columns).

---

## 2. Object storage — DigitalOcean Spaces via boto3 (MinIO for local dev)

`app/services/storage.py` wraps boto3 against an S3-compatible endpoint:

- `initiate_multipart_upload(key, content_type)` — returns `UploadId`
- `presign_part_url(key, upload_id, part_number)` — one URL per ~10 MB part
- `complete_multipart_upload(key, upload_id, parts)` — finalize after the browser PUTs every part
- `abort_multipart_upload(...)` — best-effort cleanup
- `put_object`, `get_object_stream`, `head_object` — for the result xlsx
- `presign_get_url(key, filename=...)` — short-TTL download links

Key layout (used by both the upload-init flow and the result generator):

```
uploads/{company_id}/{job_id}/{uuid}-{filename}        ← original upload
results/{company_id}/{job_id}/{filename}.xlsx          ← generated artifact
```

### Why the browser uploads directly, not through Flask

A 500 MB upload streamed through gunicorn would exceed App Platform's HTTP timeout, blow up memory on small dynos, and double the bandwidth bill (in then out). With presigned PUTs the bytes go straight to Spaces. Flask only sees JSON requests carrying the file's *metadata* + a list of part ETags after the fact.

### The CORS gotcha

For the browser-side multipart upload to work, the Spaces bucket needs `ExposeHeaders: ETag` in its CORS config. Without it the browser successfully PUTs each part but `fetch().headers.get('ETag')` returns null, and `/upload-complete` rejects an empty parts list. Documented in `DEPLOYMENT.md` §7b. The wizard JS error message names this explicitly so the next person doesn't have to chase it from a blank "missing ETag" symptom.

### Env vars added

`S3_ENDPOINT_URL`, `S3_REGION`, `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, plus tunables for part size and presign TTL. All added to `.env`, `.env.example`, `config.py`, and `.do/app.yaml`. Wired identically into the new `worker` component because the import worker needs storage access too.

---

## 3. Background worker — RQ + Redis (with one macOS-specific tweak)

Reasons for queueing: parsing a 100 MB CSV is ~30–60 s of CPU, well past gunicorn's HTTP timeout. The web process needs to return immediately after the user confirms the mapping.

### Layout

```
app/jobs/
  __init__.py           docstring only
  queue.py              lazy Redis connection + enqueue_import(job_id)
  import_worker.py      RQ entrypoint (run_import); streams file, builds records
  run_worker.py         standalone process; the Procfile `worker:` line points here
```

`enqueue_import` is called by the new `POST /api/scrub-jobs/{id}/mapping` endpoint right after committing the mapping rows. The worker then:

1. Opens a Flask app context (so SQLAlchemy + config work as they would in a request).
2. Streams the file from Spaces — CSV/TSV/pipe line-by-line (`csv` module), XLSX via `openpyxl(read_only=True)`.
3. Applies each saved mapping per row: standard mappings become column values on `ScrubJobRecord`, custom mappings become rows in `ScrubJobRecordField`.
4. Commits in batches of 1,000 rows. Updates `uploaded_count` after each batch so the polling UI shows progress.
5. Calls `scrub_engine.run_mock_scrub_on_records(job)` to mutate `is_valid` / `is_unique` and compute `unique_count`, `overlap_count`, `price_cents`.
6. Logs an `ACTION_RUN_SCRUB` activity (replacing the entry the old synchronous endpoint emitted).

### The macOS SimpleWorker switch

**Symptom** during the first test run: every enqueued job ended up in `rq:failed:mailer-default` with `"Work-horse terminated unexpectedly; waitpid returned 6 (signal 6)"`. Worker log:

```
objc[42020]: +[NSNumber initialize] may have been in progress in another thread
when fork() was called. We cannot safely call it or ignore it in the fork() child
process. Crashing instead.
```

**Cause:** RQ's default `Worker` forks a child "work-horse" process for each job. boto3 transitively imports `libobjc` (via `requests` → `urllib3` → macOS SSL); on macOS, once an Objective-C class is initialized, forking and continuing to use it crashes.

**Fix considered but rejected:** set `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES`. This env var has to be in the environment *before* dyld loads libobjc; setting it from inside Python is too late.

**Fix shipped:** switched `run_worker.py` from `Worker` to `SimpleWorker`, which executes jobs in the worker process itself with no fork. Trade-off is that a job crash takes down the worker process; on App Platform the process supervisor restarts it within seconds. For the current scope (one worker, one job type, modest scale) this is the right call. Documented in the worker file's own comment.

### Procfile + .do/app.yaml

```diff
 web: gunicorn ... run:app
+worker: python -m app.jobs.run_worker
```

In `.do/app.yaml`, a second component named `worker` mirrors all relevant env vars (S3 + Redis come from the same managed services as the web component). A new `mailer-redis` Managed Database entry sits next to `mailer-db`.

---

## 4. API surface — old multipart endpoint out, six new ones in

Old single-shot:

```diff
-POST /api/scrub-jobs                     (multipart/form-data file upload, synchronous scrub)
```

New flow (each step is a self-contained request; the wizard JS drives them in order):

```diff
+POST /api/scrub-jobs/upload-init         { filename, size, content_type, cleaning }
+                                         → { job_id, upload_id, s3_key, part_size, parts:[{PartNumber,url}] }
+POST /api/scrub-jobs/{id}/upload-complete{ parts:[{PartNumber,ETag}] }
+                                         → job (status = awaiting_mapping)
+POST /api/scrub-jobs/{id}/detect-headers → { headers, sample_rows, suggested_mapping, standard_fields, delimiter, format }
+POST /api/scrub-jobs/{id}/mapping        { mappings:[{source_header,column_index,target_field,is_standard,skip}] }
+                                         → enqueues import; status = importing
 GET  /api/scrub-jobs/{id}                (existing; the wizard polls until status ∈ {priced, failed})
 POST /api/scrub-jobs/{id}/pay            (existing; now writes result xlsx to Spaces)
~GET  /api/scrub-jobs/{id}/download       (now 302 → presigned GET)
+GET  /api/scrub-jobs/{id}/download-url   (JSON variant for the wizard)
```

`purchase_jobs` got the same `/download` + `/download-url` treatment for parity.

---

## 5. Header detection + synonym matcher

`app/services/header_detector.py`. Two pieces:

**Format sniff** — reads ~64 KB of the upload (or the whole file for XLSX, since the zip format doesn't permit partial reads). `csv.Sniffer` picks the delimiter for delimited text; openpyxl handles XLSX. Returns `{format, delimiter, headers, sample_rows: [first 5 rows]}`.

**Synonym matcher** — `STANDARD_FIELD_SYNONYMS` maps each of the 8 standard fields to a set of normalized variations (`email_address`, `e-mail`, `mail`, `emailaddr` all → `email`). Headers are normalized (lowercase, collapse `_`/`-` to spaces, collapse whitespace) before lookup. Adds liberally — false positives cost one click in the UI; false negatives cost more. The user can override every suggestion.

Tested with: `['EMAIL', 'fname', 'last name', 'phone#', 'street address', 'state', 'zipcode', 'car_model', 'year']` → 7 correct standard-field hits, `car_model` and `year` correctly left for custom mapping.

---

## 6. Wizard UI — 5 steps became 6

`app/templates/scrub.html` rewritten. Stepper bar at the top now reads:

```
1 Upload list → 2 Map columns → 3 Processing → 4 Review → 5 Payment → 6 Download
```

**Step 1 (Upload list)** — same file-picker, but the "Start scrub" button now drives the three-call multipart flow: `upload-init` → parallel PUTs to Spaces (one per part, with a live progress bar reading bytes) → `upload-complete` → `detect-headers`. The .accept attribute now allows `.csv, .tsv, .txt, .xlsx`.

**Step 2 (Map columns) — NEW.** Renders one row per detected header. Columns:

- *Your column* — the header verbatim
- *Sample values* — first 3 non-empty cells from the sample rows
- *Maps to* — `<select>` listing the 8 standard fields, plus "Custom field…" and "— Skip this column —"
- *Custom name* — text input, only visible when "Custom field…" is selected; pre-filled with a `snake_case` version of the header
- *Skip* — checkbox shortcut

Client-side validation: standard fields can't be mapped twice (e.g. two columns to `email`); if no column is mapped to `email` the user gets a "are you sure?" confirm (records with no email can't be scrubbed). On submit, posts to `/mapping` and advances to step 3.

**Step 3 (Processing)** — now polls `/api/scrub-jobs/{id}` every 1.5 s instead of running a fake animation. Stage highlighting tracks real status transitions (`importing → validating → scrubbing → priced`). The status enum's new states map cleanly to stages.

**Steps 4–6** — Review / Payment unchanged in logic. Download now calls `/download-url` and points the button at the returned presigned URL.

---

## 7. xlsx_generator.py — reads real records, writes to Spaces

Previously generated fake rows in memory and wrote to `/tmp/`. Now:

1. Loads the user's saved mappings (filtered to `skip=false`).
2. Builds the workbook column list from the mappings — standard fields first (in mapping order), custom fields next. Skipped columns are omitted entirely.
3. Streams `is_valid=true AND is_unique=true` rows from `scrub_job_records` in batches of 1,000, joining the EAV `scrub_job_record_fields` per batch.
4. Uses `openpyxl.Workbook(write_only=True)` so the workbook never holds the full sheet in memory.
5. PUTs the bytes to `results/{company_id}/{job_id}/{filename}.xlsx` via `storage.put_object`.

Returns `(filename, s3_key)`. The caller stores both on `scrub_jobs.{result_filename, result_s3_key}`.

The purchase artifact path got the same Spaces treatment for parity, even though `purchase_jobs` still uses stubbed record generation pending real data integration.

---

## 8. Local dev setup added — Redis + MinIO

Two new services to install on the dev machine. Both via Homebrew.

```bash
# Redis (queue backend)
brew install redis
brew services start redis

# MinIO (S3-compatible local object storage)
brew install minio/stable/minio
brew install minio/stable/mc
mkdir -p ~/minio-data
MINIO_ROOT_USER=minioadmin MINIO_ROOT_PASSWORD=minioadmin123 \
  minio server ~/minio-data --address ":9000" --console-address ":9001" \
  > /tmp/minio.log 2>&1 &

# Create the dev bucket
mc alias set local http://127.0.0.1:9000 minioadmin minioadmin123
mc mb local/gravitas-mailer-dev
```

`.env` additions (already applied on this machine):

```
S3_ENDPOINT_URL=http://127.0.0.1:9000
S3_REGION=us-east-1
S3_BUCKET=gravitas-mailer-dev
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin123
REDIS_URL=redis://localhost:6379/0
RQ_QUEUE=mailer-default
```

Two long-running processes now (in addition to MySQL / Redis / MinIO):

```bash
PYTHONPATH=./lib python3 run.py > /tmp/mailer.log 2>&1 &
PYTHONPATH=./lib python3 -m app.jobs.run_worker > /tmp/mailer_worker.log 2>&1 &
```

The MinIO admin console is at http://127.0.0.1:9001 — useful for poking at uploaded objects to confirm bytes landed.

---

## 9. End-to-end test — verified

| Step | Result |
|---|---|
| 1 `/upload-init` | Job #7 created in MySQL; multipart upload `NjNkZDQx...` opened on MinIO; 1 presigned PUT URL returned. |
| 2 Browser PUT to MinIO | 768-byte CSV uploaded to `gravitas-mailer-dev/uploads/1/7/77c5...-test_list.csv`; ETag `cc895cb...` returned. |
| 3 `/upload-complete` | MultipartUpload finalized; job status → `awaiting_mapping`. |
| 4 `/detect-headers` | 7 headers parsed. 5 auto-suggested to standard fields (`First Name`→`first_name`, `Email Address`→`email`, `Phone`→`phone`, `Zip Code`→`zip`, `Last Name`→`last_name`). `car_model` + `year` correctly left unmapped. |
| 5 `/mapping` (7 rows posted) | Mapping rows persisted; RQ job `378c7032-...` enqueued. |
| 6 Worker drained | 10 records inserted into `scrub_job_records`; 20 EAV rows into `scrub_job_record_fields` (10 × `car_model`, 10 × `year`); scrub engine bucketed 4 unique, 6 overlap; status → `priced`. |
| 7 `/pay` | Stripe stub charged; result xlsx (4 unique rows × 7 columns) written to MinIO at `results/1/7/gravitas_scrub_7_20260522.xlsx`. Job status → `complete`. |
| 8 `/download-url` | Presigned GET URL returned with 1-hour expiry. |
| 9 Download | File pulled via signed URL; `file /tmp/result.xlsx` confirms `Microsoft Excel 2007+`. Content matches: header row `first_name, last_name, email, phone, zip, car_model, year`, then 4 unique-bucketed records with their custom fields populated. |
| 10 Browser test | User confirmed: "looks great." |

### DB snapshot from the test

```
scrub_job_records (id, row_index, first_name, last_name, email, phone, zip, is_valid, is_unique):
   1  1  Alex      Smith     alex.smith@example.com       555-0101  10001  1  1
   2  2  Jordan    Lee       jordan.lee@example.com       555-0102  90210  1  0
   3  3  Casey     Brown     casey.brown@example.com      555-0103  60601  1  1
   ... etc, 10 rows total

scrub_job_record_fields (sample):
   record_id  field_name  value_text
   1          car_model   Honda Civic
   1          year        2019
   2          car_model   Toyota Camry
   2          year        2020
   ... 20 rows total

scrub_job_field_mappings (7 rows):
   First Name    → first_name (standard)
   Last Name     → last_name (standard)
   Email Address → email (standard)
   Phone         → phone (standard)
   Zip Code      → zip (standard)
   car_model     → car_model (custom)
   year          → year (custom)
```

---

## 10. Files changed this session

### New (10)

```
app/services/storage.py            boto3/Spaces wrapper
app/services/header_detector.py    format sniff + synonym matcher
app/services/migrations.py         idempotent boot-time ALTERs
app/jobs/__init__.py
app/jobs/queue.py                  Redis connection + enqueue_import
app/jobs/import_worker.py          run_import (called by RQ)
app/jobs/run_worker.py             standalone process entrypoint
app/models/scrub_job_field_mapping.py
app/models/scrub_job_record.py
app/models/scrub_job_record_field.py
```

### Modified (12)

```
app/models/scrub_job.py            +9 cols, +3 status values
app/models/purchase_job.py         +result_s3_key
app/models/__init__.py             register 3 new models
app/services/scrub_engine.py       rewritten to operate on real records
app/services/xlsx_generator.py     reads records, writes to Spaces
app/api/routes.py                  -1 endpoint, +5 endpoints, rewrote /download
app/extensions.py                  run_migrations() after create_all
app/templates/scrub.html           5 → 6 steps, mapping UI, presigned PUTs
requirements.txt                   +boto3, +redis, +rq
Procfile                           +worker line
.do/app.yaml                       +worker component, +Managed Redis, +S3 envs
.env.example                       +S3_*, +REDIS_URL, +RQ_QUEUE
.env                               (local) same vars set for MinIO + local Redis
config.py                          +S3_*, +REDIS_URL, +RQ_QUEUE
lib/                               pip install --target ./lib boto3 redis rq
```

### Docs

```
Partner_portals_Spec.md            V1.2 → V1.3; §6.4 marked done; routes, schema,
                                   change log all updated
DEPLOYMENT.md                      +§7b "Object storage (Spaces) + worker"; env
                                   matrix grew by 11 rows
CONTEXT.md                         file map, env table, run instructions, §9
                                   pending list, all refreshed for V1.3
SESSION_REPORT_2026-05-22.md       (this file)
```

---

## 11. What's still pending (good next-session targets)

In rough priority order — same list as `CONTEXT.md §9`, just summarised here for the record:

1. **Real Stripe wiring** — `Partner_portals_Spec.md §6.1`.
2. **Real overlap matching** (`§6.3`). The import pipeline now writes real rows into `scrub_job_records`, so the engine has everything it needs — swap the random `unique_rate` bucketing in `scrub_engine.run_mock_scrub_on_records` for a hash-membership check against the Gravitas data.
3. **Admin UI** — 23 admin endpoints, no UI yet.
4. **Real email validation** (NeverBounce / ZeroBounce) — `§6.2`. Operate per-row on `scrub_job_records` inside the worker so the cleaning pass and the validation pass are in the same place.
5. **Rate limiting on `/api/auth/*`** — `§6.6`.
6. **First production deploy.**

---

## 12. Memory notes (saved for future sessions)

Added to `~/.claude/projects/.../memory/`:

- `feedback_rq_simpleworker_macos.md` — when boto3/requests is in scope on macOS dev, RQ's default forking `Worker` will crash with `objc[*]: +[NSNumber initialize]...` on every job. Use `SimpleWorker` (no fork) for dev; setting `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` from Python is too late.

If anything in this report ages — update `CONTEXT.md` and the spec doc, not this file. Session reports are point-in-time.
