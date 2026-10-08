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
- `/logs` — recent service logs
- `/restart` — safe systemd restart

## ZIP deploy safety

The deploy worker rejects unsafe archive paths and symlinks, limits archive size, checks Python and JavaScript syntax, checks duplicate HTML IDs and inline handler references, creates a backup, applies only deployable project files, restarts the service, performs an API health check, and automatically rolls back code if health fails.

Protected production data is never overwritten by ZIP deploy: `config.py`, `.env`, `svgtracker.db`, SQLite WAL/SHM, `.git`, `venv`, uploads and logs.

A ZIP deploy updates production directly and does not push to GitHub. `/server_status` shows whether the production Git working tree has local changes.
