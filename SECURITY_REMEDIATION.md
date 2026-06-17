# Security Remediation — TODO

Findings from an authorized white-box pentest of `https://mailer.gravitasleads.io/` on **2026-06-10**.
Full walkthrough/tutorial lives at `~/Desktop/PEN-TEST/pen.md`.

**TL;DR:** core auth / authz / payment logic is solid. No IDOR, no JWT forgery, no debug leaks. Every open item
below is **perimeter hardening**, not a break-in. Tackle in priority order.

---

## 🔴 Do first (Medium severity)

### 1. Add HTTP security headers — ✅ DONE & DEPLOYED (2026-06-15, commit a76d5957)
None of HSTS / CSP / X-Frame-Options / X-Content-Type-Options / Referrer-Policy were sent.
Risk: clickjacking, MIME-sniffing, SSL-strip on first visit, no XSS defense-in-depth.

- [x] Added `_security_headers` `after_request` hook + `CSP` constant in `app/__init__.py`
      (HSTS, X-Content-Type-Options, X-Frame-Options, Referrer-Policy, Permissions-Policy, CSP).
      Uses `setdefault` so it never clobbers per-route headers. CSP allowlists Stripe.js + jsDelivr
      (chart.js) + `api.stripe.com` for connect-src; `frame-ancestors 'none'` + `object-src 'none'`.
- [x] Verified locally: booted the real `create_app` against in-memory SQLite, `GET /login` → 200 with
      all six headers present and correct.
- [x] **Post-deploy:** browser console check surfaced a real break — the strict `script-src` blocked the
      app's own inline `<script>` blocks (login broke). Fixed interim with `'unsafe-inline'` (commit
      c9559c9c) + added `cdn.jsdelivr.net` to `connect-src` (chart.js sourcemap). **See item 1b below.**
- [x] **Post-deploy re-test:** confirmed live ~2 min after push — all six headers serving on
      https://mailer.gravitasleads.io/login.
- [x] Deployed via `deploy_on_push` (pushed `app/__init__.py` to `rolflouisdor-stack/projectX@main`
      through the GitHub Contents API, since local git is blocked by JumpCloud — see note below).

### 2. Add login rate limiting / lockout — ✅ DONE & DEPLOYED (commit 977801ba)
15 rapid failed logins used to all return 401 with no throttle. Now Flask-Limiter (Redis-backed) gates the
auth routes.

- [x] `Flask-Limiter==4.1.1` added to `requirements.txt`; `limiter` + Cloudflare-aware `client_ip` key in
      `app/extensions.py`; init + JSON 429 handler in `app/__init__.py`.
- [x] `/api/auth/login`: `10/min;100/hr` per client-IP **and** `5/min;20/hr` per email (the per-email limit
      stops a password-spray against one account even across rotating IPs).
- [x] `/api/auth/signup`: `5/min;20/hr` per IP (brute-force + enumeration/abuse).
- [x] Shared state via the existing `REDIS_URL` (`${mailer-redis.DATABASE_URL}`, set on web + worker);
      `memory://` fallback for local. **Fails OPEN** (`RATELIMIT_SWALLOW_ERRORS=True`) so a Redis hiccup
      can't lock everyone out of login.
- [x] Cloudflare-aware key: `CF-Connecting-IP` → first `X-Forwarded-For` → `remote_addr` (else every user
      shares one bucket behind the proxy).
- [x] 429 returns JSON (`{error, detail}`) + `Retry-After`/`X-RateLimit-*` headers; normal pages unaffected.
- [x] Verified locally (login per-email, signup per-IP, JSON 429, pages unlimited) AND live (probe tripped
      429 on the 6th attempt; `content-type: application/json`, `retry-after: 59`).

Optional future hardening (not done): exponential backoff / temporary account lockout after N consecutive
failures; captcha after first failure. Current per-email + per-IP limits cover the main brute-force risk.

### 1b. Harden CSP with per-request nonces (replace `'unsafe-inline'`) — ✅ DONE & DEPLOYED (commit 82d7ae1c)
`'unsafe-inline'` in `script-src` (from item 1) largely negates CSP's anti-XSS value. The proper fix is a
nonce minted per request and attached to every inline script; then drop `'unsafe-inline'`.

