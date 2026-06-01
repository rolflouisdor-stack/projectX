# Mailer Portal — DigitalOcean Deployment

**Goal:** ship the Mailer Portal so the only thing that changes between dev and prod is one env var (`PUBLIC_BASE_URL`). Everything else — routes, templates, redirects, cookies, CORS — already reads from config.

> Research basis: DigitalOcean official Flask App Platform tutorial + sample app + community guides (sources listed at bottom). Not memory.

> **🟢 Status (2026-06-01):** App is **live in production** at `https://mailer.gravitasleads.io`. App ID `3fb2e338-db24-45ad-8965-e01a1e17a908`. Current operational state is in `continueContext.md`. This guide is the reference; the section below captures real-world deviations from the theoretical recipe.

---

## 0. Real-world learnings (from the actual deploy, supersede the theoretical steps below)

These are things that the original guide missed, got wrong, or didn't anticipate. Read these before following §4 verbatim.

- **The control panel does NOT auto-read `.do/app.yaml`.** The "Create App → import from GitHub" UI flow uses buildpack autodetection — it does *not* pre-fill components/env vars/databases from your spec. **Use `doctl apps create --spec .do/app.yaml`** (the §5 doctl path) instead, otherwise you'll spend hours hand-configuring 18 env vars and missing the database bindings.
- **`doctl apps update --spec` silently empties per-component SECRET env values.** After every spec apply, immediately re-paste them in the UI on every component. App-level env vars are slightly more resilient — move shared secrets up there if possible.
- **App Platform "dev databases" (the free, app-bundled ones) only support PostgreSQL.** MySQL and Valkey/Redis must be **pre-created managed clusters** (separate billing, ~$15/mo each), and attached by `cluster_name` in the spec. The spec does NOT auto-create them — `cluster_name: foo` with `production: true` and no existing cluster errors with "database cluster was not found."
- **DO Managed Redis is region-restricted; use Valkey.** Creating a `--engine redis` cluster fails with `region 'nyc1' is not valid` in many regions now. Valkey (Redis-protocol-compatible fork) works everywhere. The spec accepts `engine: VALKEY`. `redis-py` and RQ work unchanged against it.
- **App Platform does NOT auto-inject `DATABASE_URL`/`REDIS_URL`** (the spec comments in earlier revisions claimed otherwise — they were wrong). You **must** explicitly bind them in each component's env list:
  ```yaml
  - key: DATABASE_URL
    value: ${mailer-db.DATABASE_URL}
  - key: REDIS_URL
    value: ${mailer-redis.DATABASE_URL}
  ```
- **DO Managed MySQL hands you a `mysql://…?ssl-mode=REQUIRED` URL** which SQLAlchemy routes to the *uninstalled* `mysqlclient` driver, and PyMySQL doesn't understand the `ssl-mode` query param. **The app boot will crash** without a fix. Solution lives in `app/extensions.py:_prepare_db_url()` — it rewrites the scheme to `mysql+pymysql://`, strips the unknown query, and attaches a no-verify TLS context (DO MySQL requires SSL but uses a private CA). Local dev URLs pass through untouched.
- **DO Managed Valkey is `rediss://` with a private-CA cert** — `redis-py` rejects it by default. `app/jobs/queue.py` sets `ssl_cert_reqs='none'` for `rediss://` URLs.
- **The worker is a separate component and goes under `workers:`, NOT `services:`.** Putting it under `services:` causes a `/` route collision with `web` and the spec validates with "rule matching path prefix '/' already in use." Workers don't have HTTP routes.
- **Spaces granular access keys: the secret is shown once.** If anyone other than the eventual user creates the key, ensure they copy/relay the secret immediately or the key is useless. (Bit us on day one.)
- **`SignatureDoesNotMatch`** from Spaces = wrong/truncated/mistyped S3 secret. **`S3 credentials are not set`** = empty creds on that component. These are different errors with different fixes.
- **DO's Spaces CORS UI cannot set `ExposeHeaders`** — but the multipart upload flow needs the browser to read each part's `ETag`, so `ExposeHeaders: ['ETag']` is **required**. Set CORS via the S3 API instead — the repo ships `scripts/set_spaces_cors.py` for this.
- **doctl logs limitation:** `doctl apps logs $APP_ID --type run` only works against an ACTIVE deployment. For an ERRORED deployment it returns `cannot get running logs … in phase final_cleanup`. Fall back to the DO web UI (App → Activity → the deployment → Runtime Logs).
- **Custom domain via the spec:** add a top-level `domains:` block with `domain: subdomain.example.com`, `type: PRIMARY`, `zone: example.com`. DO auto-creates DNS when the zone is in DO Networking and issues Let's Encrypt automatically. Don't forget to change `PUBLIC_BASE_URL` from `${APP_URL}` to the new domain in the same spec apply.
- **Permission gates:** creating Spaces access keys, creating an App, attaching managed DBs, setting env vars, attaching domains — all require **Owner**-level team permission. A Member-role user will silently see missing buttons (Spaces "Create Access Key" disappears) and `doctl` will fail. Get elevated to Owner before starting.
- **Costs to plan for:** App ~$5–12/mo + worker ~$5/mo + Managed MySQL ~$15/mo + Managed Valkey ~$15/mo + Spaces ~$5/mo ≈ **$45–50/mo all-in** for the smallest production-grade footprint. Postgres dev DB would save the MySQL $15; skipping the worker (deferring uploads) saves the Valkey + worker $20. We went full.

