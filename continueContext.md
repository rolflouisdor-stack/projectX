# Continue Context — rolling resume doc

> **▶ RESUME 2026-06-08 — START HERE: [`RESUME_2026-06-08.md`](RESUME_2026-06-08.md)**.
> Prod live/healthy on `ffc33a1`. Today = **pricing-display consistency fix**: the
> amount actually charged (base price grossed up for the Stripe fee) now shows
> identically across **checkout → email → Job History → dashboard**, and the UI no
> longer rounds money to whole dollars. New columns `scrub_jobs.amount_paid_cents`
> + `purchase_jobs.amount_paid_cents` (the exact charge, set at pay time); shared
> helper `pricing.effective_total_cents()`; `to_dict` exposes `amount_total_cents`;
> `fmtMoney` now shows 2 decimals. Detail in the resume doc.
>
> (Prior: 2026-06-05 — Stripe LIVE + EO clean flow LIVE; see [`RESUME_2026-06-05.md`](RESUME_2026-06-05.md).)
> Prod live/healthy on `f4d7b13`. **EmailOversight clean flow is LIVE + proven at scale** (184k QA job
> round-tripped; emails on a PROD Mandrill key). EO pricing tiered ($0.000375/rec; 40/35/30% by size).
> **Stripe is LIVE in production** — Payment Element (scrub+buy) + SetupIntent saved cards, live keys +
> live webhook (`we_1Tez9P…`), 3-D Secure redirect handled, **$0.50 min charge**, and the **Stripe fee
> is passed to the customer** (gross-up so payout nets the quoted price; 2.9%+30¢). Also shipped:
> delete-job endpoint + Spaces cleanup, admin access (`ADMIN_EMAILS`) + `/api/internal/admin/overview`,
> in-repo API docs (`docs/api-documentation.md`). LEFT: real-card smoke test + refund; rotate the
> `sk_live_` key (pasted in chat). Detail in the resume doc + memory `project-stripe-integration`.
>
> (Prior: 2026-06-04 — EO backend built inert; see `RESUME_2026-06-04.md`.)
>
> (Prior: 2026-06-03 — JSON-blob redesign fixed EAV-heavy imports (~5 min for 878k); DB reset; email
> notifications wired on a Mandrill test key + DNS done. See `RESUME_2026-06-02.md` for that history.)

> Rolling resume doc, last updated **2026-06-01** (Phase 5 closed). Read this top-to-bottom; it has everything needed to pick up where we stopped. The deeper project ledger is `CONTEXT.md`; this file is the deployment-sprint working state.

---

## 1. One-paragraph status

The Mailer Portal is **fully deployed and live on its custom domain** at `https://mailer.gravitasleads.io` (and still on the DO default ingress `https://gravitas-mailer-ww4cc.ondigitalocean.app`). Phases 0–5 are complete: code on GitHub, DO App Platform with MySQL 8.4 + Valkey 8.0 + Spaces, secrets wired, custom domain attached with Let's Encrypt cert, upload/multipart pipeline working end-to-end through column mapping. **One application-logic bug is open** — completing a scrub fails at the *payment* step with `RuntimeError: no field mappings`. Phase 6 (real Stripe) is the next major workstream.

**Also in-flight (blocked on external):** **EO FTP integration** — real email validation via EmailOversight's bulk FTP, replacing the random-pass-rate stub. Design phase complete in `FTP.md` (FTP-only, plain-FTP-cleartext-with-asks-for-SFTP, shared `cx3ads` account with the CX3 ops system). Outbound email to EmailOversight sent **2026-06-01** with 5 open questions (SFTP availability, completion signal, full `ValidationStatusId` code table, turnaround SLA, max file size). Awaiting reply 2026-06-01 / 2026-06-02. When it lands, fold answers into `FTP.md §10` and build in order §8a (submit worker) → §8b (poller) → §8c (ingest worker, replaces `scrub_engine.run_mock_scrub_on_records` validation step).

---

## 2. 🐞 Open bug — scrub-artifact "no field mappings" at pay

