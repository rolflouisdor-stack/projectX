# Gravitas Leads — Mailer Portal

**Version:** 1.3 (Spaces-backed uploads + column mapping)
**Date:** 2026-05-22
**Status:** Backend functional end-to-end. Third-party integrations (Stripe, email validator, real scrubbing) are stubs that successfully exercise the full data flow.
**Lives at:** `/Users/rolf.louisdor/Desktop/mailer/` (relocated from `~/Desktop/Dashboard/gravitas-mailer/` on 2026-05-21).

---

## 1. Why this is a separate project

The Mailer Portal is the **public-facing** side of Gravitas Leads — partners (mailers) sign up, scrub their own email lists against our data, or buy fresh records. The internal CX3 Data Monitor at port 5050 is the **private** ops dashboard.

They're now fully detached:

| | Mailer Portal (this folder) | CX3 Data Monitor |
|---|---|---|
| Path | `/Users/rolf.louisdor/Desktop/mailer/` | `/Users/rolf.louisdor/Desktop/Dashboard/cx3-dashboard/` |
| Audience | External mailers (BrandFace, etc.) | Internal operations team |
| Brand | Gravitas Leads (orange + white) | CX3 Data Monitor (blue) |
| Port | 5070 (was 5060 until 2026-05-21 — see §11) | 5050 |
| Database | `gravitas_mailer` (MySQL) | `cx3_dashboard` (MySQL) |
| Auth | Mailer JWT cookie (own users) | Internal JWT (own users) |
| Domain | Will deploy to its own domain | Stays on internal domain |
| Coupling | None — connected only through one read-only **internal activity API** (§5) | Calls into the activity API from its admin views |

Same Flask + SQLAlchemy stack so operational knowledge transfers, but zero code or DB sharing.

---

## 2. Quick start

```bash
cd /Users/rolf.louisdor/Desktop/mailer
PYTHONPATH=./lib python3 run.py
```

The app boots on **http://127.0.0.1:5070**. On first boot it creates the MySQL DB, runs all migrations, and seeds:

- 5 default verticals + subcategories
- 5 volume-discount pricing tiers (1k / 10k / 25k / 50k / 100k → 0/5/10/15/20%)
- One promo banner ("Spring Special — AUTO15")
- One promo code (`AUTO15` = 15% off)

Then browse to `/signup` to create the first account.

### Environment

| Var | Purpose | Default |
|---|---|---|
| `SECRET_KEY` | JWT signing | random 64-hex (auto-generated on first install) |
| `INTERNAL_API_KEY` | Cross-system API key for CX3 Dashboard | random 64-hex |
| `DATABASE_URL` | MySQL DSN | `mysql+pymysql://root:Newpassword12%40@localhost/gravitas_mailer` |
| `PORT` | HTTP port | `5070` (do **not** use 5060 — see §11) |
| `STRIPE_ENABLED` | Toggle real Stripe vs stub | `false` |
| `EMAIL_VALIDATOR_ENABLED` | Toggle real NeverBounce/ZeroBounce vs stub | `false` |
| `ARTIFACT_DIR` | Where generated `.xlsx` files live | `/tmp/gravitas_mailer_artifacts` |

---

## 3. URL map

### Mailer-facing pages

| Path | Auth | Purpose |
|---|---|---|
| `GET /` | — | Redirect to `/dashboard` if signed in, otherwise `/login` |
| `GET /login` | Public | Sign-in form |
| `GET /signup` | Public | Account creation |
| `GET /dashboard` | Mailer | Intent picker (Scrub or Buy) + promo banner + recent activity |
| `GET /scrub` | Mailer | 5-step scrub wizard |
| `GET /buy` | Mailer | 4-step purchase wizard |
| `GET /jobs` | Mailer | Job history (scrubs + purchases) |

