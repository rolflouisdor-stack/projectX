# Gravitas Leads Mailer Portal — API Documentation

Generated 2026-06-04. Source of truth is the code in `app/` — regenerate if routes change.

- **Production base URL:** `https://mailer.gravitasleads.io`
- **Local dev:** `http://127.0.0.1:5070`
- **Framework:** Flask (app factory + blueprints), server-rendered Jinja pages + JSON API.

---

## Table of contents

1. [Conventions](#conventions)
2. [Authentication](#authentication)
3. [Server-rendered pages](#server-rendered-pages)
4. [Auth API](#auth-api) — `/api/auth/*`
5. [User API](#user-api) — `/api/*` (login required)
   - [Verticals & banner](#verticals--banner)
   - [Dashboard](#dashboard)
   - [Scrub jobs](#scrub-jobs)
   - [Purchase jobs](#purchase-jobs)
   - [Downloads](#downloads)
   - [Job history](#job-history)
6. [Account API](#account-api) — `/api/account/*`
7. [Internal API](#internal-api) — `/api/internal/*` (API-key or admin session)
8. [Admin API](#admin-api) — `/api/internal/admin/*` (API-key or admin session)
9. [Appendix: scrub job lifecycle & statuses](#appendix-scrub-job-lifecycle--statuses)

---

## Conventions

- **Requests/responses are JSON** unless noted (uploads go straight to object storage; downloads 302-redirect to a presigned URL).
- **Errors** use the shape `{ "error": "human-readable message" }` with an appropriate HTTP status (`400` bad input, `401` unauthenticated, `403` forbidden, `404` not found, `409` wrong state/conflict, `503` dependency/queue failure). A few validation errors add a `code` field (e.g. `missing_email_mapping`).
- **Money** is integer **cents** (`price_cents`); some payloads also include a rounded `*_dollars` convenience field.
- **Timestamps** are ISO-8601 UTC.
- **CORS preflight:** an app-level `OPTIONS /<any path>` handler answers preflight requests.

---

## Authentication

Three auth schemes:

| Scheme | Used by | How |
|---|---|---|
| **Session JWT** | All pages + `/api/*`, `/api/account/*` | `gm_session` cookie — an HS256 JWT signed with `SECRET_KEY`, set on signup/login. `httponly`, `secure`, `SameSite=Lax`. Sent automatically by the browser. |
| **Internal API key** | `/api/internal/*` and `/api/internal/admin/*` | `X-Internal-Api-Key: <key>` header (or `?api_key=<key>`) matched against `INTERNAL_API_KEY` (server-to-server, e.g. the CX3 Dashboard). |
| **None** | `/api/internal/health`, OPTIONS preflight, login/signup pages | Public. |

### Admin access (cross-company)

Platform admins get full cross-company read access to the **entire internal + admin API** (`/api/internal/*`, `/api/internal/admin/*`) **using their normal logged-in session** — no API key needed. An account is an admin if its email is in `ADMIN_EMAILS` (config, comma-separated; default `rolf.louisdor@cx3ads.com`). So an admin can log in at `/login` in the browser and then call e.g. `/api/internal/admin/overview` directly.

- Each internal/admin endpoint accepts **either** a valid `X-Internal-Api-Key` **or** an admin session — whichever is present.
- Non-admin sessions and anonymous callers get `401 {"error":"admin access required"}`.
- `GET /api/auth/me` returns `is_admin: true|false` so a client can decide whether to surface admin views.

On a protected `/api/*` call without a valid session the API returns `401 {"error":"not authenticated"}`; protected **pages** redirect to `/login`.

Payments are currently a **Stripe stub** (`create_payment_intent` always "succeeds"); `STRIPE_ENABLED=false`.

---

## Server-rendered pages

HTML pages (blueprint `views`, no prefix). All except `/login` and `/signup` require a session; unauthenticated requests redirect to `/login`.

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/` | — | Redirects to `/dashboard` (if logged in) or `/login`. |
| GET | `/login` | — | Login page. |
| GET | `/signup` | — | Signup page. |
| GET | `/dashboard` | session | Main dashboard. |
| GET | `/scrub` | session | Scrub/clean wizard. Accepts `?job=<id>` to resume. Renders the EmailOversight flow when `EO_FTP_ENABLED=true`, else the legacy mapping flow. |
| GET | `/buy` | session | Buy-records (purchase) page. |
| GET | `/jobs` | session | Job History (scrubs + purchases, with per-row actions incl. Delete). |
| GET | `/account` | session | Account settings page. |

---

## Auth API

Blueprint `auth`, prefix **`/api/auth`**.

### POST `/api/auth/signup`
Create a company (if new) + owner user, and start a session.
- **Body:** `{ full_name, company_name, email, phone?, password }` — password ≥ 8 chars.
- **Response:** `{ user, company }` + sets `gm_session` cookie.
- **Errors:** `400` missing/invalid field, `409` email already exists, `503` DB unavailable.

### POST `/api/auth/login`
- **Body:** `{ email, password }`
- **Response:** `{ user, company }` + sets `gm_session` cookie.
- **Errors:** `400` missing fields, `401` invalid credentials.

### POST `/api/auth/logout`
Clears the session cookie. **Response:** `{ ok: true }`.

### GET `/api/auth/me`
Current session identity. **Auth:** session. **Response:** `{ user, company, is_admin }`.

---

## User API

Blueprint `api`, prefix **`/api`**. All require a session.

### Verticals & banner

#### GET `/api/verticals`
Active verticals, each with its active `subcategories[]`.

#### POST `/api/verticals/<vertical_id>/track`
Logs that the user selected a vertical. **Response:** `{ ok: true }`. `404` if unknown.

#### GET `/api/promo-banner`
The single active promo banner (respecting `starts_at`/`ends_at`), or `null`.

### Dashboard

#### GET `/api/dashboard/summary`
**Response:** `{ recent: [ { kind, date, records, price_cents, status } ], total_spent_cents }`. Logs a `view_dashboard` event.

### Scrub jobs

Two flows share these endpoints, switched by `EO_FTP_ENABLED`:
- **EmailOversight (current):** upload → `confirm-email` → quote → `pay` → file sent to EO via FTP → polled → cleaned file delivered as-is.
- **Legacy mock-scrub:** upload → `mapping` → import → `pay` → result xlsx generated.

#### POST `/api/scrub-jobs/upload-init`
Create a draft job + open a multipart upload; returns presigned part URLs the browser PUTs to directly.
- **Body:** `{ filename, size, content_type?, cleaning? }`
- **Response:** `{ job_id, upload_id, s3_key, part_size, parts: [ { PartNumber, url } ] }`.
- **Errors:** `400` missing filename / size ≤ 0.

#### POST `/api/scrub-jobs/<job_id>/upload-complete`
Finalize the multipart upload.
- **Body:** `{ parts: [ { PartNumber, ETag } ] }`
- **Response:** the job, now `awaiting_mapping`.
- **Errors:** `400` no parts, `404`, `409` wrong status, `500` finalize failed.

#### POST `/api/scrub-jobs/<job_id>/detect-headers`
- **Response:** `{ job_id, format, delimiter, headers, sample_rows, suggested_mapping, standard_fields }`.
- **Errors:** `404`, `409` not `awaiting_mapping`, `422` parse failure.

#### POST `/api/scrub-jobs/<job_id>/confirm-email` *(EmailOversight flow)*
Confirm the email column; counts records + prices asynchronously.
- **Body:** `{ email_column_index: <int> }`
- **Response:** the job, now `importing` (→ `priced`; poll with GET).
- **Errors:** `400` missing/out-of-range, `404`, `409` not `awaiting_mapping`, `503` queue failure.

#### POST `/api/scrub-jobs/<job_id>/mapping` *(legacy flow)*
- **Body:** `{ mappings: [ { source_header, column_index, target_field, is_standard, skip } ] }` — at least one column must map to `email`.
- **Response:** the job, now `importing`.
- **Errors:** `400` (incl. `code: missing_email_mapping`), `404`, `409`, `500`.

#### GET `/api/scrub-jobs/<job_id>`
Fetch one job (company-scoped). Poll for status. `404` if not found/owned.

#### DELETE `/api/scrub-jobs/<job_id>`
Permanently delete a job: its Spaces objects (`uploads/<co>/<job>/` + `results/<co>/<job>/`) **and** the DB row (children cascade). Company-scoped.
- **Response:** `{ deleted: true, id, spaces_objects_deleted }`. `404` if not found/owned.

#### POST `/api/scrub-jobs/<job_id>/pay`
Stub payment + advance. From `priced`/`awaiting_payment`.
- **EO flow:** → `submitting_ftp`, enqueues FTP submit → poll → complete; sends "payment received" email.
- **Legacy flow:** guards kept columns + unique_count > 0 → `generating`, enqueues artifact build.
- **Errors:** `400`, `404`, `409`, `503`.

### Purchase jobs

#### POST `/api/purchase-jobs/quote`
- **Body:** `{ selected_verticals: [ { vertical_id, subcategory_ids? } ], volume, promo_code? }`
- **Response:** `{ rows, subtotal_cents, tier_discount_cents, promo_discount_cents, discount_cents, price_cents, tier_pct, promo }`.

#### POST `/api/purchase-jobs`
- **Body:** `{ selected_verticals, volume, promo_code? }` → job (`awaiting_payment`). `400` if invalid.

#### GET `/api/purchase-jobs/<job_id>`
Fetch one (company-scoped). `404` if not found/owned.

#### POST `/api/purchase-jobs/<job_id>/pay`
Stub payment → generates result synchronously → `complete`. `409` if wrong status.

### Downloads

Company-scoped; `404` if no result file.

| Method | Path | Returns |
|---|---|---|
| GET | `/api/scrub-jobs/<job_id>/download` | `302` → presigned URL (logs `download`). |
| GET | `/api/scrub-jobs/<job_id>/download-url` | `{ url, filename, expires_in }`. |
| GET | `/api/purchase-jobs/<job_id>/download` | `302` → presigned URL (logs `download`). |
| GET | `/api/purchase-jobs/<job_id>/download-url` | `{ url, filename, expires_in }`. |

### Job history

#### GET `/api/jobs`
The caller's jobs (most recent 50 each). **Response:** `{ scrub_jobs, purchase_jobs }`.

---

## Account API

Blueprint `account`, prefix **`/api/account`**. All require a session; updates only touch the caller's own rows.

| Method | Path | Body / notes |
|---|---|---|
| GET | `/api/account` | `{ user, company, payment_method }`. |
| PUT | `/api/account/profile` | `{ full_name, email, phone? }`. `409` email taken. |
| PUT | `/api/account/company` | `{ company_name }`. **Owner only** (`403` otherwise); `409` name taken. |
| PUT | `/api/account/payment-method` | `{ card_number, exp_month, exp_year, cvc, cardholder_name }` (stub Stripe). |
| DELETE | `/api/account/payment-method` | `{ ok: true }` (or `{ ok, noop }` if none). |

---

## Internal API

Blueprint `internal`, prefix **`/api/internal`**. **Auth:** `X-Internal-Api-Key` **or** an admin session (except `/health`, which is public). Read-only; built for the CX3 Dashboard and platform admins. Most accept a window (`?since=ISO` or `?days=N`, default 90) and a company selector (`?company_id` or `?company_name`).

| Method | Path | Description |
|---|---|---|
| GET | `/api/internal/health` | **Public** liveness probe → `{ status:'ok', service:'gravitas-mailer' }`. |
| GET | `/api/internal/companies` | All registered companies (newest first). |
| GET | `/api/internal/activity` | Rich per-company rollup: summary, purchases, scrubs, downloads, tracking, recent_actions. Company selector **required**. |
| GET | `/api/internal/activity/raw` | Raw `activity_log` rows for a company (max 500). |

---

## Admin API

Blueprint `admin`, prefix **`/api/internal/admin`**. **Auth:** `X-Internal-Api-Key` **or** an admin session. Write side of the internal namespace + platform overview/reports.

### Overview (everything happening in the app)

#### GET `/api/internal/admin/overview`
One-call cross-company snapshot for an admin home screen. Window via `?days=N` (default 30) or `?since=ISO`.
- **Response:** `{ window, companies{total,new_in_window,total_users}, jobs{purchases{count,revenue_cents,records_sold}, scrubs{count,revenue_cents,records_uploaded,unique_found}}, revenue{purchase_cents,scrub_cents,combined_cents,combined_dollars}, recent_jobs[20], recent_activity[25] }`.

### Pricing — tiers

| Method | Path | Body / notes |
|---|---|---|
| GET | `/api/internal/admin/pricing/tiers` | List all tiers. |
| POST | `/api/internal/admin/pricing/tiers` | `{ min_volume, discount_pct (0–100), is_active?, sort_order? }` → `201`. |
| PUT | `/api/internal/admin/pricing/tiers/<tier_id>` | Partial update. `404` if missing. |
| DELETE | `/api/internal/admin/pricing/tiers/<tier_id>` | `{ ok: true }`. |

### Pricing — verticals

| Method | Path | Body / notes |
|---|---|---|
| GET | `/api/internal/admin/verticals` | List all (incl. inactive). |
| POST | `/api/internal/admin/verticals` | `{ slug, display_name, icon?, description?, base_rate?, available_records?, is_active?, sort_order? }` → `201`. `409` slug exists. |
| PUT | `/api/internal/admin/verticals/<vid>` | Partial update. |
| DELETE | `/api/internal/admin/verticals/<vid>` | Soft-delete; `?hard=1` to remove (fails if referenced). |

### Pricing — promo codes

| Method | Path | Body / notes |
|---|---|---|
| GET | `/api/internal/admin/promo-codes` | List all. |
| POST | `/api/internal/admin/promo-codes` | `{ code, discount_pct? / discount_cents? (one > 0), starts_at?, ends_at?, max_redemptions?, applies_to_vertical_id?, is_active? }` → `201`. `409` exists. |
| PUT | `/api/internal/admin/promo-codes/<pid>` | Partial update. |
| DELETE | `/api/internal/admin/promo-codes/<pid>` | `{ ok: true }`. |

### Promo banner (dashboard hero push)

| Method | Path | Body / notes |
|---|---|---|
| GET | `/api/internal/admin/banners` | List all banners. |
| POST | `/api/internal/admin/banners` | `{ title, tag?, body?, cta_label?, cta_url?, icon?, activate?, starts_at?, ends_at? }` → `201`. `activate` (default true) deactivates others. |
| PUT | `/api/internal/admin/banners/<bid>` | Partial update; `is_active:true` deactivates others. |
| DELETE | `/api/internal/admin/banners/<bid>` | `{ ok: true }`. |
| POST | `/api/internal/admin/banners/<bid>/activate` | Atomically make this the only active banner. |

### Reports (platform-wide)

All accept window params (`?days=N` default 30, or `?since=ISO`) and an optional company filter (`?company_id`/`?company_name`). List endpoints accept `?limit` (default 100, max 500) and `?offset`.

| Method | Path | Description |
|---|---|---|
| GET | `/api/internal/admin/reports/summary` | Top-line counts: companies, purchases, scrubs, revenue, activity totals. |
| GET | `/api/internal/admin/reports/daily-sales` | Per-day purchase + scrub revenue. `?fill=true` (default) inserts zero rows for gaps. |
| GET | `/api/internal/admin/reports/purchases` | Paginated purchase jobs, decorated with company. |
| GET | `/api/internal/admin/reports/scrubs` | Paginated scrub jobs, decorated with company. |
| GET | `/api/internal/admin/reports/jobs` | Unified scrub + purchase history across all companies. |
| GET | `/api/internal/admin/reports/downloads` | Every download event, decorated with company + job kind. |
| GET | `/api/internal/admin/reports/activity` | Raw `activity_log`, filterable by `?action=` (max 1000). |

---

## Appendix: scrub job lifecycle & statuses

`scrub_jobs.status` enum:

```
created → uploading → uploaded → awaiting_mapping → importing
   → validating → scrubbing → priced → awaiting_payment → paid
   → submitting_ftp → awaiting_ftp_result → generating → complete
   → failed
```

**EmailOversight clean flow (when `EO_FTP_ENABLED=true`):**
```
upload-init → (PUT parts) → upload-complete            [awaiting_mapping]
   → detect-headers → confirm-email                    [importing → priced]
   → pay                                                [submitting_ftp → awaiting_ftp_result]
   → (worker submits to EO FTP, polls, retrieves)       [complete]
   → download                                           (EO's cleaned CSV, as-is)
```
Two emails fire from the worker: **"payment received"** (after FTP submit) and **"cleaned list is ready"** (on complete), both to the job owner's email.

**Legacy mock-scrub flow (when EO is off):**
```
upload → mapping → import → validate → scrub → priced
   → pay → generating → complete → download (result .xlsx)
```

### EmailOversight pricing (per record, tiered margin)

EO cost is **$0.000375/record**; the user is charged that plus a margin tiered by list size:

| List size | Margin | Effective rate/record |
|---|---|---|
| < 100,000 | +40% | $0.000525 |
| 100,000 – 149,999 | +35% | $0.000506 |
| ≥ 150,000 | +30% | $0.000488 |

Configurable via `EO_PRICE_PER_RECORD`, `EO_MARGIN_PCT_SMALL/MID/LARGE`, `EO_TIER_MID_MIN`, `EO_TIER_LARGE_MIN`.
