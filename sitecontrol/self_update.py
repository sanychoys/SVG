"""Site Control self-updates via a separate systemd transient unit.

Never execute uploaded archives. The worker preserves protected files and admin state.
A non-privileged web process may queue an update only when systemd authorizes it.
"""
from __future__ import annotations
import io,json,os,re,secrets,shutil,subprocess,sys,time,zipfile
from pathlib import Path, PurePosixPath
from . import project_files

STATE=project_files.STATE/'self-updates'
ROOT=project_files.ROOT
MAX_SIZE=25*1024*1024
ALLOWED_ROOT={'main.py','index.html','script.js','style.css','product.js','product.css','sitecontrol_logo.jpg','admin_access.py','config.example.py'}
ALLOWED_DIRS={'sitecontrol','deploy'}
ALLOWED_EXT={'.py','.js','.css','.html','.txt','.md','.jpg','.jpeg','.png','.svg'}

def _paths(blob,full_required=True):
    if len(blob)>MAX_SIZE:raise ValueError('Архив платформы превышает 25 МБ')
    try:z=zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:raise ValueError('Неверный ZIP')
    files={};total=0
    with z:
        infos=[x for x in z.infolist() if not x.is_dir()]
        if len(infos)>300:raise ValueError('Слишком много файлов')
        wrapper='TRC/' if infos and all(x.filename.startswith('TRC/') for x in infos) else ''
        for info in infos:
            raw=info.filename[len(wrapper):]
            if not raw:continue
            # Instructions may be in ZIP but must not overwrite unexpected source paths.
            p=PurePosixPath(raw)
            if p.is_absolute() or '..' in p.parts or '\\' in raw or not p.parts:
                raise ValueError('Опасный путь в установочном архиве')
            if (info.external_attr>>16)&0o170000==0o120000:raise ValueError('Ссылки в ZIP запрещены')
            if not (raw in ALLOWED_ROOT or (len(p.parts)==2 and p.parts[0] in ALLOWED_DIRS and p.suffix.lower() in ALLOWED_EXT)):
                continue
            name=project_files.relpath(raw)
            if p.parts[0]=='deploy' and p.name.endswith(('.service','.timer')):continue
            if name in files:raise ValueError('Дубликат файла '+name)
            total+=info.file_size
            if total>70*1024*1024:raise ValueError('Распакованный ZIP слишком большой')
            contents=z.read(info)
            if len(contents)!=info.file_size:raise ValueError('Не удалось прочитать файл '+name)
            if name.endswith('.py'):
                try:compile(contents.decode('utf-8-sig'),name,'exec')
                except (SyntaxError,UnicodeError) as exc:raise ValueError(f'Ошибка Python: {name}: {exc}')
            files[name]=contents
    if not files or (full_required and not {'main.py','sitecontrol/web.py','index.html'}.issubset(files)):
        raise ValueError('В установочном ZIP нет обязательных файлов Site Control')
    return files

def stage(blob,filename='Site_Control.zip'):
    if not isinstance(filename,str) or not filename.lower().endswith('.zip'):
        name=project_files.relpath(filename)
        if not (name in ALLOWED_ROOT or (len(Path(name).parts)==2 and Path(name).parts[0] in ALLOWED_DIRS and Path(name).suffix.lower() in ALLOWED_EXT)):
            raise ValueError('Этот файл нельзя обновить через панель платформы')
        blob=project_files.pack({name:blob})
    files=_paths(blob,full_required=False)
    STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
    rid=secrets.token_hex(8)
    archive=STATE/(rid+'.zip')
    fd=os.open(archive,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'wb') as fh:fh.write(blob);fh.flush();os.fsync(fh.fileno())
    except BaseException:
        archive.unlink(missing_ok=True);raise
    status={'id':rid,'status':'staged','files':len(files),'created_at':int(time.time()),'message':'ZIP проверен; можно установить'}
    write_status(rid,status)
    return status

def write_status(rid,obj):
    if not re.fullmatch(r'[a-f0-9]{16}',rid):raise ValueError('Неверная версия')
    STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
    target=STATE/(rid+'.json');tmp=STATE/(rid+'.tmp')
    tmp.write_text(json.dumps(obj,ensure_ascii=False),encoding='utf-8')
    os.replace(tmp,target)