---

## TL;DR

For most teams **App Platform** is the right pick (managed, autodeploys from Git, free TLS, built-in MySQL). Pick a Droplet only if you need full OS control (custom system packages, multi-tenancy on one box, or sub-$5/mo).

Files this repo ships for App Platform: `requirements.txt`, `Procfile`, `runtime.txt`, `.do/app.yaml`, `.gitignore`.

```
# One-time
1. Push the mailer repo to GitHub
2. DO Dashboard → Create → Apps → "Import from GitHub" (or `doctl apps create --spec .do/app.yaml`)
3. Attach a Managed MySQL → automatically injects DATABASE_URL
4. Set the 3 SECRET env vars (SECRET_KEY, INTERNAL_API_KEY, JWT_COOKIE_SECURE=true)
5. Deploy. DO assigns a *.ondigitalocean.app URL → set PUBLIC_BASE_URL to that URL
   (or to your custom domain once attached)
```

Done. Future code changes: `git push` → DO auto-deploys. Future host changes: update `PUBLIC_BASE_URL` in DO env settings, nothing else.

---

## 1. App Platform vs Droplet — which to pick

|  | **App Platform** (recommended) | **Droplet** (advanced) |
|---|---|---|
| Cost (small prod) | ~$5–12/mo app + $15/mo MySQL (smallest) | $4–6/mo droplet, MySQL on-box or +$15/mo managed |
| Build / deploy | GitHub push → auto-build & deploy, zero downtime | You SSH in, `git pull`, restart gunicorn/systemd |
| HTTPS | Free Let's Encrypt cert, auto-renew | Set up nginx + certbot yourself |
| Scaling | UI slider (or autoscaling on Pro plan) | Manually resize / add droplets behind a load balancer |
| Where it sees your code | Cloned from GitHub at build time | Whatever you SSH up |
| Right answer when… | You want hands-off; you're OK paying ~$20/mo total | You want max control or have multiple apps on one box |

Below is the App Platform path. Droplet path is in §6 for reference.

---

## 2. What's in the repo for App Platform

All four files DO's buildpack looks for, already committed:

| File | Purpose |
|---|---|
| `requirements.txt` | Direct deps (Flask, SQLAlchemy, PyMySQL, gunicorn, etc.) with versions pinned to what `lib/` ships locally. Source: bundled `lib/` inventory. |
| `Procfile` | The run command: `web: gunicorn --worker-tmp-dir /dev/shm --bind 0.0.0.0:${PORT:-8080} --workers 2 --timeout 60 run:app` |
| `runtime.txt` | `python-3.12.7` — DO defaults to an older Python without this. Bump if you need 3.13. |
| `.do/app.yaml` | Declarative app spec — services, env vars, attached MySQL. Lets you `doctl apps create --spec .do/app.yaml` instead of clicking through the UI. |
| `.gitignore` | Excludes `.env`, `lib/` (DO installs from `requirements.txt`), `__pycache__/`, etc. |

