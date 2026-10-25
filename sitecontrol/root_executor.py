"""Site Control root command transport.

Root commands run ONLY in an explicitly installed, separate systemd service.
The public web process never starts the privileged server, owns its code, or
receives credentials for it. A Unix peer UID check and owner-ID gate are used;
the root daemon also verifies the actor against the persistent role registry.
Commands require authorization and audit logging; the interface does not demand a second confirmation.

WARNING: a compromised allowed web-service UID can operate this helper.
Enable only when the operator accepts that risk; limit Site Control exposure,
protect admin sessions with Telegram and prefer dedicated managed VPSes.
"""
from __future__ import annotations
import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import socketserver
import stat
import struct
import subprocess
import threading
import time

SOCKET_PATH = Path(os.environ.get('SITE_CONTROL_ROOT_SOCKET','/run/sitecontrol-root/root.sock'))
MAX_COMMAND=1200
MAX_OUTPUT=60000
MAX_REQUEST=4096
RUN_TIMEOUT=30


class RootUnavailable(RuntimeError):
    pass


def _request(data, path=None):
    """Send a bounded request to an operator-enabled local Unix socket."""
    target = str(path or SOCKET_PATH)
    payload = (json.dumps(data,separators=(',',':'),ensure_ascii=False)+'\n').encode()
    if len(payload)>MAX_REQUEST:raise ValueError('Слишком длинный запрос к root-службе')
    try:
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as conn:
            conn.settimeout(36)
            conn.connect(target)
            conn.sendall(payload)
            data=bytearray()
            while len(data)<MAX_OUTPUT+8192:
                part=conn.recv(min(8192,MAX_OUTPUT+8192-len(data)))
                if not part or b'\n' in part:
                    data.extend(part)
                    break
                data.extend(part)
        reply=json.loads(bytes(data).split(b'\n',1)[0])
        if not isinstance(reply,dict):raise ValueError('Неверный формат ответа')
        if reply.get('error'):raise RootUnavailable(str(reply['error'])[:240])
        return reply
    except (ConnectionRefusedError, FileNotFoundError, PermissionError, socket.timeout, OSError) as exc:
        raise RootUnavailable('Root-служба недоступна. Настройте её один раз через SSH по инструкции ROOT_ДОСТУП.txt') from exc
    except (json.JSONDecodeError,ValueError) as exc:
        raise RootUnavailable('Root-служба вернула некорректный ответ') from exc


def status(path=None):
    try:
        r=_request({'op':'ping'},path)
        return r.get('ready') is True
    except RootUnavailable:
        return False


def execute(command:str, actor:int, path=None):
    if not isinstance(command,str) or not 1<=len(command.strip())<=MAX_COMMAND or any(ord(c)<32 and c!='\t' for c in command):
        raise ValueError('Введите одну команду длиной до 1200 символов')
    if not isinstance(actor,int) or actor<=0:raise ValueError('Неверный ID владельца')
    r=_request({'op':'run','command':command.strip(),'actor':actor,'id':secrets.token_hex(12)},path)
    if not isinstance(r.get('exit_code'),int) or not isinstance(r.get('output'),str):
        raise RootUnavailable('Root-служба вернула неполный результат')
    return {'ok':r['exit_code']==0,'exit_code':r['exit_code'],
            'output':r['output'][:MAX_OUTPUT],'truncated':bool(r.get('truncated')),'as_root':True}




def _root_actor_allowed(server, actor):
    if type(actor)!=int or actor<1:return False
    if actor==server.owner_id:return True
    role_dir=getattr(server,'roles_dir',None)
    if not role_dir:return False
    try:
        members=json.loads((Path(role_dir)/'admins.json').read_text(encoding='utf-8'))
        if members.get('version')!=1 or actor not in members.get('admins',[]):return False
        role_file=Path(role_dir)/'admin_roles.json'
        if not role_file.exists():return True # legacy existing admins have full role
        roles=json.loads(role_file.read_text(encoding='utf-8'))
        if roles.get('version')!=1:return False
        detail=roles.get('roles',{}).get(str(actor),{'role':'full'})
        if detail.get('role')=='full':return True
        return detail.get('role')=='custom' and 'platform.root' in detail.get('permissions',[])
    except (OSError,ValueError,TypeError,AttributeError):return False