def status(rid):
    if not re.fullmatch(r'[a-f0-9]{16}',rid):raise ValueError('Неверная версия')
    path=STATE/(rid+'.json')
    if not path.is_file():raise LookupError('Обновление не найдено')
    return json.loads(path.read_text('utf-8'))

def launch(rid):
    item=status(rid)
    if item['status']!='staged':raise ValueError('Это обновление уже отправлено на установку')
    archive=STATE/(rid+'.zip')
    if not archive.is_file():raise ValueError('Файл обновления отсутствует')
    # Keep independent systemd job alive when the web service restarts.
    unit='sitecontrol-update-'+rid
    python=sys.executable
    cmd=['systemd-run','--unit='+unit,'--collect','--property=Type=oneshot',
         '--working-directory='+str(ROOT),'--setenv=PYTHONPATH='+str(ROOT),
         '--setenv=SITE_CONTROL_PROJECT_ROOT='+str(ROOT),
         '--setenv=SITE_CONTROL_PROJECT_FILES_DIR='+str(project_files.STATE),
         '--setenv=SITE_CONTROL_DATA_DIR='+str(project_files.store.STATE),
         '--setenv=SVGTRACKER_SERVICE_NAME='+os.environ.get('SVGTRACKER_SERVICE_NAME','svgtracker'),
         python,'-m','sitecontrol.self_update','apply',rid]
    if not shutil.which('systemd-run'):
        raise RuntimeError('На VPS нет systemd-run. Необходимо настроить отдельный сервис установки обновлений.')
    item.update(status='queued',message='Установка передана сервису',unit=unit)
    write_status(rid,item)
    try:
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=15,cwd=str(ROOT))
        if p.returncode:
            reason=(p.stderr or p.stdout).strip()[:400]
            raise RuntimeError('Веб-службе не разрешено запускать установщик. '+reason+' Нужны права отдельной службы обновлений.')
    except Exception:
        item.update(status='staged',message='Не удалось запустить установщик')
        write_status(rid,item)
        raise
    return item

def apply(rid):
    item=status(rid)
    if item['status'] not in ('queued','staged'):
        return 1
    files=_paths((STATE/(rid+'.zip')).read_bytes(),full_required=False)
    backup=STATE/(rid+'-backup.zip')
    previous={}
    try:
        for name in files:
            target=ROOT/name
            if target.is_symlink():raise ValueError('Символьная ссылка в пути: '+name)
            if target.exists():previous[name]=target.read_bytes()
        backup.write_bytes(project_files.pack(previous))
        item.update(status='applying',message='Резервная копия создана, обновляем файлы',backup=backup.name)
        write_status(rid,item)
        result=project_files.apply_local(ROOT,project_files.pack(files),max_bytes=project_files.MAX_BACKUP,max_total=project_files.MAX_BACKUP)
        item.update(status='applied',message='Файлы установлены; перезапуск службы',changed=len(result['changed']))
        write_status(rid,item)
        # systemd-run transient unit is separate from the application cgroup.
        service=os.environ.get('SVGTRACKER_SERVICE_NAME','svgtracker')
        p=subprocess.run(['systemctl','restart',service],capture_output=True,text=True,timeout=25)
        if p.returncode:raise RuntimeError('Не удалось перезапустить службу: '+p.stderr[-350:])
        time.sleep(3)
        import urllib.request
        with urllib.request.urlopen('http://127.0.0.1:8000/api/test',timeout=10) as response:
            if response.status!=200:raise RuntimeError('API не ответил HTTP 200')
        item.update(status='success',message='Site Control обновлён и отвечает')
        write_status(rid,item)
        return 0
    except Exception as exc:
        try:
            project_files.apply_local(ROOT,project_files.pack(previous),max_bytes=project_files.MAX_BACKUP,max_total=project_files.MAX_BACKUP,allow_empty=True)
            subprocess.run(['systemctl','restart',os.environ.get('SVGTRACKER_SERVICE_NAME','svgtracker')],timeout=25,capture_output=True)
        except Exception:pass
        item.update(status='failed',message=str(exc)[:400])
        write_status(rid,item)
        return 2

if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='apply':sys.exit(apply(sys.argv[2]))
    sys.exit(3)
