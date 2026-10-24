"""Management of Site Control's own VPS without self-SSH.
Manual shell executes as a non-root service account. Changes are audited via API.
"""
from __future__ import annotations
import os
import pwd
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PRESETS={
    'uptime':['uptime'],
    'disk':['df','-h'],
    'memory':['free','-m'],
    'site_status':['systemctl','is-active','svgtracker.service'],
    'site_logs':['journalctl','-u','svgtracker.service','-n','80','--no-pager','--output=short-iso'],
    'files':['ls','-lah',str(ROOT)],
}

def execute(template, command=None):
    if template not in (*PRESETS,'manual'):raise ValueError('Неизвестная команда')
    if template=='manual':
        if not isinstance(command,str) or not 1<=len(command.strip())<=500 or '\x00' in command or '\n' in command:
            raise ValueError('Введите команду до 500 символов без перевода строки')
        args=['/bin/sh','-c',command]
    else:args=PRESETS[template]
    privilege_drop=None
    if os.geteuid()==0:
        try: account=pwd.getpwnam('www-data')
        except KeyError:raise RuntimeError('Нет непривилегированного пользователя www-data: локальная консоль отключена')
        def downgrade():
            os.setgroups([])
            os.setgid(account.pw_gid)
            os.setuid(account.pw_uid)
        privilege_drop=downgrade
    try:
        result=subprocess.run(args,cwd='/tmp',capture_output=True,text=True,errors='replace',timeout=25,
                              preexec_fn=privilege_drop,env={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':'/tmp','LANG':'C.UTF-8'})
        return {'ok':result.returncode==0,'exit_code':result.returncode,'output':((result.stdout or '')+(result.stderr or ''))[-100_000:] or '(Вывод пустой)'}
    except subprocess.TimeoutExpired:raise RuntimeError('Команда превысила лимит 25 секунд') from None