### Mailer-facing JSON API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/auth/signup` | Create company + first user, set session cookie |
| POST | `/api/auth/login` | Validate password, set session cookie |
| POST | `/api/auth/logout` | Clear session cookie |
| GET | `/api/auth/me` | Current user + company |
| GET | `/api/verticals` | List active verticals + subcategories + live counts/rates |
| POST | `/api/verticals/{id}/track` | Log a "user is interested in this vertical" event |
| GET | `/api/promo-banner` | The currently-active promo banner (or `null`) |
| GET | `/api/dashboard/summary` | Recent jobs + lifetime spend |
| POST | `/api/scrub-jobs/upload-init` | Create job + initiate Spaces multipart upload → `{job_id, upload_id, s3_key, part_size, parts:[{PartNumber,url}]}` |
| POST | `/api/scrub-jobs/{id}/upload-complete` | Finalize multipart upload after the browser PUTs every part (sends `{parts:[{PartNumber,ETag}]}`). |
| POST | `/api/scrub-jobs/{id}/detect-headers` | Server reads only the first ~5 rows from Spaces, returns `{headers, sample_rows, suggested_mapping, standard_fields}`. |
| POST | `/api/scrub-jobs/{id}/mapping` | Persist the user's column mapping + enqueue the import worker. Body: `{mappings:[{source_header, column_index, target_field, is_standard, skip}]}` |
| GET | `/api/scrub-jobs/{id}` | Job status (front-end polls during importing/scrubbing) |
| POST | `/api/scrub-jobs/{id}/pay` | Charge card (stub for now), generate `.xlsx` to Spaces, flip to `complete` |
| GET | `/api/scrub-jobs/{id}/download` | 302 → presigned Spaces GET URL |
| GET | `/api/scrub-jobs/{id}/download-url` | Same URL as JSON `{url, filename, expires_in}` (used by the wizard) |
| POST | `/api/purchase-jobs/quote` | Compute live quote (used by the volume slider + promo input) |
| POST | `/api/purchase-jobs` | Persist a quote as a real job awaiting payment |
| GET | `/api/purchase-jobs/{id}` | Job status |
| POST | `/api/purchase-jobs/{id}/pay` | Charge card (stub), generate `.xlsx`, complete |
| GET | `/api/purchase-jobs/{id}/download` | Stream the generated `.xlsx` |
| GET | `/api/jobs` | Both job lists for the history page |

### Cross-system (internal) API

All paths require the `X-Internal-Api-Key` header. **Never** expose this on the public mailer-facing domain — gate by host, IP allowlist, or VPN in production.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/internal/health` | Liveness probe (no key required) |
| GET | `/api/internal/companies` | List every mailer company (id, name, status, created_at) |
| GET | `/api/internal/activity?company_name=&days=` | **The main endpoint** — see §5 |
| GET | `/api/internal/activity/raw?company_name=&days=` | Last 500 raw `activity_log` rows for a company |

---

## 4. Database schema

| Table | Purpose |
|---|---|
| `mailer_companies` | Tenant rows. One per partner company. |
| `mailer_users` | Logins. Many-to-one to companies. |
| `verticals` | Top-level industries (Auto Insurance, Home Insurance, …). `available_records` + `base_rate` are admin-maintained per vertical. |
| `subcategories` | Children of verticals (GEICO, Progressive, …). |
| `scrub_jobs` | Upload-and-scrub workflow with lifecycle `created → uploading → awaiting_mapping → importing → validating → scrubbing → priced → awaiting_payment → paid → complete`. Carries the Spaces `s3_key`, the detected file headers, and the upload's multipart upload id. |
| `scrub_job_field_mappings` | One row per detected column in an uploaded file: the user's choice of standard target (`email`, `first_name`, …), a custom field name, or skip. |
| `scrub_job_records` | One row per parsed line of an upload. Standard fields (`email`, `first_name`, `last_name`, `phone`, `address`, `city`, `state`, `zip`) are first-class indexed columns; the scrub engine fills `is_valid`, `invalid_reason`, `is_unique`. |
| `scrub_job_record_fields` | EAV table for non-standard columns the user mapped (e.g. `car_model`, `year`). One row per (record, field). |
| `purchase_jobs` | Buy-without-upload workflow with lifecycle `created → priced → awaiting_payment → paid → generating → complete`. |
| `activity_log` | Append-only audit trail of every meaningful action. Powers the cross-system activity API. |
| `promo_banners` | Marketing banner content shown on the dashboard. |
| `promo_codes` | Discount codes (validated at quote time + on `/pay`). |
| `pricing_tiers` | Volume-discount tiers (server-side; no longer hardcoded in JS). |

All tables auto-create via SQLAlchemy `Base.metadata.create_all` on app boot.

---

## 5. The cross-system activity API — what the CX3 Dashboard reads

> "If BrandFace purchases, what verticals did he purchase, what verticals he tracks the most, and what verticals he downloaded?"

This is the headline integration point. The CX3 Dashboard (internal ops) calls this endpoint to get a structured rollup per mailer company. Read-only, scoped to a single company, in a configurable time window.

### Request

```bash
curl -s -H "X-Internal-Api-Key: $INTERNAL_API_KEY" \
  "http://gravitas-mailer/api/internal/activity?company_name=BrandFace&days=30"