About `lib/`: kept for **local-dev** convenience (lets you run without a venv). It's `.gitignore`d so production doesn't ship 100MB of wheels. App Platform installs deps from `requirements.txt`.

---

## 3. The one-knob host config (`PUBLIC_BASE_URL`)

The promise: change one env var when the deployed host changes, nothing else.

What `PUBLIC_BASE_URL` controls:

- The boot-log line so you can see at a glance which host this instance thinks it is (`Gravitas Mailer Portal ready on port 8080 (public=https://mailer.gravitasleads.com)`).
- Anywhere absolute URLs need to be generated for outbound calls — Stripe webhook return paths, email link generation when SES is wired, social-preview metadata. None of those are emitted today *yet* but they all read from this one variable when they are.
- Optional CORS: if you set `CORS_ORIGINS=https://cx3.gravitasleads.com,https://admin.gravitasleads.com`, the mailer's `/api/*` will return proper `Access-Control-Allow-*` headers to browser callers from those exact origins, and respond `204` to preflights — no `*` wildcard, ever.

What it **doesn't** need to control:

- Template links — `app/templates/*.html` already use root-relative paths (`/dashboard`, `/jobs`, `/api/account`). Those work from any hostname.
- Internal redirects — Flask `redirect('/login')` is relative.
- The Flask listen port — read from `$PORT` (App Platform injects this) with `5070` as the dev fallback.

So the rule on deploy: change `PUBLIC_BASE_URL` to the live URL. Done.

---

## 4. Step-by-step on App Platform (UI path)

### 4a. Push to GitHub

```bash
cd /Users/rolf.louisdor/Desktop/mailer
git init                                          # if not already a repo
git add .
git commit -m "Initial mailer portal commit"
# Create an empty repo on github.com/<you>/gravitas-mailer first, then:
git remote add origin git@github.com:<you>/gravitas-mailer.git
git push -u origin main
```

`.gitignore` already excludes `.env` and `lib/`. **Double-check** before pushing:

```bash
git ls-files | grep -E '\.env$|^lib/' && echo "BAD: secret/bloat in commit" || echo "OK"
```

### 4b. Create the app

DO Dashboard → **Create → Apps → GitHub** → authorize → pick `gravitas-mailer` repo, branch `main`, autodeploy on. App Platform reads `.do/app.yaml` and pre-fills the form:

- Service name: `web`
- Build command: (none — buildpack handles it)
- Run command: `gunicorn --worker-tmp-dir /dev/shm --bind 0.0.0.0:$PORT --workers 2 --timeout 60 run:app`
- Health check: `GET /api/internal/health`
- Instance size: **Basic XXS ($5/mo, 512MB)** — bump to **Basic XS ($12/mo, 1GB)** for any production-ish load.

### 4c. Attach Managed MySQL

In the create flow, **Add Database**:

- Engine: **MySQL 8**
- Smallest dev tier: $15/mo. For real prod with backups + standby, go higher.
- App Platform will **inject `DATABASE_URL`** into the web service automatically. Do not set it manually.

The mailer's `config.py:18` reads `os.getenv('DATABASE_URL')` — same env contract as local. SQLAlchemy's `create_all` runs on boot and creates all tables on first deploy.

### 4d. Set the secrets

App Platform settings → Environment Variables → add as **Encrypted** (type `SECRET`):

```
SECRET_KEY        = <generate: openssl rand -hex 32>
INTERNAL_API_KEY  = <generate: openssl rand -hex 32>
```

Plain env vars (not secret):

```
FLASK_ENV          = production
PUBLIC_BASE_URL    = ${APP_URL}                  # auto-expanded to the DO URL
JWT_COOKIE_SECURE  = true                        # required on HTTPS
CORS_ORIGINS       =                              # fill in once CX3 has a URL
STRIPE_ENABLED     = false
EMAIL_VALIDATOR_ENABLED = false
ARTIFACT_DIR       = /tmp/gravitas_mailer_artifacts
```

`${APP_URL}` is a DO-provided expansion to the public URL of the app. After your first deploy DO assigns something like `https://gravitas-mailer-xyz12.ondigitalocean.app`. With this expansion, `PUBLIC_BASE_URL` follows automatically — no need to copy/paste the URL back in.

