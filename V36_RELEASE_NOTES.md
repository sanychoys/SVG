# SVGTracker V36 — Home shortcuts and backend security hardening

Based on V35. No data reset. Full replacement ZIP for existing Telegram admin deploy.

## UI changes
- Added a compact quick-access dock immediately below the week activity chart with **Search**, **SVG Assistant**, **Analytics**, and **Reports**; four tappable, evenly sized SVG controls with accessible labels.
- Finance Analytics remains inside Finance and linked from the Analytics Center. Automation rules remain available in Profile → Settings → Tools. All features and screens from V35 remain available.
- Mobile layout inspected at 320 / 375 / 390 / 430 px without horizontal overflow.

## Security hardening included in ZIP
- API binds to `127.0.0.1:8000` by default, instead of listening publicly on all interfaces. Use `SVGTRACKER_BIND_HOST` only when reverse proxy architecture requires it. Nginx should reverse-proxy to localhost.
- **Cookie-authenticated write requests** must present same-origin `Origin` or `Referer` matching `SVGTRACKER_PUBLIC_URL`. Telegram's HMAC-verified initData and iPhone Shortcut bearer-token requests are unaffected. Other origins return HTTP 403.
- API responses send `Cache-Control: private, no-store`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, and a restrictive API-only Content Security Policy.
- Reject known oversize API JSON requests (>2 MiB `Content-Length`) before parsing. Existing streaming file-upload per-chunk limits remain in effect.
- Tightened Telegram initData validation: no duplicated query params, max 8 KiB, signature shape, positive integer ID, and no timestamps beyond 60 seconds in the future.
- Sensitive DB, WAL, SHM and `config.py`/`.env` permissions set to owner-only at app startup. New files created by the service use `UMask=0077`.
- ZIP deploy and backup rollback explicitly mark *only public root-level HTML/CSS/JS/images* as readable (`0644`); other deployed files use `0600`.
- SQLite connections now close after each `with connect()` block, preventing accumulation of open descriptors.
- The systemd service adds `NoNewPrivileges`, `RestrictSUIDSGID`, `LockPersonality`. It **still runs as root** for the existing admin deploy workflow; this is an **unresolved architecture-level risk**.
- Added `deploy/NGINX_SECURITY.txt` as a **manual Nginx configuration template**; deploy via ZIP cannot safely edit existing live Nginx vhosts or firewall rules.
- Added `deploy/security_audit.py` for local file-permission checks and optional non-destructive HTTPS exposure checks.

## Required after installing
1. Back up DB and media using the existing admin backup workflow, then deploy V36.
2. Verify nginx reverse proxy points at **127.0.0.1:8000**. Confirm web version & Telegram Mini App login and save flows. Keep `SVGTRACKER_PUBLIC_URL` equal to the actual public HTTPS origin; Origin checks use it.
3. Review `deploy/NGINX_SECURITY.txt` and merge allowlist locations into the existing TLS server configuration. **This manual step is essential** if Nginx currently exposes arbitrary files in `/var/www/SVG`.
4. Run `sudo nginx -t` and reload Nginx only if valid. Check `https://your-domain/svgtracker.db`, `/config.py`, `/.env`, `/.git/config` and `/deploy/admin_deploy.py` give 403/404, never 200/302.
5. On server: `sudo python3 /var/www/SVG/deploy/security_audit.py --root /var/www/SVG --public-url https://your-domain`; evaluate any warnings. Check port 8000 unreachable externally, TLS/HTTPS, UFW/Nginx firewall, non-public backups and file permissions.
6. Longer term: separate admin deployment privileges from public API/bot and migrate service from `User=root` to a restricted service account; rotate credentials if any sensitive paths have previously been exposed.

## Validation carried out in sandbox
- 18 unit/regression tests, including HMAC initData, CSRF with web-cookie sessions, HTTP no-store/security headers, 413 payload limits, SQLite connection lifecycle, cross-account isolation and report/automation test suites.
- Frontend structure validated at 320 / 375 / 390 / 430 px; deploy-validator verifies all onclick handlers and Python/JavaScript syntax.
- **Not performed**: authenticated penetration testing of your actual VPS, real Nginx configuration audit, TLS/OS patch status, restore verification of production backups. No one should claim that zero vulnerabilities remain solely on the basis of source-code review.
