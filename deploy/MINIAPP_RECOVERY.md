# SVGTracker — VPS Mini App recovery checklist (V39)

## 1. Read-only checks first

Run these from an SSH shell on the VPS. They display status and do not delete data:

```bash
sudo systemctl status svgtracker --no-pager -l
sudo journalctl -u svgtracker -n 120 --no-pager
ps -eo pid,ppid,cmd | grep -E '[m]ain.py|[a]iogram|[u]vicorn'
curl -sS -i http://127.0.0.1:8000/api/test
curl -sS -i https://starslix.ru/api/test
curl -sS -i https://starslix.ru/api/user
```

Expected after V39: `/api/test` HTTP 200 with `"version":"39"` on BOTH paths. `/api/user` without Telegram credentials should return **401**. It must not be `404` (Nginx routing mismatch) or `200` without credentials (security issue).

Check that `https://starslix.ru/` serves `script.js?v=39` and `product.js?v=39`; JS/CSS requests themselves should return HTTP 200 and correct content types. If the local API returns 200 but public `/api/test` returns 404/502, inspect Nginx proxy configuration. **Do not expose port 8000 to the internet.**

## 2. Telegram polling flood

There must be only one `GetUpdates` poller for one bot token. V39 prevents multiple V39 processes on the **same VPS** from polling simultaneously; it cannot detect an old bot instance on another server, a locally running old version, or a webhook still configured.

Find and stop the **duplicate** process/service only after confirming its owner and purpose. Do not indiscriminately kill every Python process: SSH/deploy helpers and other bots may be running. Prefer `sudo systemctl restart svgtracker` once after confirming that only its intended service owns the bot.

Temporary `TelegramRetryAfter`, Telegram `Bad Gateway`, and `ServerDisconnectedError` during a normal service restart do not themselves indicate database corruption.

## 3. Mini App frontend

Check via Telegram, not just Safari. Close Mini App fully, then reopen through the bot's official button. If it says the signed Telegram session has expired, close and reopen rather than forcing an unsigned request. The new error screen provides a retry button for recoverable HTTP/network failures.

Use `/diagnostics`, `/logs_frontend`, `/logs_backend`, `/logs_bot` and `/logs_system` from the admin bot to pinpoint a remaining issue. Never paste bot tokens, Telegram initData, session cookies, SSH keys or database contents into chat or public issue reports.

## 4. Backups and rollback

Use the supported `/backup` before installing updates; never remove `svgtracker.db`, WAL/SHM or user uploads as an attempted fix. If the admin deployment fails its ready/version check, the deploy system should roll back the code. If the Telegram bot itself is unavailable, use your existing VPS access and restore procedure rather than uploading unverified files into the public website.

The delivered ZIP does **not** apply or verify your actual Nginx, firewall or TLS settings automatically. Fix a production misconfiguration only after reviewing its live configuration and taking a backup.
