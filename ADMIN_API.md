# Mailer Portal — Admin API

**Base URL (dev):** `http://127.0.0.1:5070`
**Namespace:** `/api/internal/admin/`
**Auth (either):** the header `X-Internal-Api-Key: <key>` (or `?api_key=<key>`) for server-to-server callers (CX3 Dashboard), **OR** a logged-in **platform-admin session** — accounts whose email is in `ADMIN_EMAILS` (default `rolf.louisdor@cx3ads.com`) reach every `/api/internal/*` + `/api/internal/admin/*` endpoint straight from the browser, no key. Non-admins get `401 admin access required`. **Never expose the key on a public hostname.**

Companion to the read-only cross-system endpoints documented in §5 of `Partner_portals_Spec.md` (`/api/internal/*`). This file covers the *write* side + reports. Full machine reference: `docs/api-documentation.md`.

**One-call overview:** `GET /api/internal/admin/overview?days=30` returns a cross-company snapshot — companies/users, job counts, revenue, recent jobs + activity — the "everything happening in the app" view.

> CX3 Ops is the intended caller. There is no admin UI yet — calls happen via curl, scripts, or whatever the CX3 dashboard adds on top of these endpoints.

---

## Quick start

```bash
KEY=$(grep ^INTERNAL_API_KEY /Users/rolf.louisdor/Desktop/mailer/.env | cut -d= -f2)
BASE=http://127.0.0.1:5070/api/internal/admin

# Top-line numbers for the last 30 days
curl -s -H "X-Internal-Api-Key: $KEY" "$BASE/reports/summary?days=30" | jq

# Push a new banner that immediately replaces whatever the mailers see
curl -s -H "X-Internal-Api-Key: $KEY" -H 'Content-Type: application/json' \
  -X POST "$BASE/banners" \
  -d '{"tag":"July 4th","title":"INDY25 — 25% off through July 4","cta_label":"Shop","cta_url":"/buy","icon":"🎆"}' | jq

# Bump the base rate on Auto Insurance to $0.025 per record
curl -s -H "X-Internal-Api-Key: $KEY" -H 'Content-Type: application/json' \
  -X PUT "$BASE/verticals/1" -d '{"base_rate":0.025}' | jq
```

---

## Error shape & status codes

Every error response is `{"error": "<safe message>"}` with the appropriate HTTP status.

| Status | When |
|---|---|
| 400 | Validation failure (missing required field, out-of-range value). Body explains. |
| 401 | Missing or wrong `X-Internal-Api-Key`. |
| 404 | Resource id doesn't exist. |
| 409 | Slug / code / company name uniqueness conflict. |
| 503 | Server-side `INTERNAL_API_KEY` env var is unset (misconfiguration). |

All other 2xx returns are JSON. Creates return **201**, mutations return the updated resource as **200**, deletes return `{"ok": true}` as **200**.

---

## 1. Pricing

### 1a. Volume-discount tiers

Powers `pricing_tiers` — the table used by `calc_purchase_quote` for `min_volume → discount_pct` lookups. Edit these to retune tier thresholds without redeploying.

| Method | Path | Purpose |
|---|---|---|
| GET | `/pricing/tiers` | List all tiers, sorted by `sort_order, min_volume` |
| POST | `/pricing/tiers` | Create a tier |
| PUT | `/pricing/tiers/<id>` | Update any subset of `min_volume`, `discount_pct`, `is_active`, `sort_order` |
| DELETE | `/pricing/tiers/<id>` | Remove a tier |

**Tier shape:**

```json
{ "id": 6, "min_volume": 200000, "discount_pct": 25.0, "is_active": true, "sort_order": 600 }
```

`discount_pct` is 0–100 (validated). The lookup picks the highest tier whose `min_volume` is `<= request.volume`.

### 1b. Verticals (per-vertical base price + inventory snapshot)

The mailer-facing `/api/verticals` reads from this table. Bump `base_rate` here and quotes immediately change; set `is_active=false` to hide a vertical from the buy flow without losing history.

| Method | Path | Purpose |
|---|---|---|
| GET | `/verticals` | List every vertical incl. inactive |
| POST | `/verticals` | Create new vertical (`slug` + `display_name` required) |
| PUT | `/verticals/<id>` | Update any of `display_name`, `icon`, `description`, `base_rate`, `available_records`, `is_active`, `sort_order` |
| DELETE | `/verticals/<id>` | Soft-delete (sets `is_active=false`). Pass `?hard=1` to actually remove. |

