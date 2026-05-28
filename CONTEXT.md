# Mailer Portal — Resume Context

> Single source of truth for resuming work on this project in a future session. Read top-to-bottom; everything you need to be productive in under 10 minutes is here. Detailed deep-dives are linked at the end.

**Last updated:** 2026-05-27
**Project path:** `/Users/rolf.louisdor/Desktop/mailer/`
**Local URL:** `http://127.0.0.1:5070`
**Test login:** `demo@brandface.com` / `Demo1234`

---

## 🔖 RESUME HERE — paused 2026-05-27 evening, resume 2026-05-28 ~8:30 AM

**The big picture:** mid-deployment sprint. Phase 0 (pre-push hygiene) is **done**. Phase 1 (first GitHub push) is **blocked** by a JumpCloud MDM policy on this Mac that denies `git init` / `git config` writes anywhere under `/Users/`. A `/tmp` workaround is queued and ready to run.

### Where we are in the deployment plan

The plan is in 8 phases (full text in conversation transcript). Status:

| Phase | What | Status |
|---|---|---|
| 0 | Pre-push hygiene (README, `.do/app.yaml` repo, CI workflow, `.gitignore` `.claude/`) | ✅ done |
| 1 | First GitHub push to `rolflouisdor-stack/projectX` | ⏸ blocked by JumpCloud — `/tmp` workaround ready |
| 2 | DigitalOcean account + add `gravitasleads.io` in DO Networking + point `name.com` nameservers to `ns1/2/3.digitalocean.com` | ⏳ user action, not started |
| 3 | Routing decided: **subdomains** (`mailer.`, `sources.`, `dashboard.`, `www.gravitasleads.io`) | ✅ decided |
| 4 | Spin up mailer App on DO (App Platform, attach MySQL + Redis + Spaces, set secrets, configure Spaces CORS) | ⏳ user action |
| 5 | Attach custom domain `mailer.gravitasleads.io`, flip `PUBLIC_BASE_URL` | ⏳ user action |
| 6 | Real Stripe with two-layer idempotency (job-scoped lock + Stripe `Idempotency-Key` header) + webhook + Elements client | ⏳ I'll write, user pastes secrets — test mode first |
| 7 | GitHub Actions CI (already authored in `.github/workflows/ci.yml`) + branch protection | partial — workflow exists, needs branch protection click |
| 8 | sources + Dashboard federation. "sources" doesn't exist yet — TBD. Defer. | ⏳ later |

### Decisions locked in
- **Routing**: subdomains (not paths)
- **Registrar**: name.com (NS change at My Account → Domains → `gravitasleads.io` → Nameservers → Manage)
- **Stripe**: test mode first (`pk_test_*`/`sk_test_*`), flip to live after one full round-trip
- **`sources` app**: TBD, deferred until mailer is live

### The blocker (full diagnosis in memory)
JumpCloud MDM denies all writes to `.git/config` and `.git/config.lock` inside `/Users/rolf.louisdor/`. Confirmed: writes to *other* files in the same dirs work; `git init` works in `/tmp/`; no ACLs/xattrs/flags on the paths. Don't chase chmod/chflags rabbit holes — see `~/.claude/projects/-Users-rolf-louisdor-Desktop-mailer/memory/jumpcloud_git_block.md`. User has been told to email IT for a policy exception in parallel.

### **Resume command — first thing to run tomorrow morning**

```bash
cd /Users/rolf.louisdor/Desktop/mailer
export GIT_DIR=/tmp/mailer-git-meta
export GIT_WORK_TREE=/Users/rolf.louisdor/Desktop/mailer

rm -rf "$GIT_DIR"
git init "$GIT_DIR"
git --git-dir="$GIT_DIR" --work-tree="$GIT_WORK_TREE" branch -M main
git --git-dir="$GIT_DIR" --work-tree="$GIT_WORK_TREE" add .
git --git-dir="$GIT_DIR" --work-tree="$GIT_WORK_TREE" status --short | wc -l
git --git-dir="$GIT_DIR" --work-tree="$GIT_WORK_TREE" commit -m "Initial commit: Mailer Portal, deploy-ready for DigitalOcean App Platform"
git --git-dir="$GIT_DIR" --work-tree="$GIT_WORK_TREE" remote add origin https://github.com/rolflouisdor-stack/projectX.git
git --git-dir="$GIT_DIR" --work-tree="$GIT_WORK_TREE" push -u origin main
```

