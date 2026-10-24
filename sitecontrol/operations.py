"""Short, human-readable admin status. No raw logs in normal bot messages."""
from __future__ import annotations
import json
import os
import urllib.request
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from . import store

STATE_NAMES = {
    'success': 'Обновление установлено', 'rollback_success': 'Выполнен откат',
    'no_changes': 'Новых изменений нет', 'validating': 'Проверяется архив',
    'deploying': 'Устанавливается обновление', 'publishing': 'Синхронизация с GitHub',
    'rolled_back': 'Обновление отменено', 'github_rollback': 'Обновление отменено',
    'error': 'Ошибка обновления', 'rollback_error': 'Ошибка отката',
    'legacy_cleanup_success':'Удалены старые файлы', 'legacy_cleanup_error':'Ошибка очистки',
}
TIMEZONE_FILE = Path(os.environ.get('SVGTRACKER_ADMIN_STATE_DIR', '/var/lib/svgtracker-admin'))/'timezone.json'


def display_tz():
    """Use the configured Site Control/V48 reporting timezone."""
    try:
        data=json.loads(TIMEZONE_FILE.read_text(encoding='utf-8'))
        name=str(data.get('timezone_name') or '').strip()
        if name:
            try:return ZoneInfo(name)
            except (ZoneInfoNotFoundError,ValueError):pass
        minutes=max(-840,min(840,int(data.get('timezone_offset_minutes') or 0)))
        return timezone(timedelta(minutes=minutes))
    except (OSError,ValueError,TypeError):
        return datetime.now().astimezone().tzinfo


def probe_api(url='http://127.0.0.1:8000/api/test') -> bool:
    """Check the real local endpoint, not an assumed 'API OK' flag."""
    try:
        with urllib.request.urlopen(url,timeout=3) as response:
            if response.status!=200:return False
            data=json.loads(response.read(2048))
            return isinstance(data,dict) and data.get('status')=='ok'
    except (OSError,ValueError,TypeError):return False


ERROR_STATES = {'error', 'rolled_back', 'github_rollback', 'rollback_error','legacy_cleanup_error'}
ACTIONS = {
    'site.create':'Создан сайт', 'draft.save':'Сохранён черновик',
    'site.publish':'Опубликован сайт', 'lead.create':'Получена заявка',
    'lead.status':'Изменён статус заявки', 'bot.attach':'Подключён Telegram-бот',
    'bot.disable':'Отключён Telegram-бот', 'content.change':'Изменён контент',
    'items.change':'Обновлены товары или услуги',
}


def pretty_time(value: Any) -> str:
    if not value: return '—'
    try:
        if isinstance(value, (int,float)):
            moment=datetime.fromtimestamp(value, tz=timezone.utc)
        else:
            moment=datetime.fromisoformat(str(value).replace('Z','+00:00'))
            if moment.tzinfo is None: moment=moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(display_tz()).strftime('%d.%m.%Y · %H:%M')
    except (ValueError, TypeError, OverflowError):return '—'


def read_deploy(path: Path) -> dict:
    try:
        obj=json.loads(path.read_text(encoding='utf-8'))
        return obj if isinstance(obj,dict) else {}
    except (OSError, ValueError, TypeError):return {}


def last_event(status_path: Path) -> dict:
    deploy=read_deploy(status_path)
    with store.connect() as db:
        row=db.execute('SELECT action, detail, created_at FROM audit ORDER BY created_at DESC, id DESC LIMIT 1').fetchone()
    events=[]
    if row:
        action=ACTIONS.get(row['action'], 'Изменение проекта')
        # Do not display user-entered free text or identifiers in short status.
        events.append({'at':int(row['created_at']), 'when':pretty_time(row['created_at']), 'label':action, 'error':False, 'message':''})
    if deploy.get('updated_at'):
        try:
            dt=datetime.fromisoformat(str(deploy['updated_at']).replace('Z','+00:00'))
            ts=dt.timestamp()
            kind=deploy.get('status')
            events.append({'at':ts, 'when':pretty_time(deploy['updated_at']),
                'label':STATE_NAMES.get(kind,'Изменено состояние установки'),
                'error':kind in ERROR_STATES, 'message':str(deploy.get('message') or '')+('\n\n'+str(deploy['error_trace']) if deploy.get('error_trace') else '')})
        except (ValueError, TypeError, OverflowError):pass
    if not events:return {'when':'—', 'label':'Изменений пока нет', 'error':False, 'message':''}
    return max(events,key=lambda x:x['at'])


def status_text(status_path: Path, *,service_ok:bool|None=None, api_ok:bool=True) -> str:
    with store.connect() as db:
        sites=db.execute('SELECT COUNT(*) FROM sites').fetchone()[0]
        leads=db.execute("SELECT COUNT(*) FROM leads WHERE status='new'").fetchone()[0]
    activity=last_event(status_path)
    service='работает' if service_ok is True else 'не работает' if service_ok is False else 'не проверена'
    api='работает' if api_ok else 'ошибка'
    return (f'Состояние · {pretty_time(datetime.now(timezone.utc).isoformat())}\n\n'
            f'Сервис: {service}\nAPI: {api}\nСайтов: {sites} · Новых заявок: {leads}\n\n'
            f'Последнее изменение: {activity["label"]}\n{activity["when"]}' +
            (('\n\nПОДРОБНОСТИ ОШИБКИ\n'+activity['message']) if activity['error'] else ''))


def deploy_status_text(status_path: Path) -> str:
    payload=read_deploy(status_path)
    if not payload:return 'Обновлений пока не было.'
    name=STATE_NAMES.get(payload.get('status'),'Состояние обновления')
    time_str=pretty_time(payload.get('updated_at'))
    message=f'{name}\n{time_str}'
    if payload.get('status') in ERROR_STATES:
        # Preserve error details, unlike successful/regular updates.
        return message+'\n\n'+str(payload.get('message') or 'Нет подробностей ошибки')+('\n\n'+str(payload['error_trace']) if payload.get('error_trace') else '')
    if payload.get('status')=='success' and payload.get('github_commit'):
        return message+'\nКод: '+str(payload['github_commit'])[:9]
    return message
