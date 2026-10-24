# Site Control 1.3 — deployment compatibility

The live V48 ZIP installer and systemd service still use legacy service/path names. They are retained **only as an installation bridge**, not as tracker functionality. Renaming the service and repository is a separate VPS migration.

- Telegram `/start`: Mini App for admin automatically; unauthorized users have no access.
- `/status`: concise status as Telegram **text**, then the latest `ИНСТРУКЦИЯ.txt` attachment.
- `/sites`, `/newsite`, `/edit`, `/publish`: project operations from admin Telegram bot.
- `/servers`, `/server`, `/exec`: remote VPS management. SSH client required on central VPS.
- `/logs`: bot/site logs; exceptions retain stacktraces.
- `/backup` before update; save external offsite copy.
- `/cleanup_legacy`: **explicit** confirmed legacy clean after the updated app passes health checks, with a verified backup and Git synchronization.
- Runtime database `/var/lib/sitecontrol/sitecontrol.db` and private SSH keys `/var/lib/sitecontrol/ssh` **must not be deleted**.
- GitHub preflight, health check and transactional rollback remain.
- This ZIP **does not** install OpenSSH on the VPS or change Nginx, TLS, DNS, systemd unit name.