class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(36)
        uid=struct.unpack('3i',self.request.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[1]
        if uid!=self.server.allowed_uid:
            self._reply({'error':'Недостаточно прав для обращения к root-службе'})
            return
        raw=self.rfile.readline(MAX_REQUEST+1)
        if len(raw)>MAX_REQUEST or not raw.endswith(b'\n'):
            self._reply({'error':'Слишком большой запрос'})
            return
        try: req=json.loads(raw)
        except (ValueError,UnicodeDecodeError):req={}
        if not isinstance(req,dict):req={}
        if req.get('op')=='ping':
            self._reply({'ready':True})
            return
        if req.get('op')!='run' or not _root_actor_allowed(self.server, req.get('actor')):
            self._reply({'error':'Root-доступ не разрешён для этого администратора'})
            return
        cmd=req.get('command')
        if not isinstance(cmd,str) or not 1<=len(cmd.strip())<=MAX_COMMAND or any(ord(c)<32 and c!='\t' for c in cmd):
            self._reply({'error':'Некорректная однострочная команда'})
            return
        if not isinstance(req.get('id'),str) or len(req['id'])!=24:
            self._reply({'error':'Не указан идентификатор операции'})
            return
        with self.server.rate_lock:
            now=time.monotonic()
            while self.server.runs and self.server.runs[0]<now-60:self.server.runs.popleft()
            if len(self.server.runs)>=10:
                self._reply({'error':'Не более десяти root-команд в минуту'})
                return
            self.server.runs.append(now)
        result=_run_root(cmd)
        _audit(self.server.audit_path,req['actor'],req['id'],cmd,result['exit_code'])
        self._reply(result)

    def _reply(self,data):
        try:
            wire=(json.dumps(data,ensure_ascii=False,separators=(',',':'))+'\n').encode('utf-8')
            self.wfile.write(wire)
        except (BrokenPipeError,OSError):pass


def _run_root(command):
    # An intentionally privileged, operator-enabled executor. Never run this
    # directly as part of the Internet-facing FastAPI process.
    p=None
    try:
        p=subprocess.Popen(['/bin/bash','-lc',command],stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                           cwd='/',start_new_session=True,
                           env={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
                                'HOME':'/root','LANG':'C.UTF-8'})
        try: output,_=p.communicate(timeout=RUN_TIMEOUT)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid,signal.SIGKILL)
            output,_=p.communicate()
            return {'exit_code':124,'output':'Команда остановлена: превышено 30 секунд.\n'+output[-MAX_OUTPUT:].decode('utf-8','replace'),
                    'truncated':len(output)>MAX_OUTPUT}
        return {'exit_code':p.returncode,'output':output[-MAX_OUTPUT:].decode('utf-8','replace') or '(Нет вывода)',
                'truncated':len(output)>MAX_OUTPUT}
    except (OSError,subprocess.SubprocessError) as exc:
        return {'exit_code':125,'output':'Не удалось выполнить команду: '+type(exc).__name__,'truncated':False}


def _audit(path,actor,request_id,command,code):
    # Record metadata and command SHA only; commands may contain secrets.
    entry=json.dumps({'at':int(time.time()),'actor':actor,'request_id':request_id,
                      'sha256':hashlib.sha256(command.encode()).hexdigest(),'exit_code':code},separators=(',',':'))+'\n'
    dest=Path(path)
    dest.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd=os.open(dest,os.O_CREAT|os.O_WRONLY|os.O_APPEND|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as f:f.write(entry)


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads=True
    allow_reuse_address=False


def serve(path,uid,owner_id,audit_path,roles_dir=None):
    if os.geteuid()!=0:raise SystemExit('Root-служба должна запускаться системой от root')
    path=Path(path)
    path.parent.mkdir(mode=0o711,parents=True,exist_ok=True)
    os.chown(path.parent,0,0)
    os.chmod(path.parent,0o711)
    if path.exists() or path.is_symlink():
        if not stat.S_ISSOCK(path.lstat().st_mode):raise SystemExit('На месте сокета находится посторонний файл')
        path.unlink()
    with _Server(str(path),_Handler) as server:
        server.allowed_uid=uid
        server.owner_id=owner_id
        server.roles_dir=roles_dir
        server.audit_path=audit_path
        server.rate_lock=threading.Lock()
        server.runs=collections.deque()
        os.chown(path,uid,-1)
        os.chmod(path,0o600)
        try:server.serve_forever(poll_interval=.2)
        finally:path.unlink(missing_ok=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--serve',action='store_true')
    parser.add_argument('--socket',default=str(SOCKET_PATH))
    parser.add_argument('--uid',type=int,required=True)
    parser.add_argument('--owner-id',type=int,required=True)
    parser.add_argument('--audit',default='/var/log/sitecontrol/root-commands.jsonl')
    parser.add_argument('--roles-dir',default='/var/lib/svgtracker-admin')
    args=parser.parse_args()
    if not args.serve or args.uid<0 or args.owner_id<1:parser.error('неверные параметры')
    serve(args.socket,args.uid,args.owner_id,args.audit,args.roles_dir)
