# Site Control v1.0 — deployment compatibility notes

1. Retains original `deploy/admin_deploy.py` from SVGTracker V48.
2. V48 ZIP validator requires `index.html` to reference `script.js`, `style.css`, `product.js`, `product.css`; new app meets this contract.
3. Runtime health endpoint retains `GET /api/test` and `{"status":"ok"}` for both deployment and watchdog.
4. Uses the old `svgtracker.service` name and existing main bot BOT_TOKEN: renaming service/bot requires separate infrastructure/account changes, not feasible through ZIP overlay alone.
5. All finance, workout, old notes and scheduling endpoints disappear because new `main.py` does not import the old modules.
6. Previous protected production SQLite files are neither overwritten nor deleted by the ZIP.
7. Production state lives in `/var/lib/sitecontrol` and must be backed up separately.
8. Future ZIP uploads stay supported in the new admin bot by delegating to the preserved systemd-run deploy helper.
