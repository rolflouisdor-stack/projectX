# Session Report — Mailer Portal

**Date:** 2026-05-21
**Project:** `/Users/rolf.louisdor/Desktop/mailer/`
**Related:** `/Users/rolf.louisdor/Desktop/Dashboard/cx3-dashboard/` (changes also made here for the publisher-URL forwarder)

Captures every change made this session, what problem each one solved, where it lives in the code, and what was verified before moving on. Read top-to-bottom for the narrative or jump to a section.

---

## Headline outcomes

1. **Mailer is reachable from the browser again.** Root cause was Chrome's restricted-port blocklist on 5060/SIP; moved to **5070**.
2. **Old `/partners/publisher/dashboard` URL keeps working.** CX3 now forwards `/partners/publisher/*` to the mailer with a 302 + `Cache-Control: no-store`.
3. **Mailers can manage their own account.** New `/account` page lets a logged-in user update name / email / phone, change company name (owner only), and save / replace / remove a card on file. New `mailer_payment_methods` table stores only safe fields (brand, last4, expiry) — never the PAN.
4. **CX3 Ops can manage prices, push banner updates, and pull reports without an admin UI.** New `/api/internal/admin/*` namespace, 22 endpoints, gated by the existing `X-Internal-Api-Key`.
5. **Three new docs cover the work**: `ADMIN_API.md`, `RESOLVED_publisher_dashboard_404.md`, this `SESSION_REPORT.md`. Spec doc `Partner_portals_Spec.md` bumped to v1.2 with a new §11 explaining the port choice so this isn't relearned.

---

## 1. Port 5060 → 5070 (and the 90-minute red herring it ate)

### Symptom

User clicked **Job History** on the mailer (then on `:5060`) and saw the Flask default 404 page. Hard-reloads, incognito tabs, "Disable cache" in DevTools all returned the same thing. `curl http://127.0.0.1:5060/jobs` returned **200**. The contradiction is what dragged this out.

### Root cause

Chromium-family browsers (Chrome, Edge, Brave, Arc, Opera, modern Electron) hard-block outbound HTTP requests to port **5060** (and **5061**). They're on the `kRestrictedPorts` list in `net/base/port_util.cc` because they're the SIP / SIPS-TLS signaling ports — classic VoIP cross-protocol attack surface. The browser **silently refuses to open the TCP connection**, so Flask never logs the request, and the "404" the user sees is whatever the browser surfaces (cached, or `ERR_UNSAFE_PORT` rendered into the page body).

Why this took so long:
- `curl` has no port blocklist, so every curl-based test we ran returned green. That convinced us the server side was fine and we were chasing browser cache.
- The user reported "other localhost apps work fine" — which is true, those are on other ports — so the obvious HTTPS-upgrade theory got ruled out.
- The smoking gun was a deliberate empty `> /tmp/mailer.log` before `open http://127.0.0.1:5060/`. The log stayed at **0 bytes**. The browser never sent. From there: known port blocklist.

### Fix

| File | Change |
|---|---|
| `mailer/.env` | `PORT=5070` |
| `mailer/.env.example` | `PORT=5070` |
| `mailer/config.py:9` | Default fallback in `Config.PORT` → `5070` |
| `cx3-dashboard/app/views.py:6` | `MAILER_PORTAL_URL` default → `http://127.0.0.1:5070` |
| `mailer/Partner_portals_Spec.md` | Bumped to v1.2; every `5060` reference in §1, §2, §3, §8, §9 changed to `5070`; **new §11** explains the restricted-port trap and lists ports to avoid. |

### Verified

- `lsof -iTCP:5070 -sTCP:LISTEN -P` shows Werkzeug listening.
- `open http://127.0.0.1:5070/` → access log immediately shows `GET / 302`, `GET /login 200`, `GET /static/css/mailer.css 200`, `GET /static/img/gravitas_logo.png 200`. Login page renders.
- Issue doc renamed `OPEN_ISSUE_publisher_dashboard_404.md` → `RESOLVED_publisher_dashboard_404.md`.
- Two memory entries written to `~/.claude/projects/.../memory/` so a future session catches the same symptom in seconds.

---

## 2. CX3 forwarder for the old `/partners/publisher/*` URLs

### Symptom

When the publisher portal was detached to the mailer on 2026-05-21, CX3 lost its `/partners/publisher/*` routes. Old bookmarks (e.g. `http://127.0.0.1:5050/partners/publisher/dashboard`) hit dead 404s on CX3.

### Fix

`cx3-dashboard/app/views.py:114-141` — added a catch-all route handler:

```python
_PUBLISHER_PATH_MAP = {
    '': '/login', 'dashboard': '/dashboard', 'login': '/login',
    'signup': '/signup', 'scrub': '/scrub', 'buy': '/buy', 'jobs': '/jobs',
}

@views_bp.route('/partners/publisher', defaults={'rest': ''})
@views_bp.route('/partners/publisher/', defaults={'rest': ''})
@views_bp.route('/partners/publisher/<path:rest>')
def publisher_relocated(rest):
    target = _PUBLISHER_PATH_MAP.get(rest, '/' + rest if rest else '/login')
    resp = redirect(MAILER_PORTAL_URL + target, code=302)
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    return resp
```

Important detail: **302**, not 301. An earlier 301 implementation got permanently cached in user's Chrome and pointed back at the dead `:5060` after we moved the mailer. 302 + `no-store` headers means dev port changes never trap users in a cache.

`cx3-dashboard/app/templates/partners/_base.html:13-17` — cleaned up the dead `portal_kind == 'publisher'` branch that still referenced the deleted routes.

### Verified

```
GET http://127.0.0.1:5050/partners/publisher/dashboard
  → 302 Location: http://127.0.0.1:5070/dashboard
    Cache-Control: no-store
  → 302 Location: /login
  → 200 (Gravitas Leads sign-in page)
```

---

## 3. Account page — let mailers self-serve

### What was built

`/account` is now a real page in the mailer with three cards: **Profile**, **Company**, **Payment method**.

### Backend

**New table — `mailer_payment_methods`** (`app/models/payment_method.py`)

```
id BIGINT PK
company_id BIGINT FK UNIQUE       -- one card per company
stripe_payment_method_id VARCHAR(100)
brand VARCHAR(20)                  -- 'visa', 'mastercard', 'amex', ...
last4 VARCHAR(4)
exp_month INT
exp_year INT
cardholder_name VARCHAR(120)
created_at, updated_at DATETIME
```

Picked a new table over adding columns to `mailer_companies` so SQLAlchemy's `create_all` would pick it up cleanly (no in-place column migration needed).

Registered in `app/models/__init__.py`.

**Stripe stub — `app/services/stripe_stub.py`** (extended)

Added:

- `CardValidationError` — raised on bad input with a user-safe message.
- `_luhn_ok(digits)` — standard Luhn checksum.
- `_detect_brand(digits)` — BIN-prefix lookup (visa/mc/amex/discover/diners/jcb).
- `attach_payment_method(card_number, exp_month, exp_year, cvc, cardholder_name)` — validates (length 13–19, Luhn, exp not past, CVC 3–4 digits), strips PAN to last4, returns a stub `pm_stub_...` id + safe fields.
- `detach_payment_method(pm_id)` — stub no-op.

Hard rule: never persists the full PAN or CVC. Comments flag the production swap as Stripe Elements + SetupIntent so PAN never reaches our servers (PCI-compliant path).

**Account API — `app/api/account_routes.py`** (new blueprint `/api/account`)

| Method | Path | Behaviour |
|---|---|---|
| GET | `/api/account` | Returns `{user, company, payment_method}` |
| PUT | `/api/account/profile` | Update `full_name`, `email`, `phone`. Email uniqueness check against other users. |
| PUT | `/api/account/company` | Update `company_name`. Owner role only (`g.current_user.role == 'owner'`). Uniqueness check. |
| PUT | `/api/account/payment-method` | Upsert card. Detaches previous stripe pm-id before swapping. |
| DELETE | `/api/account/payment-method` | Remove card on file. |

Blueprint registered in `app/__init__.py:36-46`.

### Frontend

**View route** — `app/views.py:55-58` — `GET /account` with `@mailer_login_required`, renders `account.html`.

**Template** — `app/templates/account.html` — three cards, matching the existing `pp-*` CSS:

- **Profile card** — name / email / phone inputs + Save button + inline status.
- **Company card** — company_name input + status/created metadata + Save button.
- **Payment method card**:
  - When there's a card on file: shows `Visa •••• 4242 — Expires 12/2030 — Demo Mailer` with a Remove button.
  - Always shows the add/replace form: cardholder name, card number, MM, YYYY, CVC.
  - Says explicitly: "We never store your full card number. Only brand, last four digits, and expiry are kept on file."

**Nav wiring** — Account link in all four mailer pages now points at `/account` instead of `#`:

- `app/templates/dashboard.html:8`
- `app/templates/scrub.html:8`
- `app/templates/buy.html:8`
- `app/templates/jobs.html:8`

### Verified

End-to-end curl test passed (login → GET account → PUT profile → PUT card Visa → PUT card Mastercard (replace) → PUT bad number (400) → DELETE → confirm null). Browser-side: `open http://127.0.0.1:5070/account` logged `GET /account 200` and `GET /api/account 200` in the live access log, confirming the page rendered and the JS fetched.

