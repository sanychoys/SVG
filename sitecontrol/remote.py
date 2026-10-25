"""Remote VPS maintenance: key-only SSH, pinned host keys, bounded output/time.

No key upload or automatic host-key acceptance: administrators provision an
SSH account (including root when explicitly configured), authorized_keys
and a pinned known_hosts entry. Root keys are not enabled automatically.
"""
from __future__ import annotations
import os
import re
import shutil
import shlex
import subprocess
from pathlib import Path
from . import store

KEY_DIR = Path(os.environ.get('SITE_CONTROL_SSH_KEY_DIR','/var/lib/sitecontrol/ssh'))
HOST = re.compile(r'^(?=.{1,253}$)[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?$')
USER = re.compile(r'^[a-z_][a-z0-9_-]{0,31}$')
ALIAS = re.compile(r'^[a-zA-Z0-9_-]{1,48}$')
SERVICE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_.@-]{0,79}$')
TEMPLATES = {
    'uptime': ('Время работы','uptime'),
    'disk': ('Диски','df -h --output=source,size,used,avail,pcent,target'),
    'memory': ('Память','free -h'),
    'services': ('Активные службы','systemctl --no-pager --failed'),
    'nginx_check': ('Проверка Nginx','nginx -t'),
    'site_logs': ('Логи сайта','journalctl --no-pager -n 80 --since="24 hours ago" --utc --output=short-iso -u {service}'),
    'site_status': ('Состояние сайта','systemctl --no-pager status {service}'),
    'site_restart': ('Перезапустить сервис','sudo -n systemctl restart {service}'),
    'processes': ('Процессы','ps -eo pid,user,%cpu,%mem,comm --sort=-%cpu | head -n 22'),
    'network': ('Сеть','ip -brief address'),
    'inodes': ('Заполнение inode','df -i'),
    'ports': ('Порты','ss -tuln'),
    'docker_status': ('Docker','docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"'),
    'system_info': ('ОС и ядро','uname -a'),
    'recent_errors': ('Ошибки','journalctl -p err -n 35 --no-pager --since="24 hours ago"'),
    'disk_usage': ('Каталоги','du -h -d 1 /var/www /srv 2>/dev/null | tail -n 30'),

}


def validate(name, host, port, username, key_alias, service, site_id=None, deploy_root=""):
    if not isinstance(name,str) or not 2 <= len(name.strip()) <= 70:raise ValueError('Название сервера: 2–70 символов')
    if not isinstance(host,str) or not HOST.fullmatch(host) or '..' in host:raise ValueError('Некорректный адрес сервера')
    try:port=int(port)
    except (ValueError,TypeError):raise ValueError('Некорректный SSH-порт')
    if not 1 <= port <= 65535:raise ValueError('Некорректный SSH-порт')
    if not isinstance(username,str) or not USER.fullmatch(username):raise ValueError('Некорректное имя SSH-пользователя')
    if not isinstance(key_alias,str) or not ALIAS.fullmatch(key_alias):raise ValueError('Некорректный идентификатор ключа')
    if not isinstance(service,str) or not SERVICE.fullmatch(service):raise ValueError('Некорректное имя службы')
    if site_id not in ('',None):
        try:site_id=int(site_id)
        except (ValueError,TypeError):raise ValueError('Некорректный проект')
        if not store.get_site(site_id):raise ValueError('Проект не найден')
    else:site_id=None
    if deploy_root and (not isinstance(deploy_root,str) or '..' in deploy_root.split('/') or len(deploy_root)>250 or not deploy_root.startswith(('/var/www/','/srv/','/home/'))):raise ValueError('Папка проекта должна начинаться с /srv/, /var/www/ или /home/')
    return {'name':name.strip(),'host':host,'port':port,'username':username,'key_alias':key_alias,'service':service,'site_id':site_id,'deploy_root':deploy_root}