```

Query params:
- `company_id=N` **or** `company_name=…` (one required)
- `days=N` — last N days (default 90)
- `since=YYYY-MM-DDTHH:MM:SS` — alternative absolute lower bound

### Response shape

```json
{
  "company": { "id": 1, "company_name": "BrandFace", "status": "active", "created_at": "..." },
  "window":  { "since": "...", "generated_at": "..." },
  "summary": {
    "total_events": 47, "login_count": 3, "signup_count": 1,
    "dashboard_views": 12, "apply_promo_count": 2
  },
  "purchases": {
    "count": 5,
    "total_records": 90000,
    "total_spent_cents": 158430,
    "by_vertical": [
      { "vertical_id": 1, "vertical_slug": "auto-ins", "vertical_name": "Auto Insurance",
        "count": 3, "records": 50000, "spend_cents": 90000 },
      ...
    ]
  },
  "scrubs": {
    "count": 8,
    "total_records": 412000,
    "total_unique": 124100,
    "total_spent_cents": 22340
  },
  "downloads": {
    "count": 13,
    "by_vertical": [
      { "vertical_id": 1, "vertical_slug": "auto-ins", "vertical_name": "Auto Insurance",
        "download_count": 7 },
      ...
    ]
  },
  "tracking": {
    "by_vertical": [
      { "vertical_id": 1, "vertical_slug": "auto-ins", "vertical_name": "Auto Insurance",
        "view_count": 18, "track_count": 4 },
      ...
    ]
  },
  "recent_actions": [
    { "action": "purchase", "purchase_job_id": 12, "meta": { ... }, "created_at": "..." },
    ... up to 25 ...
  ]
}
```

### What gets logged

The `activity_log` table is written every time the user does something interesting. Verb taxonomy:

| `action` | When |
|---|---|
| `signup` | Account created |
| `login` | Successful login |
| `logout` | Logout endpoint hit |
| `view_dashboard` | Dashboard summary loaded |
| `view_vertical` | (Reserved — not currently emitted; for future "user hovered a vertical for >N seconds" analytics) |
| `track_vertical` | User selected (toggled-on) a vertical card in the buy flow |
| `upload_list` | A file was uploaded for a scrub |
| `run_scrub` | Scrub pipeline produced a priced job (counts captured in `meta`) |
| `buy_init` | A purchase job was created (not yet paid) |
| `apply_promo` | A promo code was tried at the quote step |
| `purchase` | Payment captured (`meta.kind` distinguishes scrub vs purchase) |
| `download` | File actually downloaded |

Each row carries `vertical_id`, `scrub_job_id`, `purchase_job_id` (any/all nullable), plus a free-form JSON `meta`.

The aggregator in `/api/internal/activity` joins through `purchase_jobs` to attribute purchases + downloads to verticals; tracking is computed directly from `activity_log.vertical_id`.

### CX3 Dashboard side (not yet built)

In the internal blue dashboard, an admin viewing a "BrandFace" detail page would call:

```python
import requests
import os