Scope (surveyed 2026-06-15):
- 9 inline `<script>` blocks across 8 templates (`_base.html` has the shared helpers; `auth/login.html` +
  `auth/signup.html` are testable without auth; `dashboard/jobs/scrub/account/buy` need a logged-in session).
- 5 inline `on*=` handlers that **nonces do NOT cover** — must be refactored to `addEventListener`/event
  delegation: `_base.html:40` (gmLogout), `dashboard.html:91` (dismiss promo), `jobs.html:98`
  (deleteJob — built in a JS template literal, needs delegation/data-id), `scrub.html:178` (location.reload),
  `scrub.html:345` (clearFile).

Done:
- [x] `before_request`: `g.csp_nonce = secrets.token_urlsafe(16)`; context processor exposes `csp_nonce`;
      `_build_csp(nonce)` emits `script-src 'self' 'nonce-…' js.stripe.com cdn.jsdelivr.net` (no `'unsafe-inline'`).
- [x] Added `nonce="{{ csp_nonce }}"` to all 8 inline `<script>` tags (the 9th match was a comment).
- [x] Replaced all 5 inline `on*=` handlers with one delegated `data-action` listener in `_base.html`
      (`logout`, `reload`, `dismiss-promo`, `clear-file`, `delete-job` — delegation covers innerHTML-injected rows).
- [x] Verified locally (SQLite boot): `/login` + `/signup` header nonce == body nonce, unique per request.
- [x] Verified LIVE post-deploy: all 7 pages (`/login /signup /dashboard /jobs /scrub /account /buy`) →
      200 with header nonce == body nonce; 0 leftover `onclick=`; `script-src` has no `'unsafe-inline'`.

**Item #1 (headers + CSP) is now fully closed.** Only the unrelated future-CORS note remains under §ℹ️.

**Final CSP allowlist (live)** — what each non-`'self'` origin is for; keep in sync when adding integrations:
- `script-src`: `'nonce-<per-request>'` (inline blocks), `js.stripe.com` (Stripe.js), `cdn.jsdelivr.net` (chart.js)
- `frame-src`: `js.stripe.com` (Payment Element), `hooks.stripe.com` (**3-D Secure** challenge)
- `connect-src`: `api.stripe.com` (Stripe API), `cdn.jsdelivr.net` (chart.js sourcemap),
  `*.nyc3.digitaloceanspaces.com` + bucket host (**scrub upload/download** — browser PUTs/GETs straight to Spaces;
  derived from S3 config in `_spaces_connect_origins`)
- `style-src 'unsafe-inline'`: inline `<style>`/`style=` (only relaxation left; tighten later if styles externalized)
- `frame-ancestors 'none'`, `object-src 'none'`, `base-uri 'self'`: lockdowns

These last three CSP fixes were found by clicking through prod after the nonce deploy: chart.js sourcemap,
Spaces upload origin, and Stripe 3DS frame. Re-test any new external fetch/iframe/script against the CSP.

---

## 🟡 Launch blocker (business logic)

### 3. Stripe payment-bypass stub — ✅ NO LONGER APPLICABLE (Stripe is LIVE)
**Correction (2026-06-15):** prod is actually running Stripe **live** — `STRIPE_ENABLED=true`, `pk_live_…`,
fee pass-through on, signature-verified webhook configured. The earlier "deliverables are free" concern was
based on the stale `STRIPE_ENABLED=false` assumption and does **not** apply. The stub path only runs when
Stripe is disabled (dev/QA).

Residual hardening (low priority, defense-in-depth):
- [ ] Guard the stub so the "instant paid" branch can never run when `DEBUG=False`, in case `STRIPE_ENABLED`
      is ever toggled off in prod by mistake.

---

## 🟢 Account hygiene (Low severity)

### 4. Username enumeration + email verification — ✅ DONE & DEPLOYED (commit 3f690abf)
Signup used to return `409 "already exists"` (enumeration) and create a usable account instantly. Now full
email verification with a generic, non-enumerable response.

