# Gravitas Leads — Mailer Portal

External partner portal where mailers sign up, scrub their email lists against Gravitas data, or buy fresh records. Flask + SQLAlchemy + MySQL, server-rendered HTML with a JSON API on the same app.

- **Resume context for new sessions:** [`CONTEXT.md`](CONTEXT.md)
- **Deploy to DigitalOcean:** [`DEPLOYMENT.md`](DEPLOYMENT.md)
- **Admin / cross-system API reference:** [`ADMIN_API.md`](ADMIN_API.md)
- **Original spec:** [`Partner_portals_Spec.md`](Partner_portals_Spec.md)

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
