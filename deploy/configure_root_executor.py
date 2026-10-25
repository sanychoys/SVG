#!/usr/bin/env python3
"""Explicit operator opt-in for the privileged, permission-controlled command executor.

Usage from an SSH session (NOT from the Site Control web UI):
  sudo python3 deploy/configure_root_executor.py --web-user root --owner-id 382257126 --enable-root

Prefer a NON-ROOT web service user, e.g. --web-user www-data. A separate root
executor is still a major security trade-off; protect the frontend carefully.
"""
from __future__ import annotations
import argparse
import os
import pwd
import shutil
import subprocess
import sys
from pathlib import Path

p=argparse.ArgumentParser()
p.add_argument('--web-user',required=True,help='Linux user running the web application (check systemctl status/show)')
p.add_argument('--owner-id',required=True,type=int,help='Telegram ID of Site Control owner')
p.add_argument('--roles-dir',default=os.environ.get('SVGTRACKER_ADMIN_STATE_DIR','/var/lib/svgtracker-admin'))
p.add_argument('--enable-root',action='store_true',help='I explicitly approve privileged shell commands from my panel')
a=p.parse_args()
if os.geteuid()!=0:raise SystemExit('Запускайте через sudo из SSH, не из веб-приложения')
if not a.enable_root or a.owner_id<=0:raise SystemExit('Явно добавьте --enable-root и --owner-id')
try:uid=pwd.getpwnam(a.web_user).pw_uid
except KeyError:raise SystemExit('Linux-пользователь службы не найден')
source=Path(__file__).resolve().parents[1]/'sitecontrol'/'root_executor.py'
if not source.is_file():raise SystemExit('Не найден root_executor.py')
folder=Path('/usr/local/libexec')
folder.mkdir(mode=0o755,parents=True,exist_ok=True)
target=folder/'sitecontrol-root-executor.py'
shutil.copyfile(source,target)
os.chown(target,0,0)
os.chmod(target,0o700)
unit=Path('/etc/systemd/system/sitecontrol-root-executor.service')
content=f'''[Unit]
Description=Site Control on-demand root executor (explicitly enabled)
After=local-fs.target

[Service]
Type=simple
User=root
Group=root
ExecStart={sys.executable} {target} --serve --socket /run/sitecontrol-root/root.sock --uid {uid} --owner-id {a.owner_id} --roles-dir {a.roles_dir}
Restart=on-failure
RestartSec=2
UMask=0077
PrivateTmp=yes
NoNewPrivileges=no
TimeoutStopSec=8

[Install]
WantedBy=multi-user.target
'''
unit.write_text(content,encoding='utf-8')
os.chown(unit,0,0)
os.chmod(unit,0o644)
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','enable','--now',unit.name],check=True)
subprocess.run(['systemctl','restart',unit.name],check=True)
print('Root executor включён для владельца и администраторов с разрешением platform.root; Linux UID',uid)
print('Проверка: systemctl status sitecontrol-root-executor.service --no-pager')
print('ОТКЛЮЧЕНИЕ: sudo systemctl disable --now sitecontrol-root-executor.service')
