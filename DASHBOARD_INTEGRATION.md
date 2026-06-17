# Connecting the CX3 Dashboard to the Mailer Admin API

How to wire the **CX3 Ops Dashboard** (internal ops app, runs on `:5050` locally) into the **Mailer Portal's**
internal + admin API so the Dashboard has full cross-company, admin-level read/write access to mailer data
(companies, activity, revenue reports, pricing, promo codes, banners).

- **Mailer** — this app. Dev `http://127.0.0.1:5070`, prod `https://mailer.gravitasleads.io`.
- **CX3 Dashboard** — `~/Desktop/Dashboard/cx3-dashboard/`, dev `http://127.0.0.1:5050`.
- Full endpoint catalog: [`ADMIN_API.md`](ADMIN_API.md) (write side + reports) and §5 of
  [`Partner_portals_Spec.md`](Partner_portals_Spec.md) (read side). Machine reference: [`docs/api-documentation.md`](docs/api-documentation.md).

---

## 1. The two namespaces

| Namespace | Purpose | Examples |
|---|---|---|
| `/api/internal/*` | **Read** cross-system data | `/companies`, `/activity`, `/activity/raw`, `/health` |
| `/api/internal/admin/*` | **Write** + platform reports | `/overview`, `/reports/*`, `/pricing/*`, `/promo-codes`, `/banners`, `/verticals` |

`/api/internal/health` is open (no auth) — a liveness probe. Everything else under both namespaces requires
admin auth (below).

---

## 2. Authentication — two ways in

A request to any guarded `/api/internal/*` or `/api/internal/admin/*` endpoint is authorized if **either** holds:

### Mode A — Static API key (server-to-server) ← use this for the Dashboard
Send the shared secret in a header (or query param):

```
X-Internal-Api-Key: <INTERNAL_API_KEY>
```
```
?api_key=<INTERNAL_API_KEY>     # accepted but prefer the header — query strings land in logs
```

This is the intended path for the Dashboard's **server-side** code calling the mailer. It needs no user session
and grants full cross-company access. `INTERNAL_API_KEY` is already set in mailer prod.

### Mode B — Platform-admin session (browser/cookie)
Any logged-in mailer user whose email is in `ADMIN_EMAILS` (default `rolf.louisdor@cx3ads.com`) reaches every
internal/admin endpoint straight from the browser with their normal `gm_session` cookie — no key. Useful for
ad-hoc admin work from the mailer UI; **not** how the Dashboard should authenticate.

**Failure modes:**
| Response | Meaning |
|---|---|
| `401 {"error":"admin access required"}` | Key missing/wrong **and** caller isn't an admin session. |
| `503 {"error":"internal API key not configured"}` | `INTERNAL_API_KEY` unset on the mailer (misconfig). |

> Earlier today's live probe returned `401` (not `503`), confirming the key **is** configured in prod.

---

## 3. Setup checklist

### 3.1 Mailer side (already done in prod; verify)
- [x] `INTERNAL_API_KEY` set as a SECRET on the mailer `web` (and `worker`) component. Edit via the DO
      live-spec round-trip (see `do_env_spec_roundtrip` in memory / [`DEPLOYMENT.md`](DEPLOYMENT.md)) — never
      `--spec .do/app.yaml`.
- [x] `ADMIN_EMAILS` includes the platform admin(s) for Mode B (default `rolf.louisdor@cx3ads.com`).

Generate a strong key if you ever rotate it:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```
Set the SAME value on the Dashboard (below). Rotating it invalidates both the read and admin APIs at once.

### 3.2 Dashboard side
Give the Dashboard the mailer's base URL + the shared key as env vars (names are a suggestion — match the
Dashboard's config):
```bash
MAILER_API_BASE=https://mailer.gravitasleads.io     # dev: http://127.0.0.1:5070
MAILER_INTERNAL_API_KEY=<same value as the mailer's INTERNAL_API_KEY>
```
Call the mailer **from the Dashboard's server**, never from the Dashboard's browser JS — that would ship the
key to clients. (If you ever truly need browser→mailer calls, see §5 CORS — but proxy through the Dashboard
server instead.)

### 3.3 Connectivity smoke test
```bash
# No key needed — proves you can reach the mailer
curl -s https://mailer.gravitasleads.io/api/internal/health
# -> {"service":"gravitas-mailer","status":"ok"}

# Key required — proves auth works
curl -s -H "X-Internal-Api-Key: $MAILER_INTERNAL_API_KEY" \
  https://mailer.gravitasleads.io/api/internal/companies | jq
```

---

## 4. The endpoints the Dashboard will use

### 4.1 One-call snapshot (start here)
`GET /api/internal/admin/overview?days=30` — a cross-company "everything happening in the app" view:
companies/users, job counts, revenue, recent jobs + activity. Ideal for the Dashboard's mailer landing panel.

```bash
curl -s -H "X-Internal-Api-Key: $KEY" \
  "$MAILER_API_BASE/api/internal/admin/overview?days=30" | jq