**Vertical shape:**

```json
{
  "id": 1, "slug": "auto-ins", "display_name": "Auto Insurance",
  "icon": "🚗", "description": "...",
  "base_rate": 0.025,
  "available_records": 1500000,
  "is_active": true, "sort_order": 10
}
```

`base_rate` is dollars-per-record (e.g. `0.025` = 2.5¢ per record). The buy-flow line subtotal is `base_rate × volume`, then the tier discount % is applied, then any promo code.

### 1c. Promo codes

`promo_codes` powers the discount input on the buy and scrub quote flows.

| Method | Path | Purpose |
|---|---|---|
| GET | `/promo-codes` | List all, newest first |
| POST | `/promo-codes` | Create a code (must specify `discount_pct > 0` or `discount_cents > 0`) |
| PUT | `/promo-codes/<id>` | Update any field |
| DELETE | `/promo-codes/<id>` | Hard delete |

**Promo shape:**

```json
{
  "id": 2, "code": "SUMMER30",
  "discount_pct": 30.0, "discount_cents": 0,
  "starts_at": null, "ends_at": null,
  "max_redemptions": 100, "redeemed_count": 0,
  "applies_to_vertical_id": null,
  "is_active": true
}
```

`code` is normalised to UPPERCASE on create. `applies_to_vertical_id=null` means it applies to every vertical.

---

## 2. Promo banner (push-to-dashboard notifications)

This is the "send notification that updates the banner with new information" feature. The mailer dashboard pulls `/api/promo-banner` on load and shows whichever banner is currently active (respecting `starts_at`/`ends_at` windows). Push a new banner here and it shows up on every mailer's next dashboard render — no client release needed.

| Method | Path | Purpose |
|---|---|---|
| GET | `/banners` | List every banner (active + inactive) newest first |
| POST | `/banners` | Create a banner. By default `activate=true` and the create is **atomic** — every previously-active banner is flipped to `is_active=false` in the same transaction, so only the new banner shows. Pass `activate=false` if you want to stage one without showing it yet. |
| PUT | `/banners/<id>` | Update any field. If `is_active` transitions false→true, other banners are deactivated atomically. |
| DELETE | `/banners/<id>` | Hard delete |
| POST | `/banners/<id>/activate` | Atomic "switch the live banner to this one." Deactivates all others, activates this one. |

**Banner shape:**

```json
{
  "id": 2, "tag": "Heads up", "title": "New scrub engine just shipped",
  "body": "Up to 30% more matches with our new pipeline.",
  "cta_label": "Try it", "cta_url": "/scrub", "icon": "⚡",
  "is_active": true,
  "starts_at": null, "ends_at": null
}
```

**Field guide:**
- `tag` — short kicker above the title, e.g. *"Spring Special"*.
- `title` — required, 1–160 chars.
- `body` — long-form. Markdown is not rendered; treat as plain text.
- `cta_label` + `cta_url` — both or neither. URL can be relative (`/buy`) or absolute.
- `icon` — single emoji.
- `starts_at` / `ends_at` — optional UTC ISO8601 (`2026-07-04T00:00:00`). The mailer-side `/api/promo-banner` resolver filters by these. Outside the window the banner won't render even if `is_active=true`.

**Pushing a quick announcement to every mailer right now:**

```bash
curl -s -H "X-Internal-Api-Key: $KEY" -H 'Content-Type: application/json' \
  -X POST $BASE/banners -d '{
    "tag":"Maintenance",
    "title":"Scheduled downtime tonight 11pm–12am ET",
    "body":"We are upgrading the scrubbing pipeline. Jobs in flight will resume automatically.",
    "icon":"🛠️"
  }'
```

That single call replaces the live banner.

---

## 3. Reports

Platform-wide rollups across every mailer company. All endpoints accept:

- `days=N` — last N days (default 90)
- `since=YYYY-MM-DDTHH:MM:SS` — alternative absolute lower bound

Listing endpoints additionally accept:

- `company_id=N` or `company_name=...` — scope to a single company (returns empty if not found)
- `limit` (default 100, max 500)
- `offset` (where supported)
- `action=<verb>` (only on `/reports/activity`)

### 3a. `/reports/summary`

Top-line counts for executive dashboards.

```json
{
  "window": { "since": "2026-04-21T...", "generated_at": "..." },
  "companies": { "total": 1, "new_in_window": 1, "total_users": 1 },
  "purchases": { "count": 2, "revenue_cents": 220063, "revenue_dollars": 2200.63, "records_sold": 125000 },
  "scrubs":    { "count": 2, "revenue_cents": 1775,   "revenue_dollars": 17.75,
                 "records_uploaded": 2000, "unique_records_found": 822 },
  "activity_totals": {
      "all_events": 52, "signups": 1, "logins": 8, "downloads": 5, "promo_applies": 1
  },
  "combined_revenue_cents": 221838,
  "combined_revenue_dollars": 2218.38
}
```

Revenue only counts jobs in `paid` or `complete` status with `paid_at` inside the window.

### 3b. `/reports/daily-sales`

Per-day revenue breakdown — purchase and scrub revenue bucketed by UTC date. Chart-friendly: by default missing days are zero-filled so a sparkline / bar chart has no gaps.

```bash
GET /reports/daily-sales?days=30
GET /reports/daily-sales?days=7&fill=false                 # only days that had sales
GET /reports/daily-sales?days=30&company_name=BrandFace    # scope to one mailer
```

Query params:
- `days=N` or `since=ISO8601` — window (default 90 if neither given).
- `fill` — `true` (default) inserts zero rows for every day in the window with no sales; `false` returns only days that had a sale.
- `company_id=N` or `company_name=...` — optional scope.

Response shape:

```json
{
  "window": {
    "since": "2026-05-14T...",
    "generated_at": "2026-05-21T...",
    "days_in_window": 8,
    "fill": true
  },
  "filter": { "company_id": null },
  "days": [
    {
      "date": "2026-05-21",
      "purchase_count": 1,
      "purchase_revenue_cents": 176000, "purchase_revenue_dollars": 1760.0,
      "purchase_records": 100000,
      "scrub_count": 1,
      "scrub_revenue_cents": 1028, "scrub_revenue_dollars": 10.28,
      "scrub_records_uploaded": 1000,
      "scrub_unique_records": 476,
      "combined_revenue_cents": 177028, "combined_revenue_dollars": 1770.28
    },
    { "date": "2026-05-20", "...": "..." },
    { "date": "2026-05-19", "combined_revenue_dollars": 0.0, "...": "..." }
  ],
  "totals": {
    "purchase_count": 2, "purchase_revenue_dollars": 2200.63, "records_sold": 125000,
    "scrub_count": 2, "scrub_revenue_dollars": 17.75,
    "records_scrubbed": 2000, "unique_records_found": 822,
    "combined_revenue_dollars": 2218.38,
    "days_with_sales": 2,
    "avg_per_day_dollars": 277.3,           // averaged across every day in window
    "avg_per_active_day_dollars": 1109.19   // averaged across only days that had sales
  }
}
```

Notes:
- Rows are returned **newest-first**.
- A "sale" means a job with `status ∈ ('paid', 'complete')` AND `paid_at` inside the window. The bucket date is the UTC date of `paid_at`. `created_at` is intentionally not used — a job created at 11:55 PM and paid at 12:05 AM lands on the day the money actually moved.
- `unknown company_name` returns 200 with an empty `days` array.

### 3c. `/reports/purchases`, `/reports/scrubs`, `/reports/jobs`

Paginated lists of jobs across every company.

```bash
GET /reports/purchases?days=30&limit=50&offset=0
GET /reports/scrubs?days=30&company_name=BrandFace
GET /reports/jobs?days=7&limit=20             # purchases + scrubs interleaved by created_at
```

Response shape:

```json
{
  "window": { "since": "..." },
  "count":  17,
  "returned": 17,
  "offset": 0,
  "limit": 50,
  "rows": [
    {
      "id": 2, "kind": "purchase",
      "company_id": 1, "company_name": "BrandFace",
      "status": "complete",
      "volume": 100000, "price_cents": 176000, "price_dollars": 1760.0,
      "subtotal_cents": 220000, "discount_cents": 44000,
      "selected_verticals": [{...}],
      "promo_code": null,
      "result_filename": "gravitas_purchase_2_20260521.xlsx",
      "created_at": "...", "completed_at": "..."
    },
    ...
  ]
}
```

