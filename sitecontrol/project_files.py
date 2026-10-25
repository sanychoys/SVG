"""Site Control v1.5: staged file releases, bounded project backups and rollback.

Archives contain project FILES. External databases/services need their own backups.
The administrative registry and Site Control state are always external to the tree.
"""
from __future__ import annotations
import base64
import fcntl
from contextlib import contextmanager,nullcontext
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from . import store, remote

ROOT=Path(os.environ.get('SITE_CONTROL_PROJECT_ROOT',os.environ.get('SVGTRACKER_PROJECT_ROOT','/var/www/SVG')))
STATE=Path(os.environ.get('SITE_CONTROL_PROJECT_FILES_DIR',str(store.STATE/'project-files')))
MAX_UPLOAD=30*1024*1024
MAX_ARCHIVE=90*1024*1024
MAX_ENTRIES=1000
MAX_BACKUP=110*1024*1024
PROTECTED={'.env','config.py','admins.json','admins.lock','admin_access.pyc','sitecontrol.db','svgtracker.db','id_rsa','id_ed25519'}
PROTECTED_DIRS={'.git','venv','.venv','__pycache__','node_modules','.ssh','backups','logs','tokens','project-files'}
ALLOWED=re.compile(r'^[A-Za-z0-9._\-]+$')


@contextmanager
def project_lock(pid):
    """Serialize publishing/restoring across API worker processes."""
    folder=pid_path(pid)
    fd=os.open(folder/'.update.lock',os.O_CREAT|os.O_RDWR,0o600)
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('В этом проекте уже выполняется обновление или бэкап')
        yield
    finally:
        try:fcntl.flock(fd,fcntl.LOCK_UN)
        finally:os.close(fd)


def base():
    STATE.mkdir(mode=0o700,parents=True,exist_ok=True)
    return STATE


def relpath(raw):
    if not isinstance(raw,str) or not raw or len(raw)>250 or '\\' in raw or '\x00' in raw:raise ValueError('Недопустимое имя файла')
    # Exact, trusted Site Control documentation filenames. Other project filenames
    # still follow the strict ASCII component and protected-path policy below.
    if raw in {'ИНСТРУКЦИЯ.txt','ЧТО_ИЗМЕНИЛОСЬ.txt','АУДИТ_И_ТЕСТЫ.txt',
               'FAQ_ПО_ШАГАМ.txt','ROOT_ДОСТУП.txt','ВОССТАНОВЛЕНИЕ_ДЕПЛОЯ.txt'}:
        return raw
    p=PurePosixPath(raw)
    if p.is_absolute() or not p.parts or any(x in ('','.','..') or not ALLOWED.fullmatch(x) for x in p.parts):raise ValueError('Недопустимый путь в файле')
    if any(x in PROTECTED_DIRS or x.startswith('.') for x in p.parts[:-1]):raise ValueError('Системную папку менять нельзя')
    if p.name in PROTECTED or p.name.startswith('.') or p.suffix.lower() in ('.db','.sqlite','.pem','.key','.p12'):
        raise ValueError('Системный файл нельзя обновлять через проект')
    return str(p)


def validate_zip(raw, max_bytes=MAX_UPLOAD, max_total=MAX_ARCHIVE,allow_empty=False):
    if len(raw)>max_bytes:raise ValueError('Архив превышает установленный лимит')
    try:z=zipfile.ZipFile(io.BytesIO(raw))
    except (zipfile.BadZipFile,ValueError):raise ValueError('Некорректный ZIP')
    with z:
        files={};total=0
        for info in z.infolist():
            if info.is_dir():continue
            if len(files)>=MAX_ENTRIES:raise ValueError('Слишком много файлов')
            if (info.external_attr>>16)&0o170000 == 0o120000:raise ValueError('Символические ссылки запрещены')
            name=relpath(info.filename)
            if name in files:raise ValueError('Повторяющийся файл: '+name)
            total+=info.file_size
            if total>max_total:raise ValueError('Распакованный ZIP слишком большой')
            with z.open(info) as fh:
                content=fh.read(max_total+1)
            if len(content)!=info.file_size:raise ValueError('Невозможно прочитать ZIP')
            files[name]=content
        if not files and not allow_empty:raise ValueError('Нет файлов для обновления')
        return files


