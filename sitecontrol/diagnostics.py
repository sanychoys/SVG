"""Private, rotating, readable diagnostics for the Site Control app and bot."""
from __future__ import annotations
import logging
import os
import re
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
            handler.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | %(message)s', '%d.%m.%Y %H:%M:%S'))
            logger.addHandler(handler)
        path = LOG_DIR / (kind + '.log')
        if path.exists():path.chmod(0o600)


def tail(kind: str, limit: int = 150) -> str:
    if kind not in KINDS:
        raise ValueError('Unknown log kind')
    limit = max(1, min(int(limit), 400))
    path = LOG_DIR / (kind + '.log')
    if not path.is_file():
        return 'Записей пока нет. Журнал появится при работе соответствующего компонента.\n'
    # Read at most 2 MiB and preserve complete multiline traceback events.
    with path.open('rb') as file:
        file.seek(0, 2)
        size=file.tell()
        file.seek(max(0, size-2_000_000))
        data=file.read()
    if size > 2_000_000:
        data=data.split(b'\n',1)[-1]
    text=data.decode('utf-8','replace')
    # Each timestamp starts a new event; keep multiline tracebacks intact.
    entries=re.split(r'(?=^\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2} \| )',text,flags=re.M)
    entries=[entry for entry in entries if entry.strip()]
    result=''.join(entries[-limit:]).strip()
    return (result or 'Записей пока нет.')+'\n' 


def scrub_path(path: str) -> str:
    # Never log raw incoming query strings, access codes, BotFather tokens or webhook IDs.
    path=path.split('?',1)[0]
    path=re.sub(r'/api/sc/hooks/[^/]+', '/api/sc/hooks/[hidden]', path)
    path=re.sub(r'/api/sc/auth/[^/]+', '/api/sc/auth/[hidden]', path)
    return path[:160]
