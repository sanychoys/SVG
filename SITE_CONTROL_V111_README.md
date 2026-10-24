# Site Control v1.11 — console permission fix

Fixes the `PermissionError: [Errno 1] Operation not permitted` from
`subprocess.Popen` on the platform console. Native Linux `/proc`,
`os.statvfs`, in-process status and Site Control log-tail replace
credential-changing subprocess calls for diagnostic presets. Root manual
shell commands stay denied; a dedicated least-privilege runner is necessary
for writable operations if the web unit currently runs as root.

See `ИНСТРУКЦИЯ.txt` and `АУДИТ_И_ТЕСТЫ.txt`. Does not migrate DB or admins.