def pack(files):
    buff=io.BytesIO()
    with zipfile.ZipFile(buff,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for name,blob in sorted(files.items()):z.writestr(name,blob)
    return buff.getvalue()


def pid_path(pid):
    pid=int(pid)
    if pid<0 or pid>99999999:raise ValueError('Неверный проект')
    if pid!=0 and not store.get_site(pid):raise LookupError('Проект не найден')
    p=base()/str(pid)
    p.mkdir(parents=True,exist_ok=True,mode=0o700)
    return p


def metadata(pid,kind):
    path=pid_path(pid)/kind
    path.mkdir(mode=0o700,exist_ok=True)
    return path


def stage(pid,raw,name):
    if len(raw)>MAX_UPLOAD:raise ValueError('Файл больше 30 МБ')
    files=validate_zip(raw) if name.lower().endswith('.zip') else {relpath(name):raw}
    for fn,blob in files.items():
        if fn.endswith('.py'):
            try:compile(blob.decode('utf-8-sig'),fn,'exec')
            except (SyntaxError,UnicodeError) as exc:raise ValueError(f'Ошибка Python в {fn}: {exc}')
    rid=uuid.uuid4().hex[:16]
    dest=metadata(pid,'releases')
    # Stage is private; it is not executed or published upon upload.
    bundle=pack(files)
    (dest/(rid+'.zip')).write_bytes(bundle)
    manifest={'id':rid,'created_at':int(time.time()),'files':list(files),'size':len(bundle),'status':'Ожидает публикации'}
    (dest/(rid+'.json')).write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf-8')
    return manifest


def list_releases(pid):
    folder=metadata(pid,'releases')
    values=[]
    for file in folder.glob('*.json'):
        try:values.append(json.loads(file.read_text('utf-8')))
        except (OSError,ValueError):continue
    return sorted(values,key=lambda x:x['created_at'],reverse=True)[:70]


def get_release(pid,rid):
    if not re.fullmatch(r'[a-f0-9]{16}',rid):raise ValueError('Неверная версия')
    folder=metadata(pid,'releases')
    meta=folder/(rid+'.json'); archive=folder/(rid+'.zip')
    if not meta.is_file() or not archive.is_file():raise LookupError('Версия не найдена')
    return json.loads(meta.read_text('utf-8')),archive.read_bytes()


def listing_root(root):
    """Snapshot of regular project files, bounded and never follows symlinks."""
    root=root.resolve(strict=True)
    if not root.is_dir():raise ValueError('Папка проекта недоступна')
    files={};total=0
    for current,dirs,names in os.walk(root,followlinks=False):
        here=Path(current)
        dirs[:]=[d for d in dirs if d not in PROTECTED_DIRS and not (here/d).is_symlink() and not d.startswith('.')]
        for name in names:
            path=here/name
            if path.is_symlink() or not path.is_file():continue
            relative=path.relative_to(root).as_posix()
            try:relpath(relative)
            except ValueError:continue
            size=path.stat().st_size
            total+=size
            if len(files)>=MAX_ENTRIES or total>MAX_BACKUP:raise ValueError('Проект слишком большой для файлового бэкапа (110 МБ / 1000 файлов)')
            files[relative]=path.read_bytes()
    return files


def apply_local(root,raw,max_bytes=MAX_UPLOAD,max_total=MAX_ARCHIVE,allow_empty=False):
    files=validate_zip(raw,max_bytes=max_bytes,max_total=max_total,allow_empty=allow_empty)
    root=root.resolve(strict=True)
    if not root.is_dir():raise ValueError('Папка проекта отсутствует')
    # Check all paths BEFORE changing anything. Exclude symlinks, including parents.
    for name in files:
        path=root
        for part in PurePosixPath(name).parts:
            path=path/part
            if path.is_symlink():raise ValueError('Символическая ссылка в пути запрещена')
        if not path.resolve(strict=False).is_relative_to(root):raise ValueError('Файл за пределами проекта')
    for name,data in files.items():
        target=root/name
        target.parent.mkdir(parents=True,exist_ok=True)
        fd,tmp=tempfile.mkstemp(prefix='.sc-update-',dir=target.parent)
        try:
            with os.fdopen(fd,'wb') as f:
                f.write(data);f.flush();os.fsync(f.fileno())
            os.chmod(tmp,0o644)
            os.replace(tmp,target)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
    return {'changed':sorted(files),'restart_required':any(x.endswith(('.py','.service','.timer')) for x in files)}


# Remote agent is passed over SSH and executed under an unprivileged deployment user.
# It creates archives on stdout and accepts validated update ZIPs on stdin.
REMOTE_AGENT=r'''
import io,json,os,sys,zipfile,tempfile
from pathlib import Path,PurePosixPath
mode=sys.argv[1];root=Path(sys.argv[2]).resolve(strict=True)
if not root.is_dir():raise SystemExit('PROJECT_ROOT_NOT_FOUND')
blocked={'.git','venv','.venv','__pycache__','node_modules','.ssh','backups','logs','tokens','project-files'}
protected={'.env','config.py','admins.json','admins.lock','sitecontrol.db','svgtracker.db'}
def safe(raw):
 p=PurePosixPath(raw)
 if p.is_absolute() or not p.parts or any(x in ('','.','..') or x in blocked or x.startswith('.') for x in p.parts[:-1]) or p.name in protected or p.name.startswith('.') or p.suffix.lower() in ('.db','.sqlite','.pem','.key','.p12'):raise ValueError('BAD_PATH')
 if any(not all(c.isascii() and (c.isalnum() or c in '._-') for c in seg) for seg in p.parts):raise ValueError('BAD_PATH')
 target=root
 for part in p.parts:
  target=target/part
  if target.is_symlink():raise ValueError('SYMLINK')
 if not target.resolve(strict=False).is_relative_to(root):raise ValueError('OUTSIDE_ROOT')
 return target
if mode=='backup':
 out=io.BytesIO();total=0;count=0
 with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED,compresslevel=4) as z:
  for current,dirs,names in os.walk(root,followlinks=False):
   here=Path(current)
   dirs[:]=[d for d in dirs if d not in blocked and not d.startswith('.') and not (here/d).is_symlink()]
   for name in names:
    path=here/name
    if path.is_symlink() or not path.is_file():continue
    rel=path.relative_to(root).as_posix()
    try:safe(rel)
    except ValueError:continue
    total+=path.stat().st_size;count+=1
    if total>115343360 or count>1000:raise SystemExit('BACKUP_LIMIT_EXCEEDED')
    z.write(path,rel)
 sys.stdout.buffer.write(out.getvalue())
elif mode in ('apply','restore'):
 limit=115343360 if mode=='restore' else 31457280
 raw=sys.stdin.buffer.read(limit+1)
 if len(raw)>limit:raise SystemExit('UPLOAD_TOO_BIG')
 with zipfile.ZipFile(io.BytesIO(raw)) as z:
  infos=[i for i in z.infolist() if not i.is_dir()]
  if (not infos and mode!='restore') or len(infos)>1000 or sum(i.file_size for i in infos)>(115343360 if mode=='restore' else 94371840):raise SystemExit('ZIP_LIMIT')
  changes=[]
  for info in infos:
   if (info.external_attr>>16)&0o170000==0o120000:raise SystemExit('SYMLINK_ARCHIVE')
   dest=safe(info.filename)
   changes.append((dest,z.read(info)))
  for dest,blob in changes:
   dest.parent.mkdir(parents=True,exist_ok=True)
   fd,temp=tempfile.mkstemp(prefix='.sc-update-',dir=dest.parent)
   try:
    with os.fdopen(fd,'wb') as f:f.write(blob);f.flush();os.fsync(f.fileno())
    os.chmod(temp,0o644);os.replace(temp,dest)
   finally:
    if os.path.exists(temp):os.unlink(temp)
 sys.stdout.write(json.dumps({'changed':[str(p.relative_to(root)) for p,_ in changes]}))
else:raise SystemExit('BAD_OPERATION')
'''


def remote_run(server,mode,data=b''):
    target=server.get('deploy_root') or ''
    if not isinstance(target,str) or not (target.startswith('/var/www/') or target.startswith('/srv/') or target.startswith('/home/')) or '..' in target.split('/'):
        raise ValueError('Укажите безопасный абсолютный путь проекта на VPS')
    root=remote.KEY_DIR.resolve();priv=root/(server['key_alias']+'.key');hosts=root/'known_hosts'
    if not shutil.which('ssh') or not priv.is_file() or not hosts.is_file():raise RuntimeError('SSH не настроен')
    script=base64.b64encode(REMOTE_AGENT.encode()).decode()
    import shlex
    command='python3 -c '+shlex.quote('import base64;exec(base64.b64decode("'+script+'"))')+' '+shlex.quote(mode)+' '+shlex.quote(target)
    argv=['ssh','-T','-i',str(priv),'-p',str(server['port']),'-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+str(hosts),'-o','GlobalKnownHostsFile=/dev/null','-o','ConnectTimeout=7','-o','IdentitiesOnly=yes','-o','LogLevel=ERROR','--',f'{server["username"]}@{server["host"]}',command]
    result=subprocess.run(argv,input=data,capture_output=True,timeout=65)
    if result.returncode:raise RuntimeError('SSH: '+result.stderr.decode('utf-8','replace')[-500:])
    return result.stdout


def root_for(pid,server_id=None):
    pid=int(pid)
    if pid==0:
        if server_id not in (None,'',0,'local'):raise ValueError('Для платформы используйте локальные файлы; SSH управляется отдельно')
        return ROOT,None
    if not store.get_site(pid):raise LookupError('Проект не найден')
    if not server_id:raise ValueError('Сначала подключите VPS к проекту')
    srv=store.get_server(int(server_id))
    if not srv or srv['site_id']!=pid:raise ValueError('Сервер не принадлежит проекту')
    return None,srv


def create_backup(pid,server_id=None,_nested=False):
    with nullcontext() if _nested else project_lock(pid):
        return _create_backup_inner(pid,server_id)


def _create_backup_inner(pid,server_id=None):
    root,srv=root_for(pid,server_id)
    content=pack(listing_root(root)) if root else remote_run(srv,'backup')
    validate_zip(content,max_bytes=MAX_BACKUP,max_total=MAX_BACKUP,allow_empty=True)
    if len(content)>MAX_BACKUP:raise ValueError('Бэкап больше 110 МБ')
    bid=uuid.uuid4().hex[:16]
    folder=metadata(pid,'backups');(folder/(bid+'.zip')).write_bytes(content)
    meta={'id':bid,'created_at':int(time.time()),'server_id':int(server_id) if srv else None,'server_name':srv['name'] if srv else 'Наша платформа','size':len(content),'files':len(validate_zip(content,max_bytes=MAX_BACKUP,max_total=MAX_BACKUP,allow_empty=True))}
    (folder/(bid+'.json')).write_text(json.dumps(meta,ensure_ascii=False),encoding='utf-8')
    return meta


def import_backup(pid,raw,server_id=None):
    # Import never applies files immediately. All paths are checked before saving.
    root,srv=root_for(pid,server_id)
    files=validate_zip(raw,max_bytes=MAX_BACKUP,max_total=MAX_BACKUP)
    bid=uuid.uuid4().hex[:16]
    folder=metadata(pid,'backups')
    (folder/(bid+'.zip')).write_bytes(raw)
    meta={'id':bid,'created_at':int(time.time()),'server_id':int(server_id) if srv else None,
          'server_name':srv['name'] if srv else 'Наша платформа','size':len(raw),'files':len(files),'imported':True}
    (folder/(bid+'.json')).write_text(json.dumps(meta,ensure_ascii=False),encoding='utf-8')
    return meta


def list_backups(pid,server_id=None):
    values=[]
    for p in metadata(pid,'backups').glob('*.json'):
        try:
            meta=json.loads(p.read_text('utf-8'))
            if server_id is None or meta.get('server_id')==server_id:values.append(meta)
        except (ValueError,OSError):pass
    return sorted(values,key=lambda x:x['created_at'],reverse=True)


def lookup_backup(pid,bid):
    if not re.fullmatch('[a-f0-9]{16}',bid):raise ValueError('Неверный ID бэкапа')
    folder=metadata(pid,'backups');meta=folder/(bid+'.json');bundle=folder/(bid+'.zip')
    if not meta.is_file() or not bundle.is_file():raise LookupError('Бэкап не найден')
    return json.loads(meta.read_text('utf-8')),bundle


def deploy(pid,rid,server_id=None):
    with project_lock(pid):
        root,srv=root_for(pid,server_id)
        release,raw=get_release(pid,rid)
        backup=create_backup(pid,server_id,_nested=True)
        result=apply_local(root,raw) if root else json.loads(remote_run(srv,'apply',raw).decode('utf-8'))
        release.update(status='Опубликовано',backup_id=backup['id'],published_at=int(time.time()),server_id=int(server_id) if srv else None)
        folder=metadata(pid,'releases');(folder/(rid+'.json')).write_text(json.dumps(release,ensure_ascii=False),encoding='utf-8')
        return {'ok':True,'backup':backup,'result':result}


def restore(pid,bid):
    with project_lock(pid):
        meta,path=lookup_backup(pid,bid)
        root,srv=root_for(pid,meta.get('server_id'))
        safety=create_backup(pid,meta.get('server_id'),_nested=True)
        data=path.read_bytes()
        result=apply_local(root,data,max_bytes=MAX_BACKUP,max_total=MAX_BACKUP,allow_empty=True) if root else json.loads(remote_run(srv,'restore',data).decode('utf-8'))
        return {'ok':True,'previous_backup':safety,'result':result}


def delete_backup(pid,bid):
    _,path=lookup_backup(pid,bid)
    path.unlink();path.with_suffix('.json').unlink(missing_ok=True)
    return {'ok':True}