### 4e. Deploy

Click **Create Resources**. First build takes ~3–5 minutes. When it finishes:

```bash
DO_URL=https://gravitas-mailer-xyz12.ondigitalocean.app   # your actual URL

curl $DO_URL/api/internal/health                           # → {"status":"ok",...}
curl -I $DO_URL/login                                      # → 200, TLS cert valid
```

### 4f. Add a custom domain (optional)

Settings → **Domains** → Add domain → enter `mailer.gravitasleads.com`. DO gives you the CNAME or A record to set at your DNS provider. After DNS propagates (5–60 min) it issues a free Let's Encrypt cert automatically.

Once the domain is live, **update one env var**:

```
PUBLIC_BASE_URL = https://mailer.gravitasleads.com
```

Save → App Platform redeploys (~60 sec). Done.

---

## 5. App Platform via doctl (scripted path)

If you'd rather not click:

```bash
brew install doctl
doctl auth init                                    # paste your DO token

# Edit .do/app.yaml so `github.repo` matches yours, then:
doctl apps create --spec .do/app.yaml
doctl apps list                                    # grab the app id
doctl apps get <app-id>                            # see status + assigned URL
doctl apps create-deployment <app-id>              # force a redeploy
doctl apps logs <app-id> --type=run --follow       # tail prod logs
```

To update env vars without editing the YAML:

```bash
doctl apps update <app-id> --spec .do/app.yaml     # apply the file
# or via UI for one-off changes
```

---

## 6. Droplet path (only if App Platform isn't right)

This is the manual route. Concrete recipe — Ubuntu 22.04 droplet:

```bash
# On a fresh $4/mo droplet
ssh root@<droplet-ip>
apt update && apt upgrade -y
apt install -y python3.12 python3.12-venv python3-pip nginx mysql-server certbot python3-certbot-nginx

# Clone + venv
adduser --disabled-password --gecos "" mailer
sudo -u mailer -H bash <<'EOF'
cd ~
git clone https://github.com/<you>/gravitas-mailer.git
cd gravitas-mailer
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
# edit .env: real SECRET_KEY, INTERNAL_API_KEY, DATABASE_URL, PUBLIC_BASE_URL,
# JWT_COOKIE_SECURE=true, FLASK_ENV=production
EOF

# MySQL
mysql -e "CREATE DATABASE gravitas_mailer; CREATE USER 'mailer'@'localhost' IDENTIFIED BY '<pass>'; GRANT ALL ON gravitas_mailer.* TO 'mailer'@'localhost'; FLUSH PRIVILEGES;"

# systemd unit
cat > /etc/systemd/system/gravitas-mailer.service <<'EOF'
[Unit]
Description=Gravitas Mailer Portal
After=network.target

[Service]
User=mailer
WorkingDirectory=/home/mailer/gravitas-mailer
EnvironmentFile=/home/mailer/gravitas-mailer/.env
ExecStart=/home/mailer/gravitas-mailer/.venv/bin/gunicorn --worker-tmp-dir /dev/shm --bind 127.0.0.1:8080 --workers 3 --timeout 60 run:app
Restart=always

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now gravitas-mailer

# nginx → gunicorn
cat > /etc/nginx/sites-available/mailer <<'EOF'
server {
    server_name mailer.gravitasleads.com;
    client_max_body_size 32M;
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF
ln -s /etc/nginx/sites-available/mailer /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

# Free TLS
certbot --nginx -d mailer.gravitasleads.com --redirect --non-interactive --agree-tos -m you@gravitasleads.com
```

Deploy update: `ssh` in, `git pull`, `pip install -r requirements.txt` (if deps changed), `systemctl restart gravitas-mailer`. Add a GitHub Actions workflow if you want this scripted.

---

## 7. Env-var matrix (deploy reference)