Caveat: `/tmp` may be wiped on reboot, so the local git metadata is ephemeral. After the first push lands on GitHub, the long-term fix is either (a) IT removes the JumpCloud restriction so we can `git clone` to `~/Desktop/`, or (b) keep `GIT_DIR` somewhere persistent that the policy doesn't cover (TBD if `/tmp/` survives reboots on this Mac).

### Files changed in Phase 0 (uncommitted, will land in first commit)
- `README.md` — created
- `.do/app.yaml` — github.repo set to `rolflouisdor-stack/projectX`; added `STRIPE_PUBLISHABLE_KEY` / `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` as SECRET envs
- `.github/workflows/ci.yml` — created (ruff + import smoke + compileall)
- `.gitignore` — added `.claude/`

### User-supplied artifacts for later phases
- GitHub repo: `https://github.com/rolflouisdor-stack/projectX.git` (assumed empty)
- Domain: `gravitasleads.io` (bought at name.com, not yet pointed at DO)
- Stripe publishable LIVE key was shared in chat — fine, that's public by design. Live secret key + test keys still to come, will be pasted only into DO env-var UI.

### Background processes likely still running (may be killed by reboot)
- Mailer Flask on :5070 — task `bpme1hr3m`
- MinIO on :9000 (data dir `/tmp/minio-data`) — task `b1uwdoiof`

If they're not up tomorrow: restart mailer with `PORT=5070 python3 run.py`, restart MinIO with the block in `[[run-environment]]` memory.

---

## 1. What this project is

Gravitas Leads **Mailer Portal** — the public-facing partner portal where mailers sign up, scrub their own email lists against our premium data, or buy fresh records. Flask + SQLAlchemy + MySQL, server-rendered HTML + a JSON API on the same Flask app. Detached from the internal CX3 Dashboard on 2026-05-21 (CX3 lives at `~/Desktop/Dashboard/cx3-dashboard/` on port 5050 — a separate project, not this one).

Two-app architecture:

| App | Path | Port | Role |
|---|---|---|---|
| **Mailer Portal** (this) | `~/Desktop/mailer/` | 5070 | External partner portal |
| **CX3 Dashboard** (peer) | `~/Desktop/Dashboard/cx3-dashboard/` | 5050 | Internal ops; forwards `/partners/publisher/*` here |

They talk through one HTTP boundary: CX3 calls the mailer's `/api/internal/*` endpoints with an `X-Internal-Api-Key` header. Database-wise they share nothing.

---

## 2. Current state

### Working end-to-end
- Mailer dashboard, scrub wizard (5 steps), buy wizard (4 steps), job history.
- Auth: signup, login, logout, `/api/auth/me`. JWT in a `gm_session` cookie, signed with `SECRET_KEY` from `.env`.
- **`/account` page** (new this session) — name, email, phone, company name, saved-card management. Card storage saves only brand/last4/exp; raw PAN never persisted.
- **Admin API** at `/api/internal/admin/*` (new this session) — 23 endpoints, gated by `X-Internal-Api-Key`. Manages pricing tiers, verticals, promo codes, banners; produces daily-sales and platform-wide activity reports. See `ADMIN_API.md`.
- Activity logging on every meaningful action → powers both per-company `/api/internal/activity` (used by CX3 admin views) and the platform-wide `/api/internal/admin/reports/*`.
- Cross-system internal API for CX3 → activity, raw activity, company list, health probe.

