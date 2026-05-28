# RESOLVED — `/partners/publisher/dashboard` browser 404 / "site can't be reached"

**Date opened:** 2026-05-21
**Date resolved:** 2026-05-21
**Status:** RESOLVED with one residual cache-clear step required per-browser the first time.

---

## Root cause

The Mailer Portal was on port **5060**. Chromium-family browsers (Chrome, Edge, Brave, Arc, Opera, modern Electron) **hard-block** outbound HTTP requests to port 5060 because it is the SIP (Session Initiation Protocol) signaling port — a known cross-protocol attack surface. The block is in `net/base/port_util.cc` in the Chromium source. Ports 5060 and 5061 (and a handful of others, e.g. 6000, 6566, 6665–6669, 6697, 10080) are on `kRestrictedPorts` and the browser refuses to even open the TCP connection.

This is what made the symptom so confusing:

- **`curl` worked perfectly** — curl has no port blocklist. Every test we ran with `curl` returned the correct HTTP responses, which led us to believe the server side was healthy and the browser was the culprit, but kept us looking at caches and HSTS instead of the actual reason.
- **Other localhost apps worked fine for the user** — they were on ports outside the blocklist (e.g. 5050 is fine).
- **Flask's access log was completely silent** when the browser was hitting `:5060`, because the browser never sent the request at all. We initially mistook "no log entry" for "Flask is fine, must be cache."
- **Chrome's error UI showed "This site can't be reached"** rather than `ERR_UNSAFE_PORT` in the page body, masking the specific error.

A red herring: an earlier 301-permanent redirect from CX3's `/partners/publisher/*` → `http://127.0.0.1:5060/*` got cached in the browser, so even after moving the mailer to a non-restricted port, browsers that had hit the legacy URL once kept following the cached redirect back to `:5060`. Switched the redirect to **302 + `Cache-Control: no-store`** so this can't trap us again.

## Fix

1. **Moved the mailer off 5060.** New port is **5070** (next adjacent port not on any browser blocklist).
   - `mailer/.env` — `PORT=5070`
   - `mailer/.env.example` — `PORT=5070`
   - `mailer/config.py` — default fallback `5070`
2. **Updated CX3 Dashboard's legacy-URL forwarder** to point to the new mailer.
   - `cx3-dashboard/app/views.py` — `MAILER_PORTAL_URL` default changed to `http://127.0.0.1:5070`.
3. **Changed the forwarder from 301 to 302** and added `Cache-Control: no-store, no-cache, must-revalidate, max-age=0` + `Pragma: no-cache`, so this trap can't recur during dev port changes.
4. **Spec doc updated.** `Partner_portals_Spec.md` v1.2 — change-log entry + a new §11 documenting *why* the port is 5070 and which other ports to avoid.

## End-to-end verification

After restart, with `curl`:

```
http://127.0.0.1:5070/dashboard                       → 302 (auth-required redirect to /login)
http://127.0.0.1:5070/login                           → 200
http://127.0.0.1:5070/jobs                            → 302 → /login → 200
http://127.0.0.1:5050/partners/publisher/dashboard    → 302 → http://127.0.0.1:5070/dashboard → /login → 200 (no-store)
```

In the user's actual browser (verified via `open` + tailing the live access log):

```
GET / HTTP/1.1                              302
GET /login HTTP/1.1                         200
GET /static/css/mailer.css HTTP/1.1         200
GET /static/img/gravitas_logo.png HTTP/1.1  200
```

The browser **reached** Flask, which is what was failing before. The Gravitas Leads sign-in page renders correctly.

## Residual step the user has to do once

The CX3 → Mailer redirect was previously **cached as a 301** by any browser that hit `http://127.0.0.1:5050/partners/publisher/*` earlier in this session. Even with the server now returning 302+no-store, the **already-cached** 301 entry doesn't go away on its own — Chrome keeps following the old cached destination (`:5060`, blocked) and reports "site can't be reached."

To clear that cached redirect in Chrome **one time per browser profile**:

- Easy option: visit `http://127.0.0.1:5070/` directly. That URL has never been cached and works immediately.
- Or, to make the old `/partners/publisher/*` URL itself work: open DevTools on a tab pointed at `http://127.0.0.1:5050/` → **Application** tab → **Storage** in the left rail → **Clear site data**. Then revisit the legacy URL.
- Or, brute force: `chrome://settings/clearBrowserData` → "Cached images and files" → "Last hour" → Clear data.

After that one-time clear, the legacy URL `/partners/publisher/dashboard` follows the new 302 to `:5070` and lands on the mailer login.

## Restart commands

```bash
# Mailer (port 5070)
lsof -ti:5070 | xargs -r kill -9 ; sleep 1
cd /Users/rolf.louisdor/Desktop/mailer && PYTHONPATH=./lib python3 run.py > /tmp/mailer.log 2>&1 &

# CX3 Dashboard (port 5050)
lsof -ti:5050 | xargs -r kill -9 ; sleep 1
cd /Users/rolf.louisdor/Desktop/Dashboard/cx3-dashboard && PYTHONPATH=./lib python3 run.py > /tmp/cx3.log 2>&1 &
```

## Lesson for future port choices

Avoid these ports when picking a new dev port for any Chromium-targeted app, because the browser refuses to connect to them in plain HTTP regardless of what your server is doing:

`1, 7, 9, 11, 13, 15, 17, 19, 20, 21, 22, 23, 25, 37, 42, 43, 53, 69, 77, 79, 87, 95, 101, 102, 103, 104, 109, 110, 111, 113, 115, 117, 119, 123, 135, 137, 139, 143, 161, 179, 389, 427, 465, 512, 513, 514, 515, 526, 530, 531, 532, 540, 548, 554, 556, 563, 587, 601, 636, 989, 990, 993, 995, 1719, 1720, 1723, 2049, 3659, 4045, 5060, 5061, 6000, 6566, 6665, 6666, 6667, 6668, 6669, 6697, 10080`

For dev work, safe convention: stay in `5070–5099`, `8060–8099`, `8100+` for anything Chrome will touch. Diagnostic shortcut next time the same symptom appears: if `curl` works and Flask never logs the request, **suspect the browser refused to send it before suspecting any cache or HSTS**.
