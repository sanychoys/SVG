# SVGTracker V39 — Mini App boot and polling stability

Based on V38. This is a full code release with no production database, credentials, users' attachments or bot tokens.

## Fixed

1. **Startup authentication**: `/api/user` now has a signed, read-only `GET` endpoint. The Mini App checks identity before loading personal finance/training data. If authentication fails, the splash view shows a message and **Repeat** button instead of waiting forever or silently rendering cached data.
2. **Legacy `/api/authenticate`**: implemented as a restricted compatibility endpoint for stale clients; accepts **only Telegram-signed initData** (header `X-Telegram-Init-Data`, or JSON `initData`/`init_data`). It does not accept a plain user ID, create a new cookie, expose tokens, or bypass Telegram signature verification. The current V39 frontend does not call it.
3. **Telegram bootstrap**: missing/unavailable Telegram SDK or initData produces an actionable retry screen instead of an indefinite loading skeleton. The Telegram SDK remains async/nonblocking.
4. **Single polling instance per VPS**: `fcntl.flock` on the private `SVGTRACKER_ADMIN_STATE_DIR/service-instance.lock` ensures a second V39 instance on the same host waits rather than flooding Telegram with another `GetUpdates` poller. This cannot prevent a duplicate bot running on a different VPS or with an older code version.
5. **Graceful service shutdown**: signal handling is centralized on uvicorn and aiogram does not register competing SIGTERM handlers. Clean deploy shutdowns no longer generate a false "unexpected component stopped" notification. Polling long-poll timeout is increased to 20 seconds.
6. **Stricter deployment verification**: `/api/test` readiness must report the new version; stale healthy processes do not count as successful deployment. ZIP validator also checks JS/CSS version query arguments against `APP_VERSION`.
7. **More accurate `/diagnostics`**: probes unauthenticated identity endpoints and explicitly expects HTTP 401, not 404, without requiring a Telegram token.

## What the supplied logs mean

- `POST /api/authenticate 404`: V38 did not define this path and V38 frontend did not request it; a stale client or unrelated external request is possible. This request alone does NOT establish why Mini App failed to open.
- Telegram `GetUpdates` flood: Telegram rate-limiting, often due to duplicate pollers or rapid retries; local singleton prevents a common cause but not external duplicates.
- `Bad Gateway` / `ServerDisconnectedError`: Telegram connectivity and termination events; may be transient.
- `SIGTERM`: normal on deploy/restart; do not classify it as an app failure without other evidence.
- `/api/vendor/phpunit/…` and `/api/mcp` 404: likely internet scans; both paths appropriately return 404.

## Safety and deployment

1. Back up the existing database and attachments before installing; if the bot is working, use `/backup`.
2. Deploy this ZIP using the existing admin workflow, which restarts svgtracker.service and performs a rollback on failed health check.
3. Verify `/api/test` reports `{"status":"ok","version":"39"}` through both local API and public Nginx proxy; verify static resources include `?v=39`.
4. Run `/diagnostics`, `/logs_frontend`, `/logs_bot` and `/logs_system` from admin bot if startup issues persist.
5. Test inside actual Telegram Mini App, both first and subsequent launches. The isolated integration suite cannot exercise Telegram servers or production Nginx.

**Server configuration still matters:** Nginx must serve the V39 static files and proxy `/api/*` to the running local API. If duplicate bot pollers run on another machine or a second independent service, stop those duplicates. See `deploy/MINIAPP_RECOVERY.md`.

## Verification in test environment

- 64 Python regressions passed (including 20 simultaneous training/finance requests from previous suite; signature rejection and new signed compatibility endpoint; single-process flock protection).
- Mini App error → retry → successful hydration verified in isolated Chromium at 320, 390 and 430 px without JS errors or horizontal overflow. Missing SDK recovery also verified.
- JS/Python syntax and ZIP consistency, deploy validator checks.