def build_command(server, template, custom=None):
    if template == 'manual':
        if not isinstance(custom,str) or not 1 <= len(custom.strip()) <= 1200 or any(ord(c)<32 and c!='\t' for c in custom):
            raise ValueError('Команда должна быть однострочной, до 1200 символов')
        return custom.strip()
    if template not in TEMPLATES:raise ValueError('Неизвестный шаблон команды')
    cmd=TEMPLATES[template][1]
    if template=='site_restart' and server['username']=='root':
        cmd='systemctl restart {service}'
    return cmd.replace('{service}',server['service'])


def execute(server, template, custom=None):
    cmd=build_command(server,template,custom)
    if not shutil.which('ssh'):raise RuntimeError('На центральном VPS не установлен openssh-client')
    root=KEY_DIR.resolve()
    private=root/(server['key_alias']+'.key')
    hosts=root/'known_hosts'
    if not private.is_file() or private.is_symlink() or not hosts.is_file() or hosts.is_symlink():
        raise RuntimeError('Нет SSH-ключа или проверенного known_hosts. Смотрите ИНСТРУКЦИЯ.txt')
    if private.resolve().parent!=root:raise RuntimeError('Недопустимый путь SSH-ключа')
    argv=['ssh','-T','-n','-i',str(private),'-p',str(server['port']),'-o','BatchMode=yes',
          '-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+str(hosts),
          '-o','GlobalKnownHostsFile=/dev/null','-o','ConnectTimeout=7',
          '-o','ConnectionAttempts=1','-o','ServerAliveInterval=5','-o','ServerAliveCountMax=1',
          '-o','IdentitiesOnly=yes','-o','LogLevel=ERROR','--',
          f'{server["username"]}@{server["host"]}',cmd]
    # ssh uses command execution on the remote side. Access is strictly admins only;
    # manual commands require a separate user confirmation in UI/TG.
    result=subprocess.run(argv,capture_output=True,timeout=25,check=False)
    data=(result.stdout+b'\n'+result.stderr)[:14000].decode('utf-8','replace').strip()
    return {'ok':result.returncode==0,'exit_code':result.returncode,'output':data or '(нет вывода)','truncated':len(result.stdout)+len(result.stderr)>14000}