**Symptom:** after upload → mapping → processing → review, clicking "pay" (stub) returns HTTP 500:
```
File "app/api/routes.py", line 352, in pay_scrub_job
    filename, s3_key = generate_scrub_artifact(job)
File "app/services/xlsx_generator.py", line 56
    raise RuntimeError(f"scrub_job {job.id} has no field mappings — cannot build artifact")
```

**Why it's confusing:** `generate_scrub_artifact` queries `ScrubJobFieldMapping.filter_by(scrub_job_id=job.id, skip=False)` and finds none. But the worker (`import_worker.py:141`) *requires* mappings to import, and `pay_scrub_job` only runs once status is `priced`/`awaiting_payment` (i.e. the worker succeeded). No code path deletes mappings (verified import_worker + scrub_engine).

**Leading theory:** on the mapping screen, columns were left as **skip** (no target assigned). The worker reads *all* mappings (no skip filter) so import "succeeds," but `generate_scrub_artifact` filters to `skip=False` → empty → raises.

**Diagnostic steps:**
1. Pull worker log for the failing job:
   ```bash
   APP_ID=3fb2e338-db24-45ad-8965-e01a1e17a908
   doctl apps logs $APP_ID worker --type run | tail -120
   ```
   Look for the job id, row counts, whether import succeeded.
2. Re-run a fresh scrub and **explicitly map the email column** (and others) to standard fields on the mapping screen — do NOT leave them as skip. See if pay then succeeds.
3. If it still fails with columns properly mapped, the bug is in save/read of `ScrubJobFieldMapping`:
   - `app/api/routes.py:259` (`scrub_save_mapping` — saves rows, commits at line 309)
   - `app/services/xlsx_generator.py:51` (the failing query)
   - Inspect what `scrub.html` sends for `skip`/`target_field`.
4. Possible real fix regardless: `scrub_save_mapping` should reject a mapping set with no non-skip email field; or the UI should default to mapped not skipped.

---

## 3. Live infrastructure reference

| Thing | Value |
|---|---|
| DO App ID | `3fb2e338-db24-45ad-8965-e01a1e17a908` |
| **Live URL (production)** | **`https://mailer.gravitasleads.io`** ✅ |
| DO default ingress | `https://gravitas-mailer-ww4cc.ondigitalocean.app` |
| App components | `web` (gunicorn) + `worker` (RQ SimpleWorker), build from GitHub `main` |
| GitHub repo | `https://github.com/rolflouisdor-stack/projectX` (autodeploys on push) |
| MySQL cluster | `gravitas-mailer-db`, MySQL **8.4**, region nyc1, ~$15/mo |
| Valkey cluster | `gravitas-mailer-redis`, **Valkey 8.0**, region nyc1, ~$15/mo |
| Spaces bucket | `gravitas-mailer-prod`, region nyc3, CORS authorizes both origins + `ExposeHeaders: ETag` |
| Active Spaces key | `mailer-prod-2` (the original `mailer-prod` key can be deleted) |
| Domain registrar | name.com → nameservers point to `ns1/2/3.digitalocean.com` |

**Common doctl commands:**
```bash
APP_ID=3fb2e338-db24-45ad-8965-e01a1e17a908
doctl apps list-deployments $APP_ID --format ID,Phase,Created   # newest should be ACTIVE
doctl apps logs $APP_ID web --type run --follow                 # live web logs
doctl apps logs $APP_ID worker --type run --follow              # live worker logs
doctl apps get $APP_ID --format DefaultIngress                  # the URL
doctl apps spec get $APP_ID > /tmp/current-spec.yaml            # download current spec
doctl apps update $APP_ID --spec .do/app.yaml                   # ⚠️ wipes per-component SECRETs
doctl apps create-deployment $APP_ID                            # force redeploy
curl -I https://mailer.gravitasleads.io/api/internal/health     # 200 = healthy
```
**Note:** `doctl apps logs` only works against an ACTIVE deployment. For an ERRORED one it returns "phase final_cleanup" — use the DO web UI → App → Activity → the deployment → Runtime/Deploy Logs.

---

## 4. Secrets — what is set where (values NOT stored here)

App env vars are set per-component in the DO UI. Code-push deploys preserve values; **`doctl apps update --spec` resets per-component SECRET values to empty** — see gotcha §8.