```

### 4.2 Read (cross-company)
| Method | Path | Use |
|---|---|---|
| GET | `/api/internal/companies` | Registered companies lookup table |
| GET | `/api/internal/activity?company_id=N&days=30` | One company's behavior (purchases, scrubs, downloads, tracking, recent actions) |
| GET | `/api/internal/activity/raw?company_id=N&days=30` | Raw activity_log rows (cap 500) |

`/activity` and `/activity/raw` take `company_id=` **or** `company_name=`, plus `days=N` or `since=ISO8601`.

### 4.3 Reports + writes
Full details and request/response shapes are in [`ADMIN_API.md`](ADMIN_API.md). Summary:
- **Reports (GET):** `/reports/summary`, `/reports/daily-sales`, `/reports/purchases`, `/reports/scrubs`,
  `/reports/jobs`, `/reports/downloads`, `/reports/activity` — all accept `days`/`since`, optional
  `company_id`/`company_name`, `limit`/`offset`.
- **Pricing (GET/POST/PUT/DELETE):** `/pricing/tiers`, `/verticals`, `/promo-codes`.
- **Banners (GET/POST/PUT/DELETE + `/<id>/activate`):** push the dashboard promo banner every mailer sees.

---

## 5. CORS (only if the Dashboard calls the mailer from a browser)

Server-to-server calls (the recommended pattern) don't involve CORS. If the Dashboard's **browser** must call
the mailer cross-origin, add the Dashboard's public origin to the mailer's `CORS_ORIGINS` (comma-separated):

```bash
CORS_ORIGINS=https://dashboard.gravitasleads.io,http://127.0.0.1:5050
```
The mailer reflects only allow-listed origins (never `*`) and sets `Access-Control-Allow-Credentials: true` +
allows the `X-Internal-Api-Key` header. **Do not** put the API key in browser code regardless — proxy through
the Dashboard server.

> Note (CSP): the mailer's own pages run under a strict Content-Security-Policy. That governs the *mailer's*
> browser context only; it does not restrict the Dashboard calling the mailer's API server-to-server.

---

## 6. Example — Dashboard server-side client (Python)

```python
import os, requests   # or use urllib if you avoid the dep

BASE = os.environ["MAILER_API_BASE"].rstrip("/")
KEY  = os.environ["MAILER_INTERNAL_API_KEY"]
H    = {"X-Internal-Api-Key": KEY}

def mailer_get(path, **params):
    r = requests.get(f"{BASE}{path}", headers=H, params=params, timeout=15)
    r.raise_for_status()
    return r.json()

def mailer_write(method, path, body):
    r = requests.request(method, f"{BASE}{path}",
                         headers={**H, "Content-Type": "application/json"},
                         json=body, timeout=15)
    r.raise_for_status()
    return r.json()

# Cross-company snapshot for the ops landing page
overview = mailer_get("/api/internal/admin/overview", days=30)

# Push a banner every mailer sees on next dashboard load
mailer_write("POST", "/api/internal/admin/banners", {
    "tag": "Heads up", "title": "Holiday pricing live through Dec 31",
    "cta_label": "Shop", "cta_url": "/buy", "icon": "🎁",
})
```

---

## 7. Security notes (read before exposing this)

- **The key is god-mode, cross-company.** Treat `INTERNAL_API_KEY` like a production DB password: secret store
  only, never in git, never in browser code, never in a URL you log. Rotate via the DO live-spec round-trip.
- **The internal API is currently reachable on the public hostname.** The code comments and the API itself warn
  to gate it by host/VPN in production. It's protected by the key, but for defense-in-depth consider restricting
  `/api/internal/*` to the Dashboard's egress (Cloudflare WAF rule / firewall) so the surface isn't internet-wide.
- **Prefer the header over `?api_key=`** — query strings end up in access logs and `Referer`.
- **Rotation invalidates both APIs at once** (read + admin share the key). Coordinate the mailer + Dashboard
  env update so there's no gap.
- Mode B (admin session) is bounded by `ADMIN_EMAILS`; keep that list tight.

---

## 8. Related

- [`ADMIN_API.md`](ADMIN_API.md) — full write-side + reports reference with payload shapes.
- [`docs/api-documentation.md`](docs/api-documentation.md) — complete machine reference for every endpoint.
- `Partner_portals_Spec.md` §5 — the read-only `/api/internal/*` contract.
- Memory: `project_admin_access`, `run_environment` (the two-app layout), `do_env_spec_roundtrip` (how to set
  `INTERNAL_API_KEY`/`CORS_ORIGINS` in prod without wiping secrets).