| Var | Required? | What it controls |
|---|---|---|
| `FLASK_ENV` | Yes — set `production` | Switches `DevelopmentConfig` → `ProductionConfig`, disables debug. |
| `SECRET_KEY` | **Yes, SECRET** | JWT signing key. Rotating invalidates all sessions. `openssl rand -hex 32`. |
| `PORT` | Auto on App Platform | App Platform injects. Local dev: `5070`. |
| `PUBLIC_BASE_URL` | **Yes** | The single host knob. Set to your live URL on every deploy. `${APP_URL}` expansion on App Platform. |
| `CORS_ORIGINS` | Optional | Comma-separated origins that may call `/api/*` from a browser. Leave empty if mailer is only consumed by its own UI. |
| `DATABASE_URL` | **Yes** | `mysql+pymysql://user:pass@host/db`. Auto-injected when you attach a managed MySQL. |
| `INTERNAL_API_KEY` | **Yes, SECRET** | Header `X-Internal-Api-Key` for `/api/internal/*` (CX3 + admin endpoints). |
| `JWT_TOKEN_EXPIRY_SECONDS` | Optional | Default 86400 (24h). |
| `JWT_COOKIE_SECURE` | **Yes — set `true` in prod** | Adds `Secure` flag so the session cookie never leaves over plain HTTP. |
| `STRIPE_ENABLED` | No | Flip to `true` only once real Stripe wiring replaces the stub. |
| `STRIPE_SECRET_KEY` | If `STRIPE_ENABLED=true` | `sk_live_...`. SECRET. |
| `STRIPE_WEBHOOK_SECRET` | If using webhooks | `whsec_...`. SECRET. |
| `EMAIL_VALIDATOR_ENABLED` | No | Same story for NeverBounce/ZeroBounce. |
| `EMAIL_VALIDATOR_API_KEY` | If enabled | SECRET. |
| `ARTIFACT_DIR` | Optional | Legacy local-FS path. Unused once Spaces is wired (below). |
| `ARTIFACT_TTL_DAYS` | Optional | Default 30. |
| `S3_ENDPOINT_URL` | **Yes** | e.g. `https://nyc3.digitaloceanspaces.com`. |
| `S3_REGION` | **Yes** | e.g. `nyc3`. |
| `S3_BUCKET` | **Yes** | Spaces bucket name (one per environment). |
| `S3_ACCESS_KEY` | **Yes, SECRET** | Spaces access key. |
| `S3_SECRET_KEY` | **Yes, SECRET** | Spaces secret key. |
| `S3_MULTIPART_PART_SIZE_BYTES` | Optional | Default 10485760 (10 MiB). AWS min is 5 MiB. |
| `S3_UPLOAD_URL_TTL` | Optional | Default 600s. Presigned PUT validity. |
| `S3_DOWNLOAD_URL_TTL` | Optional | Default 3600s. Presigned GET validity. |
| `REDIS_URL` | **Yes** | `redis://...`. Auto-injected when you attach a Managed Redis to the app. |
| `RQ_QUEUE` | Optional | Default `mailer-default`. |

---

## 7b. Object storage (DigitalOcean Spaces) + worker

The scrub upload pipeline writes the uploaded list AND the generated result `.xlsx`
to a Spaces bucket (S3-compatible). Parsing 100+ MB files happens in a separate
RQ worker process backed by Managed Redis. Without these two pieces wired, the
mailer can sign up users but **file uploads will fail at the upload-init step**.

### Create a Spaces bucket

1. DO dashboard → Spaces → Create Spaces Bucket
2. Pick the same region as your app (e.g. `nyc3`), name it per-environment
   (e.g. `gravitas-mailer-prod`).
3. Generate an access key under Spaces → "Access Keys". Save both halves
   into the App's secret env vars (`S3_ACCESS_KEY`, `S3_SECRET_KEY`).
4. **Configure CORS** on the bucket so the browser can upload parts directly:
   ```json
   [
     {
       "AllowedOrigins": ["https://your-public-base-url"],
       "AllowedMethods": ["PUT", "GET", "HEAD"],
       "AllowedHeaders": ["*"],
       "ExposeHeaders": ["ETag"],
       "MaxAgeSeconds": 3000
     }
   ]
   ```
   `ExposeHeaders: ETag` is non-optional — the browser collects ETags from
   each part PUT and sends them back to `/upload-complete`. Without it
   uploads fail with "missing ETag" mid-flow.