### Stubbed (not real yet — see `Partner_portals_Spec.md` §6)
- Stripe payments — `app/services/stripe_stub.py` returns fake `pi_stub_...` ids; cards are validated (Luhn + brand from BIN) but no charge.
- Email validation — random 91-95% pass rate, but now applied per-row inside the import worker on real `scrub_job_records`.
- Scrub overlap matching — random unique/overlap split, but operates on real imported rows. **Unresolved design Q:** does mailer pay for *unique* or *non-overlapping* records?

### Done in V1.3 (2026-05-22)
- **Scrub upload pipeline is real, end-to-end.** Browser → presigned multipart PUT → DigitalOcean Spaces. New 6-step wizard (Upload → **Map columns** → Processing → Review → Payment → Download). Background RQ worker parses the file from Spaces, applies the user's column mapping, writes rows into `scrub_job_records` (+ EAV custom fields in `scrub_job_record_fields`), and runs the scrub engine. Result `.xlsx` is generated from real records and stored back in Spaces; downloads are 302 → presigned GET.

### Deploy-ready
Repo has `requirements.txt`, `Procfile`, `runtime.txt`, `.do/app.yaml`, `.gitignore`. Full deploy guide in `DEPLOYMENT.md`. Verified gunicorn boots the WSGI app cleanly with 2 workers, every blueprint registered, every route responsive.

---

## 3. How to run locally

Five things need to be up: **MySQL, Redis, MinIO, Flask web, RQ worker**.
MySQL is already a brew service. The other four need to be started.

### One-time setup

```bash
# Queue backend
brew install redis && brew services start redis

# Local S3 (MinIO) — substitutes for DO Spaces on the dev machine
brew install minio/stable/minio minio/stable/mc
mkdir -p ~/minio-data
MINIO_ROOT_USER=minioadmin MINIO_ROOT_PASSWORD=minioadmin123 \
  minio server ~/minio-data --address ":9000" --console-address ":9001" \
  > /tmp/minio.log 2>&1 &
mc alias set local http://127.0.0.1:9000 minioadmin minioadmin123
mc mb local/gravitas-mailer-dev
```

`.env` (on this machine) is already wired to MinIO + local Redis. If you ever
need to rebuild it, copy from `.env.example` and set the `S3_*` block to the
values above + `REDIS_URL=redis://localhost:6379/0`.

### Day-to-day

```bash
# Hard restart (kills any zombie processes, then starts both)
lsof -ti:5070 | xargs -r kill -9
pkill -f 'app.jobs.run_worker'
sleep 1
cd /Users/rolf.louisdor/Desktop/mailer
PYTHONPATH=./lib python3 run.py > /tmp/mailer.log 2>&1 &
PYTHONPATH=./lib python3 -m app.jobs.run_worker > /tmp/mailer_worker.log 2>&1 &
# Browse http://127.0.0.1:5070; MinIO console at http://127.0.0.1:9001
```

Tails:

```bash
tail -f /tmp/mailer.log         # web access + boot
tail -f /tmp/mailer_worker.log  # RQ jobs
tail -f /tmp/minio.log          # S3 requests
```

Tail logs:

```bash
tail -f /tmp/mailer.log
```

CX3 (sibling project, port 5050, restart same way from its own dir).

---

## 4. File map (what lives where)