def public_key(alias:str, create:bool=False):
    if not isinstance(alias,str) or not ALIAS.fullmatch(alias):raise ValueError('Имя ключа: буквы, цифры, _ или -')
    KEY_DIR.mkdir(parents=True,exist_ok=True,mode=0o700)
    KEY_DIR.chmod(0o700)
    priv=KEY_DIR/(alias+'.key')
    pub=Path(str(priv)+'.pub')
    if create and not priv.exists():
        if pub.exists():raise ValueError('Публичный ключ существует без приватного')
        if shutil.which('ssh-keygen'):
            p=subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(priv)],capture_output=True,text=True,timeout=12)
            if p.returncode:raise RuntimeError('Не удалось создать SSH-ключ: '+p.stderr[:300])
        else:
            # Fallback for Python environments without openssh-client; deployment
            # still requires the ssh binary to actually connect to remote VPS.
            try:
                from cryptography.hazmat.primitives import serialization
                from cryptography.hazmat.primitives.asymmetric import ed25519
            except ImportError:raise RuntimeError('Установите openssh-client на сервер Site Control') from None
            key=ed25519.Ed25519PrivateKey.generate()
            secret=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.OpenSSH,serialization.NoEncryption())
            fd=os.open(priv,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as stream:stream.write(secret)
            pub.write_bytes(key.public_key().public_bytes(serialization.Encoding.OpenSSH,serialization.PublicFormat.OpenSSH)+b' sitecontrol\n')
        priv.chmod(0o600);pub.chmod(0o644)
    if not priv.is_file() or priv.is_symlink() or not pub.is_file() or pub.is_symlink():raise LookupError('Ключ не найден')
    return pub.read_text('utf-8').strip()


def host_scan(host:str, port:int=22):
    validate('Проверка',host,port,'scdeploy','default','nginx')
    if not shutil.which('ssh-keyscan') or not shutil.which('ssh-keygen'):
        raise RuntimeError('Установите openssh-client на VPS Site Control')
    proc=subprocess.run(['ssh-keyscan','-T','6','-p',str(port),'-t','ed25519,ecdsa,rsa',host],capture_output=True,timeout=12)
    lines=[line.strip() for line in proc.stdout.decode('utf-8','replace').splitlines() if line and not line.startswith('#')]
    candidates={}
    for line in lines:
        fields=line.split()
        if len(fields)<3 or fields[1] not in ('ssh-ed25519','ecdsa-sha2-nistp256','ecdsa-sha2-nistp384','ecdsa-sha2-nistp521','ssh-rsa'):
            continue
        key=' '.join(fields[:3]);candidates[(fields[1],fields[2])]=key
    for preferred in ('ssh-ed25519','ecdsa-sha2-nistp256','ecdsa-sha2-nistp384','ecdsa-sha2-nistp521','ssh-rsa'):
        selected=next((v for (type_,_),v in candidates.items() if type_==preferred),None)
        if selected:break
    else:
        raise RuntimeError('Не получен SSH-ключ: проверьте IP, порт 22, доступность SSH и firewall')
    printed=subprocess.run(['ssh-keygen','-lf','-'],input=(selected+'\n').encode(),capture_output=True,timeout=5)
    if printed.returncode:raise RuntimeError('Не удалось прочитать SSH-отпечаток')
    match=re.search(r'SHA256:[A-Za-z0-9+/]+',printed.stdout.decode('utf-8','replace'))
    if not match:raise RuntimeError('Нет SSH-отпечатка сервера')
    return selected,match.group(0)


def trust_host(key:str):
    if not isinstance(key,str) or not re.fullmatch(r'\S+ (?:ssh-ed25519|ecdsa-sha2-nistp(?:256|384|521)|ssh-rsa) [A-Za-z0-9+/=]+(?: [^\r\n]{0,150})?',key):
        raise ValueError('Некорректный ключ сервера')
    KEY_DIR.mkdir(parents=True,exist_ok=True,mode=0o700)
    KEY_DIR.chmod(0o700)
    dest=KEY_DIR/'known_hosts'
    if dest.is_symlink():raise ValueError('Некорректный файл known_hosts')
    with dest.open('a',encoding='utf-8') as fh:fh.write(key+'\n')
    dest.chmod(0o600)


def prepare_deploy_root(server, slug):
    """Create one deploy folder in SSH user's home and report absolute path.

    Requires a pre-authorized SSH key and pinned host key. Never creates dirs on
    the central Site Control server or assumes root privileges on remote VPS.
    """
    if not isinstance(slug,str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{2,55}',slug):
        raise ValueError('Некорректный идентификатор проекта')
    code=("import os, pathlib, sys; "
          "home=pathlib.Path.home().resolve(); "
          "dest=home/'sitecontrol-sites'/sys.argv[1]; "
          "dest.mkdir(parents=True,exist_ok=True,mode=0o755); "
          "assert dest.resolve().is_relative_to(home); "
          "print(dest.resolve())")
    cmd='python3 -c '+shlex.quote(code)+' '+shlex.quote(slug)
    result=execute(server,'manual',cmd)
    if not result['ok']:
        raise RuntimeError('Не удалось создать папку на клиентском VPS. '+result['output'][-240:])
    root=result['output'].strip().splitlines()[-1]
    # remote path must be absolute within supported user home and no traversal
    if not (root.startswith(('/home/','/root/')) and '..' not in root.split('/') and len(root)<240
            and root.endswith('/sitecontrol-sites/'+slug)):
        raise RuntimeError('VPS сообщил неожиданный путь проекта')
    return root
