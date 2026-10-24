# Site Control 1.2 — deployment compatibility

The existing systemd service name and Git-backed ZIP installer remain unchanged to avoid
breaking the live VPS. The new UI, administration logic and Mini App are Site Control only.

- Run /backup before first install; export a copy outside the server.
- /start opens the Mini App, /status sends TXT, /logs sends separate bot/site logs.
- /cleanup_legacy is a separate confirmed and backed-up migration, never automatic.
- Live data lives in /var/lib/sitecontrol; config.py and Nginx are not overwritten.
- Main app is bound to the existing local port 8000. Requires HTTPS public Site URL.
- GitHub ZIP preflight, health check and rollback are kept for compatibility.