```
mailer/
├── CONTEXT.md                            ← THIS FILE (resume here)
├── Partner_portals_Spec.md               v1.2 — original spec, port/section refs current
├── DEPLOYMENT.md                         DigitalOcean deploy guide (App Platform + Droplet paths)
├── ADMIN_API.md                          /api/internal/admin/* full reference
├── SESSION_REPORT.md                     Narrative log of 2026-05-21 work session
├── SESSION_REPORT_2026-05-22.md          Narrative log of 2026-05-22 (Spaces + mapping + worker)
├── RESOLVED_publisher_dashboard_404.md   Postmortem on the port-5060 trap
│
├── .env / .env.example                   Env config (PUBLIC_BASE_URL is the one-knob)
├── config.py                             Config classes
├── run.py                                Entrypoint; module-level `app` for gunicorn
├── requirements.txt                      Prod deps (DO installs from this)
├── Procfile                              gunicorn run command for DO App Platform
├── runtime.txt                           python-3.12.7
├── .do/app.yaml                          App Platform spec
├── .gitignore                            Excludes .env, lib/, __pycache__
│
├── lib/                                  Local-dev bundled deps (gitignored)
│
└── app/
    ├── __init__.py                       App factory; registers 6 blueprints; CORS hook
    ├── extensions.py                     SQLAlchemy engine + scoped_session + create_all
    ├── views.py                          Server-rendered HTML routes (/, /login, /signup,
    │                                     /dashboard, /scrub, /buy, /jobs, /account)
    ├── auth/
    │   ├── routes.py                     /api/auth/{signup,login,logout,me}
    │   ├── jwt_utils.py                  JWT mint/verify; gm_session cookie
    │   └── decorators.py                 @mailer_login_required
    ├── api/
    │   ├── routes.py                     Mailer-facing JSON API (verticals, scrub/purchase
    │   │                                 jobs, downloads, dashboard summary)
    │   ├── internal_routes.py            CX3 read-side: /api/internal/{companies,activity,
    │   │                                 activity/raw,health}
    │   ├── account_routes.py             /api/account/* (NEW this session)
    │   └── admin_routes.py               /api/internal/admin/* (NEW this session)
    ├── models/
    │   ├── mailer_company.py, mailer_user.py
    │   ├── vertical.py, subcategory.py
    │   ├── scrub_job.py, purchase_job.py
    │   ├── scrub_job_field_mapping.py    NEW V1.3 — user's column-mapping decisions
    │   ├── scrub_job_record.py           NEW V1.3 — parsed rows; indexed standard fields
    │   ├── scrub_job_record_field.py     NEW V1.3 — EAV for custom (non-standard) fields
    │   ├── activity_log.py               12 action verbs; central audit log
    │   ├── promo_banner.py, promo_code.py, pricing_tier.py
    │   └── payment_method.py             saved-card-on-file table
    ├── services/
    │   ├── seeds.py                      Idempotent boot-time data (5 verticals, 5 tiers,
    │   │                                 sample banner+promo)
    │   ├── migrations.py                 NEW V1.3 — boot-time idempotent ALTERs
    │   ├── activity_logger.py            log_activity() helper
    │   ├── pricing.py                    calc_purchase_quote()
    │   ├── scrub_engine.py               STUB validator + overlap; now operates on real rows
    │   ├── header_detector.py            NEW V1.3 — sniff format + suggest standard mapping
    │   ├── storage.py                    NEW V1.3 — boto3/Spaces wrapper (presigned URLs,
    │   │                                 multipart upload, etc.)
    │   ├── stripe_stub.py                STUB — payment intent + attach/detach card +
    │   │                                 Luhn + BIN→brand
    │   └── xlsx_generator.py             Reads real records, writes to Spaces
    ├── jobs/                             NEW V1.3 — RQ background worker
    │   ├── queue.py                      Redis connection + enqueue_import()
    │   ├── import_worker.py              Streams file from Spaces → records + EAV
    │   └── run_worker.py                 Standalone process entrypoint (Procfile worker:)
    ├── static/css/mailer.css             Orange/white theme
    └── templates/
        ├── _base.html                    Shared header/nav/footer
        ├── auth/{login,signup}.html
        ├── dashboard.html, scrub.html, buy.html, jobs.html
        └── account.html                  NEW
```

---

## 5. Env vars — the one-knob deploy story

`PUBLIC_BASE_URL` is the **only** value that has to change when the host changes (localhost → DigitalOcean → custom domain). Everything else either reads from env automatically or doesn't care about hostname.

