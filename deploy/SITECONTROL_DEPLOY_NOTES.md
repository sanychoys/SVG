# Site Control 1.4 — installation compatibility

The current ZIP deployment pipeline retains legacy service and directory identifiers (`svgtracker.service`, `/var/www/SVG`, `/var/lib/svgtracker-admin`) **only** as an upgrade bridge for V48. These identifiers are not tracker functionality. Renaming/migrating the live service requires an independent, tested VPS operation.

- `/start` opens a compact Site Control menu and authenticated Telegram Mini App.
- `/status` sends a short message and the current `ИНСТРУКЦИЯ.txt` attachment.
- `/logs` gives the bot and site/API journals; complete exceptions are logged privately.
- Site Control project, server, administrator, ZIP and backup controls are behind `/start` and the web/Mini App UI.
- The ZIP is code-only. It does **not** remove live databases, token files, SSH keys, `.env`, `config.py`, Git history or Nginx configuration.
- The owner is Telegram ID `382257126`, immutable in `AdminRegistry`.
- Failed `systemd-run` cannot be remedied by code in an archive that has not yet been deployed. Follow `ВОССТАНОВЛЕНИЕ_ДЕПЛОЯ.txt` for manual owner-authorized recovery via VPS SSH; avoid granting global passwordless sudo privileges to the bot.
- ZIP deployment preserves the GitHub preflight and rollback safeguards of the V48-compatible helper.