| Env var | web | worker | Source |
|---|---|---|---|
| `SECRET_KEY` | ✅ | ✅ (same) | `openssl rand -hex 32` |
| `INTERNAL_API_KEY` | ✅ | — | `openssl rand -hex 32` |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | ✅ | ✅ | Spaces key `mailer-prod-2` (`DO00…`) |
| `DATABASE_URL` / `REDIS_URL` | auto | auto | bound `${mailer-db.DATABASE_URL}` / `${mailer-redis.DATABASE_URL}` — DO NOT set manually |
| `PUBLIC_BASE_URL` | `https://mailer.gravitasleads.io` | — | hard-coded in spec |
| `STRIPE_*` | empty | empty | Phase 6; `STRIPE_ENABLED=false` for now |

⚠️ **Recurring gotcha #1:** shared secrets must be set on BOTH `web` and `worker` (`SECRET_KEY`, `S3_*`). Bit us twice.

⚠️ **Recurring gotcha #2:** `doctl apps update --spec` silently empties per-component SECRET values. After any spec apply, immediately re-paste them on web and worker.

**Recommended cleanup (not yet done):** move `SECRET_KEY`, `INTERNAL_API_KEY`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` to **app-level** env vars. Both components inherit; one place to set; reduces (but doesn't eliminate) the spec-apply wipe risk. Edit spec — remove per-component decls, add app-level — then re-enter values once in the UI's app-level env section.

---

## 5. Git on this Mac (JumpCloud block) + pending commits

JumpCloud (or a TCC/PPPC profile) blocks git metadata writes under `/Users/`. **Working setup:** GIT_DIR in `/tmp`, work-tree in the project. Auth via `gh` (Keychain).
```bash
export GIT_DIR=/tmp/mailer-git-meta
export GIT_WORK_TREE=/Users/rolf.louisdor/Desktop/mailer
git <command>
```
**`/tmp` is wiped on reboot.** If `ls -d /tmp/mailer-git-meta` is gone, rebuild:
```bash
export GIT_DIR=/tmp/mailer-git-meta
export GIT_WORK_TREE=/Users/rolf.louisdor/Desktop/mailer
git init "$GIT_DIR"
git branch -M main
git remote add origin https://github.com/rolflouisdor-stack/projectX.git
git fetch origin
git reset --mixed origin/main      # adopt remote history, keep local changes
git add . && git status --short
```

**Pending uncommitted:**
- `scripts/set_spaces_cors.py` (Spaces CORS setter)
- `.do/app.yaml` Phase-5 changes (added `domains:` block + `PUBLIC_BASE_URL` to mailer.gravitasleads.io)
- `continueContext.md` + `CONTEXT.md` + `DEPLOYMENT.md` + `README.md` revisions from this session

```bash
git add . && git commit -m "Phase 5: custom domain + CORS script + doc refresh" && git push origin main
```

Long-term: ask IT to lift the JumpCloud policy so we can `git clone` to `~/Desktop` normally.

---

## 6. Remaining work, priority-ordered

> **🚧 Blocked on external — EO FTP integration.** Design done in `FTP.md`; email to EmailOversight sent 2026-06-01 with 5 questions; reply expected 2026-06-01 / 2026-06-02. Resume from `FTP.md §10` when the answers come back. The `EMAIL_VALIDATOR_*` env scaffold in `.env.example` is dead (the real-time API path was dropped); replace with the `EO_FTP_*` block specified in `FTP.md §4`.

1. **🐞 Fix scrub-artifact "no field mappings" bug** (§2). End-to-end product blocker.
2. **🧹 Housekeeping commit** of pending files (§5).
3. **🛡️ App-level secrets cleanup** (§4) — move shared secrets to app-level to reduce per-component drift + spec-wipe risk. Small spec edit + one-time UI re-paste.
4. **Phase 6 — real Stripe** (test mode first):
   - Replace `app/services/stripe_stub.py` with the real `stripe` SDK calls.
   - Two-layer idempotency: (a) job-scoped — `pay_*_job` checks `job.stripe_payment_intent_id` first and reuses it if set; (b) Stripe `idempotency_key=f"job-{kind}-{job.id}-v1"` header.
   - Webhook at `/api/stripe/webhook` — verify `Stripe-Signature` against `STRIPE_WEBHOOK_SECRET`, dedupe `stripe_event_id`, flip job to `paid` on `payment_intent.succeeded`.
   - Client: swap stub iframe for Stripe Elements + PaymentElement + `stripe.confirmPayment()`.
   - User pastes `sk_test_*`/`pk_test_*`/`whsec_*` into DO secrets only; flip `STRIPE_ENABLED=true`.
5. **Phase 7 — CI/CD:** `.github/workflows/ci.yml` already exists. Add GitHub **branch protection** (Settings → Branches → Add rule on `main`: require CI green before merge) — UI click.
6. **Phase 8 — federation:** the `sources` app (TBD, not built yet) and the existing CX3 Dashboard (`~/Desktop/Dashboard/cx3-dashboard/`) talk to mailer via `/api/internal/*` (gated by `INTERNAL_API_KEY`). Deferred.

---

## 7. Decisions locked in
- **Routing:** subdomains — `www.` (landing, TBD), `mailer.` ✅, `sources.` (TBD), `dashboard.gravitasleads.io`.
- **Registrar:** name.com (nameservers point to DO; DNS live).
- **Stripe:** test mode first, flip to live after one clean round-trip.
- **`sources` app:** TBD, deferred until mailer is fully ship-shape.

---

## 8. Gotchas learned this deployment (catalog, so we don't repeat)
- **Control panel does NOT read `.do/app.yaml`** — must use `doctl apps create --spec` / `doctl apps update --spec`.
- **`doctl apps update --spec` silently empties per-component SECRET env values.** Re-paste them in the UI after every spec apply. App-level env vars are slightly more resilient.
- **App Platform dev DBs are Postgres-only** — MySQL/Valkey must be pre-created managed clusters, attached by `cluster_name` (not auto-created from spec).
- **DO managed Redis is region-limited → use Valkey** (protocol-compatible; redis-py + RQ work unchanged).
- **App Platform does NOT auto-inject `DATABASE_URL`/`REDIS_URL`** — bind explicitly via `${db-name.DATABASE_URL}` on each component that needs it.
- **DO MySQL injects `mysql://…?ssl-mode=REQUIRED`** which SQLAlchemy routes to the uninstalled `mysqlclient` driver — `app/extensions.py:_prepare_db_url()` rewrites to `mysql+pymysql://` + no-verify TLS context.
- **DO Valkey is `rediss://` with a private-CA cert** — `app/jobs/queue.py` sets `ssl_cert_reqs='none'` so redis-py doesn't reject it.
- **DO Spaces CORS UI can't set `ExposeHeaders`** — must set CORS via S3 API (`scripts/set_spaces_cors.py`); `ETag` expose-header is required for the browser to read multipart PUT response ETags.
- **Worker is a separate component** — must be under `workers:` not `services:` (else `/` route collision with `web`), and needs its own copy of every env var it uses.
- **`SignatureDoesNotMatch` from Spaces** = mismatched S3 secret (wrong/truncated/stale); **`S3 credentials are not set`** = empty creds on that component.
- **Spaces granular access keys**: when creating, the secret is shown **once**. If anyone but the eventual user creates the key, ensure they copy/transfer the secret immediately or the key is useless.
- **doctl logs limitation:** `doctl apps logs` only works on an ACTIVE deployment; an ERRORED deployment returns "phase final_cleanup" — fall back to the DO web UI for logs.
- **Custom domain attach via spec:** add a top-level `domains:` block with `type: PRIMARY` and `zone: <root-zone>`. DO auto-creates DNS records when the zone is in DO Networking and issues Let's Encrypt automatically.

---

## 9. Local dev (still works)
Local stack: mailer on :5070, MinIO on :9000 (data dir `/tmp/minio-data`), Redis, MySQL. See `CONTEXT.md` for local run commands. The `S3_ADDRESSING_STYLE=path` in local `.env` is for MinIO; prod uses the default `virtual` for Spaces.