| Var | Required | Local-dev value | What it does |
|---|---|---|---|
| `FLASK_ENV` | Yes | `development` | `production` flips to `ProductionConfig`, disables debug |
| `SECRET_KEY` | **Secret** | (in `.env`) | JWT signing. Rotating logs everyone out. |
| `PORT` | Auto | `5070` | DO injects in prod; Flask listens here |
| **`PUBLIC_BASE_URL`** | **Yes** | `http://127.0.0.1:5070` | THE one-knob. Set to DO URL or custom domain on deploy. |
| `CORS_ORIGINS` | No | empty | Comma-separated; add CX3 origin if cross-origin calls needed |
| `DATABASE_URL` | Yes | (in `.env`) | MySQL DSN. Auto-injected when DO managed DB is attached. |
| `INTERNAL_API_KEY` | **Secret** | (in `.env`) | Header `X-Internal-Api-Key` for `/api/internal/*` (CX3 + admin) |
| `JWT_COOKIE_SECURE` | Yes in prod | `false` | Must be `true` on HTTPS (`ProductionConfig` defaults `true`) |
| `STRIPE_ENABLED` | No | `false` | Flip when real Stripe wired |
| `ARTIFACT_DIR` | No | `/tmp/gravitas_mailer_artifacts` | **Legacy** — unused since V1.3 moved storage to Spaces |
| **`S3_*`** | **Yes** | (in `.env`) | DO Spaces creds — uploads + result xlsx live here. See `.env.example` for the 5 vars. |
| **`REDIS_URL`** | **Yes** | `redis://localhost:6379/0` | RQ queue; required for the file-import worker to drain mappings → records. |
| `RQ_QUEUE` | No | `mailer-default` | Queue name shared by web + worker. |

Full matrix + DO-specific notes in `DEPLOYMENT.md` §7.

---

## 6. Routes summary

### Mailer-facing pages (login-required except `/login`, `/signup`)
`/`, `/login`, `/signup`, `/dashboard`, `/scrub`, `/buy`, `/jobs`, `/account`

### Mailer-facing JSON API (login-required)
- `/api/auth/{signup,login,logout,me}`
- `/api/verticals`, `/api/verticals/<id>/track`
- `/api/promo-banner`, `/api/dashboard/summary`
- `/api/scrub-jobs/upload-init`, `/api/scrub-jobs/<id>/upload-complete`, `/api/scrub-jobs/<id>/detect-headers`, `/api/scrub-jobs/<id>/mapping`
- `/api/scrub-jobs/<id>`, `/api/scrub-jobs/<id>/pay`, `/api/scrub-jobs/<id>/download` (302), `/api/scrub-jobs/<id>/download-url` (JSON)
- `/api/purchase-jobs/quote`, `/api/purchase-jobs`, `/api/purchase-jobs/<id>`, `/api/purchase-jobs/<id>/pay`, `/api/purchase-jobs/<id>/download`
- `/api/jobs` (history)
- `/api/account` (GET / PUT-profile / PUT-company / PUT-payment-method / DELETE-payment-method)

### Internal — CX3 read-side (X-Internal-Api-Key)
- `/api/internal/health` (no key)
- `/api/internal/companies`
- `/api/internal/activity?company_id_or_name=&days=`
- `/api/internal/activity/raw?…`

### Internal — Admin write/reports (X-Internal-Api-Key) — `ADMIN_API.md`
- Pricing: `/api/internal/admin/{pricing/tiers, verticals, promo-codes}` (CRUD)
- Banner: `/api/internal/admin/banners` (CRUD) + `/banners/<id>/activate`
- Reports: `/api/internal/admin/reports/{summary, daily-sales, purchases, scrubs, jobs, downloads, activity}`

---

## 7. Key things to know (gotchas + decisions)

