# Gravitas Leads — Mailer Portal

External partner portal where mailers sign up, clean their email lists via EmailOversight, or buy fresh records. Flask + SQLAlchemy + MySQL, server-rendered HTML with a JSON API on the same app. Payments via **Stripe** (live); email cleaning via **EmailOversight** (live); notifications via Mandrill.

🟢 **Live in production:** <https://mailer.gravitasleads.io>

**Security posture** (hardened 2026-06-15 — see [`SECURITY_REMEDIATION.md`](SECURITY_REMEDIATION.md)): HSTS + strict nonce-based CSP and the standard security headers on every response; **email-verification signup** (no account is usable until the emailed link is clicked) with generic, non-enumerable responses; **breached-password check** (Have I Been Pwned) on signup; **rate limiting** (Flask-Limiter, Redis) on the auth routes; JWT session cookie is `Secure; HttpOnly; SameSite=Lax`.

- **Full API reference:** [`docs/api-documentation.md`](docs/api-documentation.md)
- **Current state / next steps (rolling):** [`continueContext.md`](continueContext.md)
- **Project ledger (deeper background):** [`CONTEXT.md`](CONTEXT.md)
- **Deploy to DigitalOcean (+ real-world learnings):** [`DEPLOYMENT.md`](DEPLOYMENT.md)
- **Admin / cross-system API reference:** [`ADMIN_API.md`](ADMIN_API.md)
- **Connect the CX3 Dashboard to the admin API:** [`DASHBOARD_INTEGRATION.md`](DASHBOARD_INTEGRATION.md)
- **Security hardening tracker:** [`SECURITY_REMEDIATION.md`](SECURITY_REMEDIATION.md)
- **Product spec:** [`Partner_portals_Spec.md`](Partner_portals_Spec.md)
- **EmailOversight FTP integration:** [`FTP.md`](FTP.md)

## Local quickstart

Requires MySQL, Redis, and an S3-compatible store (MinIO works) running locally. Full setup in `CONTEXT.md` §3.

```bash
cp .env.example .env
# edit DATABASE_URL, SECRET_KEY, INTERNAL_API_KEY, S3_* to match local services

PYTHONPATH=./lib python3 run.py                     # web on :5070
PYTHONPATH=./lib python3 -m app.jobs.run_worker     # RQ worker
```

Browse http://127.0.0.1:5070.

## Production

Repo ships everything DigitalOcean App Platform needs (`requirements.txt`, `Procfile`, `runtime.txt`, `.do/app.yaml`). Push to `main` → App Platform autodeploys. End-to-end deploy steps in `DEPLOYMENT.md`.