- [x] Signup returns a generic message whether or not the email exists; no auto-login, no `409`.
- [x] New signups are created **unverified** (`mailer_users.email_verified`, `verification_token`,
      `verification_sent_at`); existing users grandfathered verified via `ADD COLUMN ... DEFAULT 1`.
- [x] `GET /verify?token=` activates + auto-logs-in (single-use token, 24h TTL); bad/expired/used →
      `/login?verify=invalid|expired|already`.
- [x] Login `403`s when unverified (with `unverified` flag); login page offers a one-click resend.
- [x] `POST /api/auth/resend-verification` (rate-limited, generic response).
- [x] Verification email via the existing Mandrill relay (`EMAIL_ENABLED=true` in prod, confirmed).
- [x] Verified live: identical signup reply for same email twice; unverified login → 403; grandfathered
      existing account still logs in (no lockout); `/verify` bad token redirects correctly.
- [ ] **Human step (can't automate — needs a real inbox):** sign up with a real address, click the link,
      confirm it logs you in. Token expiry/resend also worth a manual pass.

### 5. Weak password policy — ✅ DONE & DEPLOYED (commit 3f690abf)
- [x] Signup rejects passwords found in the **Have I Been Pwned** corpus (k-anonymity range API — only the
      first 5 SHA-1 chars leave the server; fails OPEN on HIBP outage). Length ≥ 8 retained.
- [x] Verified live: `password` → `400` "appeared in a known data breach"; strong password accepted.
- Note: there's no password-CHANGE endpoint today (account update doesn't touch the password); apply the same
  `_password_pwned` check there if one is ever added.

---

## ℹ️ Defense-in-depth (informational — already mitigated)

- [ ] **`SECRET_KEY`**: strong in prod (good). Add a startup guard that refuses to boot if `SECRET_KEY == 'dev-key'`
      when `DEBUG=False`. Optional: periodic rotation (invalidates all sessions).
- [ ] **Sequential integer user/company IDs**: fine while authz holds (it does). UUIDs would be belt-and-suspenders;
      low priority.
- [ ] **CORS**: currently empty (`CORS_ORIGINS` unset) so no exposure. When the CX3 Dashboard origin is added, keep
      it a strict allowlist — never reflect an arbitrary `Origin` alongside `Allow-Credentials: true`.

---

## 🧹 Cleanup from the test itself

Testing created throwaway accounts on the prod DB (real JWTs, live CSP/auth checks, signup/verify flow).
**Every test account uses an `@example.com` email** (no real user would), so that's the safe selector.
Delete users first, then their now-orphaned companies:

```sql
-- preview
SELECT id, email, company_id FROM mailer_users WHERE email LIKE '%@example.com';

-- 1) remember the companies these test users created
CREATE TEMPORARY TABLE _test_co AS
  SELECT DISTINCT company_id AS id FROM mailer_users WHERE email LIKE '%@example.com';

-- 2) delete the test users
DELETE FROM mailer_users WHERE email LIKE '%@example.com';

-- 3) delete their companies only if no real users remain on them
DELETE c FROM mailer_companies c
  JOIN _test_co t ON t.id = c.id
 WHERE NOT EXISTS (SELECT 1 FROM mailer_users u WHERE u.company_id = c.id);

DROP TEMPORARY TABLE _test_co;
```
Prefixes seen: `pentest-*`, `pentest-csp*`, `pentest-sck*`, `pentest-reg*`, `pentest-buy*`, `pentest-pay*`,
`rl-verify*`, `rl-deptest*`, `breachtest-*` — all `@example.com`, created during 2026-06-15 testing.

---

## ✅ Confirmed secure — do NOT regress these

Access control on all protected endpoints · internal/admin API gated · **no IDOR** (every query scoped to JWT
`company_id`; downloads via scoped presigned S3 URLs) · JWT `algorithms` pinned to HS256 (no `alg:none`) · strong
prod `SECRET_KEY` · cookie flags `Secure; HttpOnly; SameSite=Lax` · payment-confirm re-verified server-side ·
webhook signature enforced · debug off / no stack traces / no exposed source · TRACE disabled.