- **Port 5060 is hard-blocked by Chrome.** It's on Chromium's `kRestrictedPorts` list (SIP). Mailer was on 5060, got moved to 5070. Don't move it back. Full postmortem in `RESOLVED_publisher_dashboard_404.md`; safe ports for dev: 5070–5099, 8060–8099, 8100+.
- **CX3's `/partners/publisher/*` redirect uses 302 + `Cache-Control: no-store`** (not 301). 301 caches permanently in browsers; if the destination port changes mid-session the browser keeps following the cached 301 and you waste an hour. Don't switch it back to 301.
- **`lib/` is local-dev only.** Gitignored. Production installs from `requirements.txt`. The mailer process needs `PYTHONPATH=./lib` to pick up the bundled deps locally (no venv on this Mac).
- **Auth scope on `/api/account/company`.** Only `role='owner'` can change the company name. Members get 403. Roles exist in the model but there's no UI to invite members yet.
- **Card storage policy: never store the PAN.** `stripe_stub.attach_payment_method` validates the card client-side-style (Luhn + length + exp), strips to brand+last4+exp, and returns a stub `pm_stub_...` id. Even the stub never persists PAN or CVC. Real Stripe wiring should swap to Stripe Elements + SetupIntent so PAN never reaches our servers at all (PCI-compliant path).
- **Banner activation is atomic.** POST to `/api/internal/admin/banners` (or `…/<id>/activate`) deactivates every other banner in the same transaction. The mailer's `/api/promo-banner` pulls on every dashboard view, so a banner push is visible on the very next mailer page load — no cache to bust.
- **`PUBLIC_BASE_URL` is logged on startup** so you can instantly check which host an instance thinks it is: look for `Gravitas Mailer Portal ready on port X (public=…)` in the boot log.
- **CORS is allowlist-only** — never `*`. Set `CORS_ORIGINS=https://cx3.example.com,https://admin.example.com`. If the env is empty, no CORS headers are sent at all (same-origin works fine without them).
- **`paid_at` is the revenue clock**, not `created_at`. The daily-sales report buckets by `paid_at.date()` so a cart created 11:55 PM and paid 12:05 AM correctly counts toward the next day.
- **The RQ worker uses `SimpleWorker`, not the default forking `Worker`.** On macOS, boto3 drags in libobjc, and RQ's default fork-per-job crashes the work-horse with `objc[*]: +[NSNumber initialize]...`. `SimpleWorker` runs jobs in-process — no fork. Trade-off: a job crash kills the worker process; the supervisor restarts it. Fine for current scale. Set in `app/jobs/run_worker.py`. Don't switch back to `Worker` without a host-OS-aware branch.
- **The browser uploads files directly to Spaces, not through Flask.** `/api/scrub-jobs/upload-init` returns presigned PUT URLs; the wizard JS PUTs each part itself. If Spaces' CORS doesn't include `ExposeHeaders: ETag`, the browser can't read the ETag from the PUT response and `/upload-complete` fails. The error message in `scrub.html` names this explicitly.
- **MinIO substitutes for Spaces locally.** Console at http://127.0.0.1:9001 (login `minioadmin / minioadmin123`). Use it to inspect uploaded objects when debugging the import worker — bytes land in `gravitas-mailer-dev/uploads/{company_id}/{job_id}/...`.
- **Schema changes use `app/services/migrations.py`, not Alembic.** Boot-time inspector applies missing ALTERs idempotently. To add a column: model class first, then a matching `_add_column(...)` line in `run_migrations()`. Re-running boot picks it up.

---

## 8. Useful commands

```bash
# Run locally
cd /Users/rolf.louisdor/Desktop/mailer && PYTHONPATH=./lib python3 run.py

# Hard restart
lsof -ti:5070 | xargs -r kill -9 ; sleep 1
cd /Users/rolf.louisdor/Desktop/mailer && PYTHONPATH=./lib python3 run.py > /tmp/mailer.log 2>&1 &

# Test login
rm -f /tmp/c.txt
curl -s -c /tmp/c.txt -X POST http://127.0.0.1:5070/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"demo@brandface.com","password":"Demo1234"}' | jq

# Hit a logged-in route
curl -s -b /tmp/c.txt http://127.0.0.1:5070/api/account | jq

# Hit an admin endpoint
KEY=$(grep ^INTERNAL_API_KEY /Users/rolf.louisdor/Desktop/mailer/.env | cut -d= -f2)
curl -s -H "X-Internal-Api-Key: $KEY" \
  "http://127.0.0.1:5070/api/internal/admin/reports/daily-sales?days=7" | jq

# Push a banner (live for every mailer on their next dashboard view)
curl -s -H "X-Internal-Api-Key: $KEY" -H 'Content-Type: application/json' \
  -X POST http://127.0.0.1:5070/api/internal/admin/banners \
  -d '{"title":"Heads up","body":"Maintenance tonight","icon":"🛠️"}' | jq

# Production smoke: real gunicorn (Linux-only flag --worker-tmp-dir /dev/shm stripped for macOS)
PYTHONPATH=./lib PORT=5081 lib/bin/gunicorn --bind 0.0.0.0:5081 --workers 2 run:app

# Look at recent mailer access log
tail -f /tmp/mailer.log

# Verify MySQL has all expected tables
mysql -u root -p'Newpassword12@' gravitas_mailer -e "SHOW TABLES;"

# CX3 (sibling project, for context)
cd /Users/rolf.louisdor/Desktop/Dashboard/cx3-dashboard && PYTHONPATH=./lib python3 run.py
```