---

## 4. Admin API — CX3 Ops controls

The mailer was already wired up with `INTERNAL_API_KEY` auth for the read-only cross-system endpoints (`/api/internal/companies`, `/api/internal/activity`). This session adds the **write side** at `/api/internal/admin/*` so CX3 can run the platform without us building an admin UI yet.

### What was built

**`app/api/admin_routes.py`** — one new blueprint, 22 endpoints, 4 feature groups:

#### 4a. Pricing

| Endpoint | What it edits |
|---|---|
| `GET/POST/PUT/DELETE /pricing/tiers[/<id>]` | Volume-discount table (`pricing_tiers`). Change the 1k/10k/25k/50k/100k thresholds and discount %s on the fly. |
| `GET/POST/PUT/DELETE /verticals[/<id>]` | Per-vertical pricing — `base_rate` (dollars/record), `available_records` inventory snapshot, `is_active`, `display_name`, etc. Soft-delete by default; `?hard=1` for permanent. |
| `GET/POST/PUT/DELETE /promo-codes[/<id>]` | Promo codes used by the buy/scrub quote flow. Validates that at least one of `discount_pct` (0–100) or `discount_cents` is positive on create. |

All return 400 with a `{"error": ...}` body on validation failure (out-of-range %, missing required slug, duplicate code, etc.) and 409 on uniqueness conflicts.

#### 4b. Banner — push-to-dashboard notifications

This is the "send a notification that updates the banner with new info" feature.

| Endpoint | Behaviour |
|---|---|
| `GET /banners` | List every banner (active + inactive) newest first |
| `POST /banners` | Create. Default `activate=true` deactivates all current banners atomically and activates the new one in the same transaction. Pass `activate=false` to stage. |
| `PUT /banners/<id>` | Update fields. If `is_active` transitions false→true, other banners are deactivated. |
| `DELETE /banners/<id>` | Hard delete |
| `POST /banners/<id>/activate` | Atomic "switch the live banner to this one" |

The mailer dashboard pulls `/api/promo-banner` on every load, so banner pushes show up on the next page render with **no client release needed**. Verified by pushing a banner via admin POST and immediately seeing it on the mailer's `/api/promo-banner` response.

#### 4c. Reports

| Endpoint | Purpose |
|---|---|
| `GET /reports/summary` | Top-line counts for executive views: total/new companies, purchases/scrubs counts + revenue + records, activity totals (signups, logins, downloads, promo applies). |
| `GET /reports/daily-sales` | **Per-day revenue.** Purchase + scrub revenue bucketed by UTC `paid_at` date, newest-first, chart-friendly. Default zero-fills missing days for contiguous time series; `?fill=false` returns only days with sales. Totals include `avg_per_day_dollars`, `avg_per_active_day_dollars`, `days_with_sales`. |
| `GET /reports/purchases` | Paginated `purchase_jobs` list across every company. `limit/offset` + optional `company_id`/`company_name` filter. Rows decorated with `company_name`. |
| `GET /reports/scrubs` | Same shape, for `scrub_jobs`. |
| `GET /reports/jobs` | **Combined** purchase + scrub feed, interleaved by `created_at` desc. The "job history report" the user asked for. Each row has a `"kind": "purchase" \| "scrub"` discriminator. |
| `GET /reports/downloads` | Every file-download event (`activity_log.action='download'`), decorated with `company_name` + originating job kind from `meta.kind`. |
| `GET /reports/activity` | Raw `activity_log` filter — optional `action`, `company`, window. Covers everything not specialised above. |

All accept `days=N` (default 90) or `since=ISO8601` for the window.

#### 4d. Auth

All admin endpoints reuse the existing `require_internal_key` decorator imported from `internal_routes.py`. Same `X-Internal-Api-Key` header gates both the read and write surfaces. Rotating the key invalidates both. No DB schema or new user model needed.

### Files touched

| File | Change |
|---|---|
| `app/api/admin_routes.py` | **NEW**, 433 lines — blueprint + 22 endpoints + helpers |
| `app/__init__.py:36-46` | Register `admin_bp` |

### Verified

Full smoke test against the live server. Output:

```
auth gate         : no-key → 401, good-key → 200
pricing/tiers     : 5 default tiers listed; POST 200k @ 25% → 201 id=6
verticals         : 5 listed; PUT auto-ins base_rate=0.025 ✓
promo-codes       : 1 listed (AUTO15); POST SUMMER30 → 201
banners           : create new "New scrub engine just shipped" → id=2 active=true
                    confirmed exactly one active banner
                    /api/promo-banner (mailer-side) returned the new banner immediately
reports/summary   : 52 events, 2 purchases ($2200.63), 2 scrubs ($17.75), 5 downloads
reports/daily-sales (days=7): 2026-05-21 → $1770.28, 2026-05-20 → $448.10, 6 zero-filled days; totals $2218.38 over 2 active days (avg/active $1109.19)
reports/jobs      : returned 5 mixed rows, ordered by created_at desc, decorated with company_name
reports/downloads : returned 5 download events with filenames + originating job ids
```

---

## 5. Documentation produced this session

| File | What it covers |
|---|---|
| `ADMIN_API.md` | Reference for the new admin namespace. Auth, error shape, every endpoint, request/response shape, sample curl, full index table. |
| `RESOLVED_publisher_dashboard_404.md` | Root cause + fix log for the port-5060 issue. Includes the Chromium restricted-port list and the diagnostic shortcut for future sessions. |
| `Partner_portals_Spec.md` (v1.2) | Spec doc updated end-to-end: port references, change-log entry, **new §11** explaining why 5070 and not 5060. |
| `SESSION_REPORT.md` | This file. |

Two memories also written under `~/.claude/projects/.../memory/`:

- `feedback_chromium_restricted_ports.md` — diagnostic rule for "curl works, browser doesn't, Flask sees nothing."
- `run_environment.md` — the two-app layout (mailer :5070, CX3 :5050) and what each handles.

---

## 6. Files inventory (new + changed this session)

**Mailer (`/Users/rolf.louisdor/Desktop/mailer/`):**

```
.env                                                     (port 5060 → 5070)
.env.example                                             (port 5060 → 5070)
config.py                                                (default port → 5070)
Partner_portals_Spec.md                                  (v1.2 + new §11)
ADMIN_API.md                                             (NEW)
RESOLVED_publisher_dashboard_404.md                      (NEW — was OPEN_ISSUE_...)
SESSION_REPORT.md                                        (NEW — this file)

app/__init__.py                                          (register account_bp, admin_bp)
app/models/__init__.py                                   (register PaymentMethod)
app/models/payment_method.py                             (NEW)
app/services/stripe_stub.py                              (added attach/detach + Luhn + brand detect)
app/api/account_routes.py                                (NEW)
app/api/admin_routes.py                                  (NEW)
app/views.py                                             (added /account view)
app/templates/account.html                               (NEW)
app/templates/dashboard.html                             (Account link → /account)
app/templates/scrub.html                                 (Account link → /account)
app/templates/buy.html                                   (Account link → /account)
app/templates/jobs.html                                  (Account link → /account)
```

**CX3 Dashboard (`/Users/rolf.louisdor/Desktop/Dashboard/cx3-dashboard/`):**

```
app/views.py                                             (MAILER_PORTAL_URL + publisher_relocated handler)
app/templates/partners/_base.html                        (cleaned dead 'publisher' branch)
```

---

## 7. Restart cheat-sheet

```bash
# Mailer (port 5070)
lsof -ti:5070 | xargs -r kill -9 ; sleep 1
cd /Users/rolf.louisdor/Desktop/mailer && PYTHONPATH=./lib python3 run.py > /tmp/mailer.log 2>&1 &

# CX3 Dashboard (port 5050)
lsof -ti:5050 | xargs -r kill -9 ; sleep 1
cd /Users/rolf.louisdor/Desktop/Dashboard/cx3-dashboard && PYTHONPATH=./lib python3 run.py > /tmp/cx3.log 2>&1 &
```

Mailer test account: `demo@brandface.com` / `Demo1234`.

Admin API key: read from `mailer/.env` → `INTERNAL_API_KEY` value.

---

## 8. What's still pending (not done this session)

These were in the original spec or surfaced during the session but were not in scope:

- Real Stripe wiring (currently stub). `stripe_stub.py` documents the swap path; `Partner_portals_Spec.md §6.1` has the production checklist.
- Real email validation (NeverBounce/ZeroBounce). Stub gives a random 91-95% pass rate.
- Real scrubbing engine. The "unique" / "overlap" buckets are randomised. `Partner_portals_Spec.md §6.3` calls out one design question still unresolved: does the mailer pay for *unique* records or *non-overlapping* records?
- S3 + signed download URLs for `.xlsx` artifacts. Today they land in `/tmp/gravitas_mailer_artifacts/`.
- Multi-user companies + RBAC UI (model already supports `owner`/`member`).
- Production hardening: rate limiting on `/api/auth/*`, CSRF on JSON endpoints, structured JSON logging, gunicorn/nginx in front.
- An admin **UI** for the endpoints this session shipped — they're API-only today. Probably the highest-leverage next step.