`kind` is always `"purchase"` or `"scrub"`. Use it to decode the rest of the row (scrub rows have `unique_count`, `uploaded_count`, `overlap_count` etc. — see `ScrubJob.to_dict()` in `app/models/scrub_job.py:53`).

### 3d. `/reports/downloads`

Every file download event with the company that made it and the originating job kind.

```json
{
  "window": { "since": "..." },
  "count": 13,
  "rows": [
    {
      "id": 46, "company_id": 1, "company_name": "BrandFace",
      "action": "download",
      "purchase_job_id": 2, "scrub_job_id": null,
      "meta": { "filename": "gravitas_purchase_2_20260521.xlsx", "kind": "purchase" },
      "created_at": "..."
    }, ...
  ]
}
```

### 3e. `/reports/activity`

Raw `activity_log` rows with company decoration. Supports the optional `action` filter so you can pull, say, every `apply_promo` event across the platform.

```bash
GET /reports/activity?days=30&action=apply_promo
GET /reports/activity?days=30&company_id=1&limit=500
```

Available action verbs (canonical list in `app/models/activity_log.py:10-21`):

`signup`, `login`, `logout`, `view_dashboard`, `view_vertical`, `track_vertical`, `upload_list`, `run_scrub`, `buy_init`, `apply_promo`, `purchase`, `download`.

---

## 4. Auth & operational notes

- Same `X-Internal-Api-Key` powers both this admin API and the read-only `/api/internal/*` (companies / activity) endpoints documented in the spec. Rotating the key invalidates both.
- A liveness probe `GET /api/internal/health` returns 200 without a key.
- Every mutation is committed atomically — no two-phase state. If a write call returns 200, the change is persisted.
- The mailer dashboard fetches the banner on every dashboard view, so banner pushes take effect on the next page load. No client cache to bust.
- All ISO timestamps in responses are naive-UTC (the way SQLAlchemy stores them locally). Treat them as UTC when comparing.

## 5. Full endpoint index

| Method | Path | Note |
|---|---|---|
| GET    | `/api/internal/admin/pricing/tiers` | List tiers |
| POST   | `/api/internal/admin/pricing/tiers` | Create tier |
| PUT    | `/api/internal/admin/pricing/tiers/<id>` | Update tier |
| DELETE | `/api/internal/admin/pricing/tiers/<id>` | Delete tier |
| GET    | `/api/internal/admin/verticals` | List verticals (incl. inactive) |
| POST   | `/api/internal/admin/verticals` | Create vertical |
| PUT    | `/api/internal/admin/verticals/<id>` | Update vertical |
| DELETE | `/api/internal/admin/verticals/<id>` | Soft-delete; `?hard=1` for hard delete |
| GET    | `/api/internal/admin/promo-codes` | List codes |
| POST   | `/api/internal/admin/promo-codes` | Create code |
| PUT    | `/api/internal/admin/promo-codes/<id>` | Update code |
| DELETE | `/api/internal/admin/promo-codes/<id>` | Delete code |
| GET    | `/api/internal/admin/banners` | List banners |
| POST   | `/api/internal/admin/banners` | Create + atomically activate |
| PUT    | `/api/internal/admin/banners/<id>` | Update banner |
| DELETE | `/api/internal/admin/banners/<id>` | Delete banner |
| POST   | `/api/internal/admin/banners/<id>/activate` | Atomically activate a single banner |
| GET    | `/api/internal/admin/reports/summary` | Top-line counts |
| GET    | `/api/internal/admin/reports/daily-sales` | Per-day revenue (purchase + scrub) bucketed by UTC date |
| GET    | `/api/internal/admin/reports/purchases` | Per-company purchase list |
| GET    | `/api/internal/admin/reports/scrubs` | Per-company scrub list |
| GET    | `/api/internal/admin/reports/jobs` | Combined purchases + scrubs |
| GET    | `/api/internal/admin/reports/downloads` | Every download event |
| GET    | `/api/internal/admin/reports/activity` | Raw activity log w/ filters |