---

## 9. What's pending (good next-session targets)

In rough priority order — closest to "ready to start" first:

- **Real Stripe wiring** — `stripe_stub.py` and `Partner_portals_Spec.md §6.1` describe the swap. Most direct user value.
- **Real overlap matching** + the unresolved pricing question (`Partner_portals_Spec.md §6.3`). The import pipeline now writes real rows into `scrub_job_records`, so the engine has everything it needs — just swap the random `unique_rate` bucketing in `scrub_engine.run_mock_scrub_on_records` for a hash-membership check against the Gravitas data.
- **Admin UI** — the 23 admin endpoints are API-only. A thin CX3-side admin page rendering price-tier table + banner form + daily-sales chart would unlock non-engineers.
- **Real email validation** (NeverBounce or ZeroBounce) — `§6.2`.
- **Rate limiting on `/api/auth/*`** (`§6.6`).
- **Member-invite UI** for multi-user companies (`§6.5`).
- **Structured JSON logging** + log aggregator on DO.
- **First production deploy** — repo isn't pushed to GitHub yet. `DEPLOYMENT.md` is the recipe. ~30 min of clicks.

---

## 10. Where to read next (by question)

| If you want to … | Open |
|---|---|
| Understand the original architecture / DB schema / spec | `Partner_portals_Spec.md` |
| Deploy to DigitalOcean | `DEPLOYMENT.md` |
| Use any `/api/internal/admin/*` endpoint | `ADMIN_API.md` |
| Know what changed on 2026-05-21 in detail | `SESSION_REPORT.md` |
| Know what changed on 2026-05-22 in detail (Spaces + mapping + worker) | `SESSION_REPORT_2026-05-22.md` |
| Hand a leadership-facing cost/effort analysis to a stakeholder | `~/Desktop/Project-Cost/Mailer-Portal-Cost-Analysis.md` (also in Google Drive as a Doc + Slides deck script) |
| Understand the port-5060 / 404 saga (or avoid repeating it) | `RESOLVED_publisher_dashboard_404.md` |
| Know how to talk to this user / project-specific rules | `~/.claude/projects/-Users-rolf-louisdor-Desktop-mailer/memory/MEMORY.md` |

---

## 11. Memory notes (for future Claude sessions)

Already saved to `~/.claude/projects/-Users-rolf-louisdor-Desktop-mailer/memory/`:

- `feedback_chromium_restricted_ports.md` — diagnostic rule: when `curl` works but Flask shows no log entry and the browser says "site can't be reached", suspect the Chromium restricted-port blocklist before cache/HSTS/CORS theories.
- `run_environment.md` — the two-app layout (mailer :5070, CX3 :5050) and what each handles.
- `feedback_rq_simpleworker_macos.md` — on macOS dev, RQ's default forking `Worker` crashes after boto3 loads libobjc. Use `SimpleWorker`. Setting `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` from Python is too late.

If anything in this CONTEXT.md gets out of date — update *this file*, not memory. Memory is for cross-project facts; CONTEXT.md is the per-project running ledger.
