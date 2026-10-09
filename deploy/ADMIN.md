# SVGTracker Admin

Admin Telegram ID is read from `SVGTRACKER_ADMIN_ID` and defaults to the project owner ID configured in `main.py`.

## Bot commands

- `/admin` — admin panel
- `/deploy` — instructions; the admin can also send a `.zip` directly
- `/deploy_status` — last deployment result
- `/server_status` — service/API/watchdog/Git/disk/RAM status
- `/backup` — code backup + consistent SQLite snapshot
- `/backups` — recent backups
- `/rollback` — restore code from the latest backup (database is intentionally not rolled back)
- `/errors` — recent errors/warnings from journald
- `/logs` — общие service logs
- `/logs_frontend` — ошибки/этапы загрузки Safari и Telegram WebView
- `/logs_backend` — FastAPI/Uvicorn/API
- `/logs_bot` — aiogram/Telegram polling
- `/logs_system` — systemd/watchdog
- `/logs_deploy` — ZIP deploy + GitHub publish
- `/diagnostics` — сводный снимок сервера + последние frontend events
- `/restart` — safe systemd restart
- `/github_setup` — generate the repository-scoped GitHub Deploy Key and show its public half
- `/github_test` — verify GitHub write access and switch `origin` to SSH
- `/github_status` — compare production with `origin/main`
- `/github_sync` — one-time/bootstrap commit + push of the current production tree

## One-time GitHub setup from a phone

1. Run `/github_setup` in Telegram.
2. Copy the public key returned by the bot.
3. Open the repository in GitHub → Settings → Deploy keys → Add deploy key.
4. Paste the public key and enable **Allow write access**.
5. Run `/github_test`.
6. Run `/github_sync` once if this release was installed by the older direct ZIP deployer.

The private key is generated on the VPS at `/root/.ssh/svgtracker_github` and is never sent to Telegram or stored in the repository.

## Transactional ZIP deploy

V21+ treats GitHub as the source of truth. Before a ZIP is applied, the worker verifies that the VPS working tree is clean and that `HEAD` exactly matches `origin/<branch>`.

The deployment transaction is:

1. Validate ZIP and Git/GitHub state.
2. Create a production backup.
3. Apply only permitted project files.
4. Restart SVGTracker and wait for the API health check.
5. Commit the applied project files.
6. Push the commit to GitHub.
7. Report success to Telegram.

If the API health check fails, production is restored from backup. If GitHub commit/push fails, production is also restored and Git is reset to the pre-deploy commit, so GitHub and the VPS do not silently diverge.

Protected production data is never overwritten by ZIP deploy: `config.py`, `.env`, `svgtracker.db`, SQLite WAL/SHM, `.git`, `venv`, uploads and logs.

## V22 frontend resilience
- Telegram WebApp SDK is loaded asynchronously and can no longer block the whole UI.
- `script.js` and `style.css` are cache-busted with `?v=22`.
- ZIP deploy validation rejects missing frontend assets and a blocking Telegram SDK tag.
- `/server_status` reports a separate Frontend status.

## V23 diagnostics
- Browser bootstrap starts logging before `script.js` is loaded.
- Captures resource failures, JS errors, unhandled promises, API/network errors and startup stages.
- Frontend telemetry is stored in `/var/lib/svgtracker-admin/logs/frontend.log`; credentials and Telegram init data are not logged.
- Deploy lifecycle is stored in `/var/lib/svgtracker-admin/logs/deploy.log`.
- `/server_status` shows whether frontend telemetry is arriving and the last startup stage.
- The diagnostic endpoint is rate-limited and intentionally works without Telegram auth so it can report failures of the Telegram SDK itself.

## Local time / timezone

V24 stores the user's device timezone when the Telegram Mini App opens. Admin log commands (`/logs*`, `/errors`, `/diagnostics`, backup timestamps and deploy notifications) render timestamps in the admin's saved device timezone while the database continues to store UTC internally. `/timezone` shows the active timezone and can override it manually, e.g. `/timezone +04:00` or `/timezone Europe/Moscow`.