5. (Optional) Set a lifecycle rule to expire `uploads/` after 90 days.

### Attach Managed Redis

The worker needs a queue. In the App Platform UI, **Components → Add Resource → Managed Database → Redis**. Pick the smallest plan (~$15/mo). Once attached,
`REDIS_URL` is auto-injected into every component (web + worker).

### Worker component

`.do/app.yaml` already declares a second service called `worker` with
`run_command: python -m app.jobs.run_worker`. On first deploy DO will spin up
one instance of it alongside the web service. Logs show:

```
RQ worker starting on queue=mailer-default redis=...
```

To scale: bump `instance_count` for the worker service (or use a larger
`instance_size_slug` if jobs are CPU-bound). Default 1 worker is fine for
the current scrub-volume estimate.

### Local-dev parity

```bash
brew install redis && brew services start redis
# In .env, point S3_* at any S3-compatible endpoint (a Spaces bucket works
# fine from a dev laptop; use a separate gravitas-mailer-dev bucket).
PYTHONPATH=./lib python3 -m app.jobs.run_worker &   # background worker
PYTHONPATH=./lib python3 run.py                     # web
```

---

## 8. After deploy — verify

```bash
DO_URL=https://gravitas-mailer-xyz12.ondigitalocean.app   # your URL
KEY=<your INTERNAL_API_KEY>

# Liveness
curl $DO_URL/api/internal/health
# {"service":"gravitas-mailer","status":"ok"}

# TLS
curl -sI $DO_URL/login | head -5
# HTTP/2 200, server, content-type, etc.

# Sign up an account → get a session cookie
curl -s -c /tmp/c -X POST $DO_URL/api/auth/signup \
  -H 'Content-Type: application/json' \
  -d '{"full_name":"Demo","company_name":"Test","email":"demo@test.com","password":"Test1234"}'

# Admin: top-line numbers
curl -s -H "X-Internal-Api-Key: $KEY" "$DO_URL/api/internal/admin/reports/summary?days=30" | jq

# Admin: push a banner; mailers see it on next /dashboard load
curl -s -H "X-Internal-Api-Key: $KEY" -H 'Content-Type: application/json' \
  -X POST "$DO_URL/api/internal/admin/banners" \
  -d '{"title":"Live on DigitalOcean 🚀","tag":"Just shipped","icon":"🚀"}'
```

If the boot log says `(public=<correct URL>)`, the cert is green, signup returns 200 with `Set-Cookie: gm_session=...; Secure; HttpOnly; Path=/; SameSite=Lax`, you're done.

---

## 9. Updating the CX3 Dashboard after the mailer deploys

The CX3 Dashboard's `/partners/publisher/*` forwarder needs to know the mailer's new URL. One env var:

```
MAILER_PORTAL_URL = https://mailer.gravitasleads.com    # whatever the mailer's PUBLIC_BASE_URL is
```

Set it on CX3 (whether CX3 is also on App Platform or a Droplet or still on localhost). The forwarder uses 302 + `Cache-Control: no-store` (see `cx3-dashboard/app/views.py:114-141`), so flipping `MAILER_PORTAL_URL` takes effect on every visitor's next request — no cache-bust drama.

---

## 10. Sources

- [DigitalOcean — How To Deploy a Flask App Using Gunicorn to App Platform](https://www.digitalocean.com/community/tutorials/how-to-deploy-a-flask-app-using-gunicorn-to-app-platform)
- [DigitalOcean Docs — Sample App for Flask](https://docs.digitalocean.com/products/app-platform/getting-started/sample-apps/flask/)
- [DigitalOcean — How To Serve Flask Applications with Gunicorn and Nginx on Ubuntu 22.04](https://www.digitalocean.com/community/tutorials/how-to-serve-flask-applications-with-gunicorn-and-nginx-on-ubuntu-22-04) (Droplet path)
- [DEV.to — How To Deploy A Flask App On DigitalOcean](https://dev.to/stefanie-a/how-to-deploy-a-flask-app-on-digitalocean-3ib7)
- [Amit Jotwani — How to Deploy a Flask App to DigitalOcean's App Platform](https://ajot.me/posts/how-to-deploy-a-flask-app-to-digital-oceans-app-platform/)
