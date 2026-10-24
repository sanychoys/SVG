"""Private, rotating, readable diagnostics for the Site Control app and bot."""
from __future__ import annotations
import logging
import os
import re
from datetime import datetime,timezone,timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(os.environ.get('SITE_CONTROL_LOG_DIR', '/var/lib/sitecontrol/logs'))
KINDS = ('bot', 'site')


def configure():
    LOG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    LOG_DIR.chmod(0o700)
    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    if not root.handlers:
        root.addHandler(logging.StreamHandler())
    logging.getLogger('uvicorn.access').disabled = True
    logging.getLogger('aiogram').setLevel(logging.ERROR)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    for kind in KINDS:
        logger = logging.getLogger('sitecontrol.' + kind)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        if not any(isinstance(h, RotatingFileHandler) and Path(h.baseFilename) == LOG_DIR / (kind + '.log') for h in logger.handlers):
            handler = RotatingFileHandler(LOG_DIR / (kind + '.log'), maxBytes=2_000_000, backupCount=3, encoding='utf-8')
            handler.setFormatter(logging.Formatter('%(asctime)sZ | %(levelname)s | %(message)s', '%Y-%m-%dT%H:%M:%S'))
            handler.formatter.converter = __import__('time').gmtime
            logger.addHandler(handler)
        path = LOG_DIR / (kind + '.log')
        if path.exists():path.chmod(0o600)


def _event_time(header: str):
    iso=re.match(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)Z \| ',header)
    if iso:
        try:return datetime.fromisoformat(iso.group(1)).replace(tzinfo=timezone.utc)
        except ValueError:return None
    legacy=re.match(r'^(\d\d\.\d\d\.\d{4} \d\d:\d\d:\d\d) \| ',header)
    if legacy:
        try:return datetime.strptime(legacy.group(1),'%d.%m.%Y %H:%M:%S').replace(tzinfo=timezone.utc)
        except ValueError:return None
    return None

EVENT_START=re.compile(r'(?=^(?:\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ|\d\d\.\d\d\.\d{4} \d\d:\d\d:\d\d) \| )',re.M)

def recent_text(text: str, limit: int=30, hours: int=24) -> str:
    """Most recent events only; multiline traceback remains in its own event."""
    limit=max(1,min(int(limit),100));hours=max(1,min(int(hours),168))
    cutoff=datetime.now(timezone.utc)-timedelta(hours=hours)
    result=[]
    for entry in EVENT_START.split(text):
        if not entry.strip():continue
        stamp=_event_time(entry)
        if stamp and stamp>=cutoff:result.append(entry.rstrip())
    selected=result[-limit:]
    return '\n'.join(selected).strip()+'\n' if selected else 'Нет событий за последние '+str(hours)+' ч.\n'

def recent_file(path:Path,limit:int=30,hours:int=24)->str:
    path=Path(path)
    if not path.is_file():return 'Журнал пуст.\n'
    with path.open('rb') as source:
        source.seek(0,2);size=source.tell();source.seek(max(0,size-2_000_000));data=source.read()
    if size>2_000_000:data=data.split(b'\n',1)[-1]
    return recent_text(data.decode('utf-8','replace'),limit,hours)

def tail(kind: str, limit: int = 30, hours: int = 24) -> str:
    if kind not in KINDS:raise ValueError('Unknown log kind')
    return recent_file(LOG_DIR/(kind+'.log'),limit,hours)


def scrub_path(path: str) -> str:
    # Never log raw incoming query strings, access codes, BotFather tokens or webhook IDs.
    path=path.split('?',1)[0]
    path=re.sub(r'/api/sc/hooks/[^/]+', '/api/sc/hooks/[hidden]', path)
    path=re.sub(r'/api/sc/auth/[^/]+', '/api/sc/auth/[hidden]', path)
    return path[:160]


def project_event(project_id:int,kind:str,message:str):
    """Isolated per-project operational log; never include user text or token material."""
    if kind not in ('bot','site','deploy'):raise ValueError('Неизвестный журнал')
    project_id=int(project_id)
    if not 1<=project_id<=99999999:raise ValueError('Неверный проект')
    folder=LOG_DIR/'projects'/str(project_id)
    folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=folder/(kind+'.log')
    text=str(message).replace('\r',' ').replace('\n',' ')[:500]
    # keep bounded: 1MB per project and log kind
    if path.is_file() and path.stat().st_size>1_000_000:
        previous=folder/(kind+'.previous.log')
        path.replace(previous)
    fd=os.open(str(path),os.O_CREAT|os.O_WRONLY|os.O_APPEND,0o600)
    with os.fdopen(fd,'a',encoding='utf-8') as out:
        out.write(datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')+' | '+text+'\n')


def project_tail(project_id:int,kind:str,limit:int=30,hours:int=24):
    if kind not in ('bot','site','deploy'):raise ValueError('Неизвестный журнал')
    project_id=int(project_id)
    if not 1<=project_id<=99999999:raise ValueError('Неверный проект')
    path=LOG_DIR/'projects'/str(project_id)/(kind+'.log')
    if not path.is_file():return 'Записей по этому проекту пока нет.\n'
    return recent_file(path,limit,hours)


def project_exception(project_id:int,kind:str,message:str,detail:str):
    """Keep bounded full traceback in a private per-project log."""
    if kind not in ('bot','site','deploy'):raise ValueError('Неизвестный журнал')
    project_id=int(project_id)
    if not 1<=project_id<=99999999:raise ValueError('Неверный проект')
    folder=LOG_DIR/'projects'/str(project_id)
    folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=folder/(kind+'.log')
    if path.is_file() and path.stat().st_size>1_000_000:path.replace(folder/(kind+'.previous.log'))
    fd=os.open(str(path),os.O_CREAT|os.O_WRONLY|os.O_APPEND,0o600)
    with os.fdopen(fd,'a',encoding='utf-8') as out:
        out.write(datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')+' | ERROR '+str(message)[:300]+'\n'+str(detail)[-18000:]+'\n')


def format_for_timezone(text:str,timezone_name:str='UTC')->str:
    """Convert UTC-formatted structured event headers to admin's saved device timezone."""
    from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
    try:tz=ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError,ValueError,TypeError):tz=timezone.utc
    def translate(match):
        timestamp=_event_time(match.group(0)+' | ')
        return timestamp.astimezone(tz).strftime('%d.%m.%Y %H:%M:%S') if timestamp else match.group(0)
    return re.sub(r'^(?:\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ|\d\d\.\d\d\.\d{4} \d\d:\d\d:\d\d)(?= \| )',translate,text,flags=re.M)


def recent_journal(text:str,limit:int=30,hours:int=24)->str:
    """Transform journalctl short-iso entries to timestamped UTC log lines.
    All timestamps remain explicit UTC; clients can render their own timezone.
    """
    items=[]
    since=datetime.now(timezone.utc)-timedelta(hours=hours)
    for line in text.splitlines():
        m=re.match(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:?\d\d))\s+(.+)',line)
        if not m:continue
        try:when=datetime.fromisoformat(m.group(1).replace('Z','+00:00'))
        except ValueError:continue
        if when < since:continue
        items.append(when.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')+' | '+m.group(2))
    return '\n'.join(items[-max(1,min(limit,100)):])+'\n' if items else 'Нет событий за последние 24 часа.\n'