r = requests.get(
    f"{MAILER_BASE}/api/internal/activity",
    headers={'X-Internal-Api-Key': os.environ['MAILER_INTERNAL_API_KEY']},
    params={'company_name': 'BrandFace', 'days': 30},
    timeout=8,
)
r.raise_for_status()
data = r.json()
# Render: data['purchases']['by_vertical'], data['tracking']['by_vertical'],
#         data['downloads']['by_vertical'], data['recent_actions']
```

A small admin page in `cx3-dashboard` is the next step — the API is ready.

---

## 6. What's stubbed, and what real implementations need

### 6.1 Stripe (currently a stub)

`app/services/stripe_stub.py` returns a fake `payment_intent_id` and immediately marks jobs paid. To go live:

1. `pip install stripe` into `lib/`
2. Replace `stripe_stub.create_payment_intent` with real `stripe.PaymentIntent.create(amount=…, currency='usd', metadata={'job_kind':..., 'job_id':...})`
3. Return `client_secret` so the front-end can mount Stripe Elements and call `stripe.confirmCardPayment`
4. Add a `/webhooks/stripe` endpoint that verifies the signature with `STRIPE_WEBHOOK_SECRET` and flips `paid_at` / `status` based on `payment_intent.succeeded`
5. Flip `STRIPE_ENABLED=true` in `.env`

### 6.2 Email validation (stub)

`app/services/scrub_engine.py` currently uses a random 91-95% pass rate to simulate cleaning. Real impl:

1. Pick a provider (NeverBounce, ZeroBounce, Kickbox)
2. Add a `app/services/email_validator.py` that batches up to 5000 emails per call, polls for completion, and returns `{valid, invalid, disposable, role}` buckets
3. Replace the random `validated_count` math in `scrub_engine.run_mock_scrub` with the real result
4. Set `EMAIL_VALIDATOR_ENABLED=true`

Cost: ~$0.004–0.008 per validation. The +20% cleaning premium covers this.

### 6.3 Real scrubbing engine

The current stub derives `unique_count` / `overlap_count` randomly. The real implementation needs to:

1. Parse the uploaded CSV/XLSX with `openpyxl`/`csv` and normalize each email (`lower().strip()`)
2. Pull the candidate-email-hash pool from CX3's data sources scoped to the publisher's verticals (this requires a connection to `cx3_dashboard`'s `conversions` table, OR a periodic sync into `gravitas_mailer`'s own table)
3. For each uploaded email, sha256-hash it and check membership against that pool
4. Bucket as `unique` (not in our data) or `overlap` (already in our data)
5. **Open question (still unresolved from Partner_Portals_Spec V1.x):** does the mailer pay for the *unique* records (i.e. emails we have that they don't), or the *non-overlapping* records (emails they have that we don't)? Resolve before this ships.

### 6.4 S3 / signed URLs — **DONE (V1.3, 2026-05-22)**

Object storage now lives in DigitalOcean Spaces:

- Uploads go browser → presigned multipart PUT → Spaces (Flask never proxies bytes).
- Result `.xlsx` files are generated by the scrub-pay path and uploaded to Spaces.
- `/scrub-jobs/<id>/download` returns 302 → presigned GET; `/download-url` returns the same URL as JSON for in-page links.

Key layout: `uploads/{company_id}/{job_id}/{uuid}-{filename}` and `results/{company_id}/{job_id}/{filename}.xlsx`. Lifecycle policy on the bucket auto-expires uploads after 90 days.

Setup steps (Spaces bucket creation, CORS, Managed Redis for the import worker) live in `DEPLOYMENT.md` §7b.

### 6.5 Multi-user companies / RBAC

`mailer_users.role` supports `owner | member`, but there's no UI to invite members or manage permissions. Add when needed.

### 6.6 Operational hardening

- `Cache-Control: no-cache` is set on `/static/*` (saved us during the CX3 portal session — leave it on)
- Move from `SECRET_KEY` in `.env` to a secret manager
- Add rate limiting on `/api/auth/*` (Flask-Limiter)
- Add CSRF on the JSON API once we move off pure cookie-based same-site
- Add structured logging (JSON) + a log aggregator
- Move to a real WSGI server (gunicorn + nginx) for prod

---

## 7. File inventory

```
mailer/
├── .env                                 dev secrets (auto-generated SECRET_KEY + INTERNAL_API_KEY)
├── .env.example                         template
├── config.py                            Config / DevelopmentConfig
├── run.py                               entrypoint (adds lib/ to path, boots Flask)
├── lib/                                 bundled pip deps (Flask, SQLAlchemy, openpyxl, PyJWT, etc.)
├── app/
│   ├── __init__.py                      app factory + static no-cache + seeds bootstrap
│   ├── extensions.py                    SQLAlchemy engine + scoped_session
│   ├── views.py                         server-rendered HTML routes
│   ├── auth/
│   │   ├── jwt_utils.py                 issue/verify session JWT, cookie helpers
│   │   ├── decorators.py                @mailer_login_required
│   │   └── routes.py                    /api/auth/{signup,login,logout,me}
│   ├── api/
│   │   ├── routes.py                    mailer-facing JSON API (verticals, jobs, downloads)
│   │   └── internal_routes.py           cross-system /api/internal/* (X-Internal-Api-Key gated)
│   ├── models/
│   │   ├── mailer_company.py
│   │   ├── mailer_user.py
│   │   ├── vertical.py
│   │   ├── subcategory.py
│   │   ├── scrub_job.py
│   │   ├── purchase_job.py
│   │   ├── activity_log.py
│   │   ├── promo_banner.py
│   │   ├── promo_code.py
│   │   └── pricing_tier.py
│   ├── services/
│   │   ├── seeds.py                     idempotent boot-time data
│   │   ├── activity_logger.py           safe append-only log_activity()
│   │   ├── pricing.py                   quote math (tier + promo discounts)
│   │   ├── scrub_engine.py              STUB scrub pipeline
│   │   ├── stripe_stub.py               STUB Stripe payment
│   │   └── xlsx_generator.py            real openpyxl output of generated rows
│   ├── static/
│   │   ├── css/mailer.css               orange/white theme
│   │   └── img/gravitas_logo.png        sampled from gravitasleads.com
│   └── templates/
│       ├── _base.html
│       ├── auth/{login,signup}.html
│       ├── dashboard.html               intent picker + promo + recent
│       ├── scrub.html                   5-step wizard
│       ├── buy.html                     4-step wizard
│       └── jobs.html
└── MAILER_PORTAL_SPEC.md                this file
```

---

## 8. Public-URL access (cloudflared quick tunnel)

When the Mailer Portal needs to be reachable from outside the dev machine (a boss reviewing it from their laptop, a partner testing signup, etc.), we use **Cloudflare's free "quick tunnel"** service via the `cloudflared` CLI.

### Why cloudflared and not ngrok

| | cloudflared quick tunnel | ngrok |
|---|---|---|
| Cost | Free, no usage limits in practice | Free tier, capped sessions |
| **Credentials required** | **None** — no signup, no API key, no auth token | Requires an ngrok.com account + auth token in `~/.config/ngrok/ngrok.yml` |
| Install | `brew install cloudflared` | `brew install ngrok` |
| URL format | `https://<random-words>.trycloudflare.com` | `https://<random>.ngrok-free.app` |
| Session lifetime | Tunnel dies if process dies; URL changes on restart | Same |

We chose cloudflared specifically **because no credentials are needed** — you can hand the project to anyone with a Mac and they can spin up a public link in two commands without an account.

### Install

```bash
brew install cloudflared
```

(Already installed on this machine — verify with `which cloudflared`.)

### Launch a public tunnel

```bash
cloudflared tunnel --url http://localhost:5070 > /tmp/cf_mailer_tunnel.log 2>&1 &
```

It prints a fresh public URL to the log within ~5 seconds:

```bash
grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" /tmp/cf_mailer_tunnel.log | head -1
```

### Stop a running tunnel

```bash
pkill -f "cloudflared tunnel --url http://localhost:5070"
```

### Caveats to communicate to anyone using the link

- **URL is random + changes** every time the tunnel restarts. Re-share after each restart.
- **No password gate** at the tunnel — anyone with the link can hit every route. The Flask app's own auth (signup/login) is the only access control.
- **No real domain yet** — this is a dev-time convenience. Production deploy will use a real Cloudflare-DNS-mapped domain (e.g. `app.gravitasleads.com`), at which point the named-tunnel form (`cloudflared tunnel login` + named tunnel) takes over, and *that* form does require credentials (a Cloudflare account + cert).

### Production path (not yet done)

When we go live we'll switch to a **named** Cloudflare tunnel:

1. `cloudflared tunnel login` — opens browser, you authenticate against Cloudflare and get `~/.cloudflared/cert.pem`
2. `cloudflared tunnel create gravitas-mailer-prod`
3. Add a DNS route in the Cloudflare dashboard mapping `app.gravitasleads.com` → the tunnel
4. `cloudflared tunnel run gravitas-mailer-prod`

That swap is documented here for when we're ready — none of it is wired today.

---

## 9. Manual verification recipe

```bash
cd /Users/rolf.louisdor/Desktop/mailer && PYTHONPATH=./lib python3 run.py &

# 1. signup → cookie set
curl -s -c /tmp/c -X POST http://127.0.0.1:5070/api/auth/signup \
  -H 'Content-Type: application/json' \
  -d '{"full_name":"Demo","company_name":"BrandFace","email":"demo@brandface.com","password":"Demo1234"}'

# 2. verticals → seeded 5
curl -s -b /tmp/c http://127.0.0.1:5070/api/verticals

# 3. upload a CSV → scrub → pay → download
echo "email" > /tmp/list.csv && for i in $(seq 1 200); do echo "u$i@x.com" >> /tmp/list.csv; done
JID=$(curl -s -b /tmp/c -X POST http://127.0.0.1:5070/api/scrub-jobs -F file=@/tmp/list.csv -F cleaning=true | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])")
curl -s -b /tmp/c -X POST http://127.0.0.1:5070/api/scrub-jobs/$JID/pay
curl -s -b /tmp/c -o /tmp/result.xlsx http://127.0.0.1:5070/api/scrub-jobs/$JID/download
file /tmp/result.xlsx   # → "Microsoft Excel 2007+"

# 4. cross-system activity API
KEY=$(grep ^INTERNAL_API_KEY .env | cut -d= -f2)
curl -s -H "X-Internal-Api-Key: $KEY" \
  "http://127.0.0.1:5070/api/internal/activity?company_name=BrandFace&days=30" | python3 -m json.tool
```

All of the above returned 200 in the smoke-test run that ships with V1.0.

---

## 10. Change log

- **2026-05-20** — V1.0 Initial detached project. Full mailer-facing UI + API + activity logging + cross-system activity API. Stripe / email validator / real scrubbing engine all stubbed (see §6 for the spec on swapping each).
- **2026-05-21** — V1.1
  - **Relocated** the entire codebase from `~/Desktop/Dashboard/gravitas-mailer/` to `~/Desktop/mailer/` (the source folder no longer exists).
  - **Renamed** the spec doc from `MAILER_PORTAL_SPEC.md` to `Partner_portals_Spec.md`.
  - Path references throughout this doc updated.
  - Added §8 documenting `cloudflared` as the chosen public-tunnel service (free, no credentials needed for quick tunnels).
  - Killed the running quick tunnel. Local Flask app on port 5060 still running.
- **2026-05-21** — V1.2
  - **Port changed from 5060 → 5070.** See §11. Updated `.env`, `.env.example`, `config.py`, and the CX3 Dashboard's `/partners/publisher/*` redirect handler that targets the mailer.
- **2026-05-22** — V1.3
  - **Scrub upload pipeline rewritten end-to-end** for real production traffic:
    - Browser uploads files directly to DigitalOcean Spaces via presigned multipart URLs. Flask never proxies file bytes; supports the spec's "files up to 500 MB" claim.
    - New mapping step in the wizard (now 6 steps): server detects headers + suggests synonyms; user maps each column to a standard field, names it as a custom field, or skips it.
    - Standard fields (`first_name, last_name, email, phone, address, city, state, zip`) land in `scrub_job_records`. Anything else lands in `scrub_job_record_fields` (EAV).
    - File parsing runs in an RQ worker process backed by Managed Redis. Web process enqueues + polls; never blocks on file I/O.
    - Result `.xlsx` is generated from the real imported records (not stub data) and uploaded to Spaces. Downloads are 302 → presigned GET, expiring after 1 hour.
  - §6.4 flipped from TODO to done. §6.3 (real overlap matching) still pending, but it now runs against real `scrub_job_records` rows rather than file-size heuristics.
  - Three new tables (`scrub_job_field_mappings`, `scrub_job_records`, `scrub_job_record_fields`) and 9 new columns on `scrub_jobs`. Boot-time `app/services/migrations.py` applies them idempotently on existing MySQL.
  - DEPLOYMENT.md §7b documents Spaces bucket creation + CORS (the `ExposeHeaders: ETag` requirement is non-obvious — see that section) + Managed Redis setup.

---

## 11. Why the port is 5070 and not 5060

**Do not put the mailer back on port 5060.** Chromium-family browsers (Chrome, Edge, Brave, Opera, Arc, modern Electron apps) hard-block outbound HTTP requests to port **5060** (and **5061**) as part of their *restricted ports* list. The block is in `net/base/port_util.cc` in the Chromium source — those ports are reserved for SIP / SIPS-TLS (VoIP signaling), and the browsers refuse to make plain HTTP requests there to prevent cross-protocol attacks.

What it looks like when you forget: the user types `http://127.0.0.1:5060/dashboard`, Chrome shows "This site can't be reached" with `ERR_UNSAFE_PORT`, *and Flask never sees the request* (so the access log is silent and you waste a long time hypothesising about cache / CORS / HSTS). `curl` and Safari are unaffected, which makes it especially confusing.

5070 was chosen because it's adjacent to the old port, not on any browser's restricted list, and not a well-known service port. If 5070 ever conflicts with something else on your machine, any of these are also safe: `5070`, `5080`, `8060`, `8070`, `8090`. Avoid anything Chromium lists: notably **5060, 5061, 6000, 6566, 6665–6669, 6697, 10080** are the common ones in the dev range.

This was found on 2026-05-21 after a user-facing 404 turned out to be Chrome silently refusing to send the request — the redirect handler at `/partners/publisher/*` on CX3 already pointed to `:5060` and was unreachable end-to-end from the browser even though it worked perfectly from `curl`.
