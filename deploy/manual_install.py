#!/usr/bin/env python3
"""One-time phone/SSH recovery install, after admin independently uploads ZIP via SFTP.

Run: sudo python3 /tmp/sc112-unpacked/TRC/deploy/manual_install.py /tmp/Site_Control_V112.zip
Does not restore, delete or overwrite credentials / databases / admin lists.
"""
from __future__ import annotations
import io,os,sys,time,shutil,subprocess,urllib.request,zipfile,stat
from pathlib import Path,PurePosixPath

ROOT=Path(os.environ.get('SITE_CONTROL_PROJECT_ROOT','/var/www/SVG'))
ALLOWED={'main.py','index.html','script.js','style.css','product.js','product.css','sitecontrol_logo.jpg','admin_access.py','config.example.py'}
DIRS={'sitecontrol','deploy'}
EXT={'.py','.js','.css','.html','.txt','.md','.jpg','.jpeg','.png','.svg'}
MAX_BYTES=35*1024*1024

def extract_zip(path):
    data=Path(path).read_bytes()
    if len(data)>MAX_BYTES:raise RuntimeError('Архив больше 35 МБ')
    files={};total=0
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            if info.is_dir():continue
            raw=info.filename
            if not raw.startswith('TRC/'):raise RuntimeError('Это не архив Site Control')
            raw=raw[4:]
            p=PurePosixPath(raw)
            if p.is_absolute() or '..' in p.parts or '\\' in raw or not p.parts:raise RuntimeError('Опасный путь')
            typ=(info.external_attr>>16)&0o170000
            if typ==stat.S_IFLNK:raise RuntimeError('Ссылки запрещены')
            if raw not in ALLOWED and not (len(p.parts)==2 and p.parts[0] in DIRS and p.suffix.lower() in EXT):continue
            if raw in files:raise RuntimeError('Повтор имени')
            total+=info.file_size
            if total>100*1024*1024:raise RuntimeError('Объём распаковки слишком большой')
            contents=z.read(info)
            if raw.endswith('.py'):compile(contents.decode('utf-8-sig'),raw,'exec')
            files[raw]=contents
    if not {'main.py','index.html','sitecontrol/web.py'}.issubset(files):raise RuntimeError('Обязательные файлы отсутствуют')
    return files

def main():
    if os.geteuid()!=0:raise RuntimeError('Запускайте через sudo')
    if len(sys.argv)!=2:raise RuntimeError('Укажите локальный ZIP-файл')
    files=extract_zip(sys.argv[1]);ROOT.mkdir(parents=True,exist_ok=True)
    backup=Path('/var/backups/sitecontrol');backup.mkdir(parents=True,exist_ok=True,mode=0o700)
    backup_path=backup/('before-v112-'+time.strftime('%Y%m%d-%H%M%S')+'.zip')
    previous={};new=[]
    for raw in files:
        target=ROOT/raw
        if any(x.is_symlink() for x in [ROOT,*target.parents,target] if x.exists()):raise RuntimeError('Символьная ссылка в пути '+raw)
        if target.is_file():previous[raw]=target.read_bytes()
        else:new.append(raw)
    with zipfile.ZipFile(backup_path,'w',zipfile.ZIP_DEFLATED) as z:
        for key,value in previous.items():z.writestr(key,value)
    backup_path.chmod(0o600)
    service=os.environ.get('SVGTRACKER_SERVICE_NAME','svgtracker')
    print('Резервная копия файлов:',backup_path,flush=True)
    try:
        for raw,data in files.items():
            target=ROOT/raw;target.parent.mkdir(parents=True,exist_ok=True)
            tmp=target.with_name(target.name+'.sc-new')
            with tmp.open('wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
            tmp.chmod(0o644);tmp.replace(target)
        subprocess.run(['systemctl','restart',service],check=True,timeout=30)
        time.sleep(2)
        with urllib.request.urlopen('http://127.0.0.1:8000/api/test',timeout=8) as r:
            if r.status!=200:raise RuntimeError('Проверка API не пройдена')
        print('Обновление успешно, API отвечает. Базы и администраторы сохранены.')
    except BaseException:
        print('Ошибка: восстанавливаем предыдущие файлы...',file=sys.stderr)
        for raw,value in previous.items():
            (ROOT/raw).write_bytes(value)
        for raw in new:
            (ROOT/raw).unlink(missing_ok=True)
        subprocess.run(['systemctl','restart',service],timeout=30,check=False)
        raise

if __name__=='__main__':main()
