#!/usr/bin/env python3
"""One-time VPS bootstrap. Run from phone SSH: sudo python3 deploy/configure_update_service.py

Never expose this script through the admin web API. Installs a fixed systemd path
unit to start an updater separately from the running web app.
"""
from __future__ import annotations
import os,sys,shutil,subprocess
from pathlib import Path

if os.geteuid()!=0:raise SystemExit('Нужен sudo (разовая настройка системной службы)')
root=Path(__file__).resolve().parents[1]
state=Path(os.environ.get('SITE_CONTROL_PROJECT_FILES_DIR','/var/lib/sitecontrol/project-files'))/'self-updates'
state.mkdir(mode=0o700,parents=True,exist_ok=True)
worker_target=Path('/usr/local/lib/sitecontrol-updater/worker.py')
worker_target.parent.mkdir(parents=True,exist_ok=True)
shutil.copyfile(root/'deploy'/'update_worker.py',worker_target)
worker_target.chmod(0o644)
service_name=os.environ.get('SVGTRACKER_SERVICE_NAME','svgtracker')
service=f"""[Unit]
Description=Site Control file updater (on demand)
After=local-fs.target
[Service]
Type=oneshot
User=root
Group=root
WorkingDirectory={root}
Environment=PYTHONPATH={root}
Environment=SITE_CONTROL_PROJECT_ROOT={root}
Environment=SITE_CONTROL_PROJECT_FILES_DIR={state.parent}
Environment=SVGTRACKER_SERVICE_NAME={service_name}
ExecStart={sys.executable} {worker_target} {state}
TimeoutStartSec=120
NoNewPrivileges=yes
PrivateTmp=yes
ProtectHome=read-only
"""
path=f"""[Unit]
Description=Watch approved Site Control updates
[Path]
PathExistsGlob={state}/*.ready
Unit=sitecontrol-updater.service
[Install]
WantedBy=multi-user.target
"""
for name,content in [('sitecontrol-updater.service',service),('sitecontrol-updater.path',path)]:
    target=Path('/etc/systemd/system')/name
    target.write_text(content,encoding='utf-8')
    target.chmod(0o644)
marker=Path('/etc/sitecontrol/updater-enabled')
marker.parent.mkdir(parents=True,exist_ok=True)
marker.write_text('Updater path service installed\n')
marker.chmod(0o644)
for cmd in (['systemctl','daemon-reload'],['systemctl','enable','--now','sitecontrol-updater.path']):
    subprocess.run(cmd,check=True)
print('Готово: service + path установлены. Перезапуск сайта не требуется.')
print('Проверка: systemctl status sitecontrol-updater.path --no-pager')
