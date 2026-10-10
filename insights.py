"""V34 account-isolated global search and Telegram activity digests.
No external services or personal data are needed for report generation.
"""
import json
from contextlib import closing
from datetime import datetime, date, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from finance_db import connect
from product_db import _occurrences, _timezone_for_row, get_notification_preferences


def _state(db, table, user_id):
    row = db.execute(f'SELECT state_json FROM {table} WHERE user_id=?', (user_id,)).fetchone()
    if not row:
        return {}
    try:
        value=json.loads(row['state_json'])
        return value if isinstance(value,dict) else {}
    except (ValueError,TypeError):
        return {}


def _date_value(value, local_tz=None):
    raw=str(value or '')
    if not raw:
        return ''
    try:
        if 'T' in raw and local_tz:
            dt=datetime.fromisoformat(raw.replace('Z','+00:00'))
            if dt.tzinfo:
                return dt.astimezone(local_tz).date().isoformat()
    except ValueError:
        pass
    return raw[:10]


def _number(value):
    try:
        n=float(value)
        return n if -1e12<n<1e12 else 0
    except (ValueError,TypeError):
        return 0


def build_report(user_id, period='daily', anchor=None, now=None):
    """Calendar-local digest, read from persisted finance/training/events/notes.
    Weekly = Monday through Sunday of anchor's calendar week; daily = its date.
    """
    if period not in ('daily','weekly'):
        raise ValueError('Unknown report period')
    now = now or datetime.now(timezone.utc)
    with closing(connect()) as db, db:
        settings=db.execute('SELECT timezone_name,timezone_offset_minutes FROM user_settings WHERE user_id=?',(user_id,)).fetchone()
        if not settings: raise ValueError('User not found')
        tz=_timezone_for_row(settings)
        today=now.astimezone(tz).date()
        if anchor is None: anchor=today
        if isinstance(anchor,str):anchor=date.fromisoformat(anchor)
        if not isinstance(anchor,date) or abs((anchor-today).days)>3660:
            raise ValueError('Invalid report date')
        start=anchor-timedelta(days=anchor.weekday()) if period=='weekly' else anchor
        end=start+timedelta(days=6 if period=='weekly' else 0)
        start_str,end_str=start.isoformat(),end.isoformat()
        financial=_state(db,'finance_state',user_id)
        training=_state(db,'training_state',user_id)
        expense_rows=[v for v in (financial.get('expenses') or []) if isinstance(v,dict) and start_str<=_date_value(v.get('date') or v.get('createdAt'),tz)<=end_str]
        income_rows=[v for v in (financial.get('incomes') or []) if isinstance(v,dict) and start_str<=_date_value(v.get('date') or v.get('createdAt'),tz)<=end_str]
        sessions=[v for v in (training.get('history') or []) if isinstance(v,dict) and v.get('status')=='completed' and start_str<=_date_value(v.get('dateKey') or v.get('ended') or v.get('date'),tz)<=end_str]
        # Notes updated_at is UTC, convert to local date for accuracy near midnight.
        notes_rows=db.execute('SELECT updated_at FROM notes WHERE user_id=?',(user_id,)).fetchall()
        notes=sum(start_str<=_date_value(r['updated_at'],tz)<=end_str for r in notes_rows)
        events=db.execute('''SELECT e.* FROM schedule_events e JOIN schedule_event_members m ON m.event_id=e.id
                     WHERE m.user_id=?''',(user_id,)).fetchall()
        window_start=datetime.combine(start,time.min,tzinfo=tz).astimezone(timezone.utc)
        window_end=datetime.combine(end+timedelta(days=1),time.min,tzinfo=tz).astimezone(timezone.utc)
        scheduled=0
        for event in events:
            try:
                scheduled+=len(_occurrences(event,window_start,window_end))
            except (ValueError,TypeError,OverflowError):
                continue
        duration=sum(max(0,min(86400,_number(s.get('duration')))) for s in sessions)
        return {'period':period,'start':start_str,'end':end_str,'timezone':str(tz),
            'expenses':round(sum(abs(_number(v.get('amount'))) for v in expense_rows),2),
            'income':round(sum(abs(_number(v.get('amount'))) for v in income_rows),2),
            'expense_count':len(expense_rows),'income_count':len(income_rows),
            'workouts':len(sessions),'training_minutes':int(round(duration/60)),
            'events':scheduled,'notes_updated':notes}


def render_report(report):
    daily=report['period']=='daily'
    heading='Ежедневный отчёт' if daily else 'Еженедельный отчёт'
    dates=report['start'] if daily else f"{report['start']} — {report['end']}"
    return (f"📊 SVGTracker · {heading}\n{dates}\n\n"
        f"🏋️ Тренировок: {report['workouts']} · {report['training_minutes']} мин\n"
        f"📅 Событий в расписании: {report['events']}\n"
        f"📝 Обновлено заметок: {report['notes_updated']}\n"
        f"💸 Расходы: {report['expenses']:g} ₽ ({report['expense_count']})\n"
        f"💰 Доходы: {report['income']:g} ₽ ({report['income_count']})\n\n"
        "Отчёт основан на сохранённых данных SVGTracker."
    )


def collect_due_reports(now=None):
    now=now or datetime.now(timezone.utc)
    due=[]
    with closing(connect()) as db, db:
        users=db.execute('''SELECT s.user_id,s.timezone_name,s.timezone_offset_minutes,u.telegram_id
                FROM user_settings s JOIN users u ON u.id=s.user_id
                WHERE s.bot_notifications=1''').fetchall()
        for row in users:
            tz=_timezone_for_row(row)
            local=now.astimezone(tz)
            # Delivery in local evening. At most one of each report per period.
            if local.hour<20 or not row['telegram_id']:continue
            prefs=get_notification_preferences(row['user_id'],db)
            for period,category,allowed in (
                ('daily','daily_report',True),('weekly','weekly_report',local.weekday()==6)):
                if not allowed or not prefs.get(category,False):continue
                key=f'report:{period}:{local.date().isoformat()}'
                if db.execute('SELECT 1 FROM bot_reminder_log WHERE user_id=? AND reminder_key=?',(row['user_id'],key)).fetchone():continue
                due.append({'user_id':row['user_id'],'telegram_id':row['telegram_id'],'key':key,'period':period,'date':local.date().isoformat()})
    return due


def search_user_data(user_id, query, limit=60):
    query=str(query or '').strip().casefold()
    if len(query)<2 or len(query)>120: return []
    results=[]
    def add(kind,ident,title,detail):
        title=str(title or '').strip()
        detail=str(detail or '').strip()
        if query in (title+' '+detail).casefold() and len(results)<limit:
            results.append({'kind':kind,'id':str(ident),'title':title[:150],'detail':detail[:200]})
    with closing(connect()) as db, db:
        for r in db.execute('''SELECT n.id,n.title,n.body,n.folder,n.tags_json,n.archived,
                  (SELECT group_concat(a.display_name,' ') FROM note_attachments a
                   WHERE a.note_id=n.id AND a.user_id=n.user_id) AS files
                  FROM notes n WHERE n.user_id=? ORDER BY n.updated_at DESC LIMIT 2000''' ,(user_id,)):
            add('note',r['id'],r['title'] or 'Без названия',f"{r['body']} {r['folder']} {r['tags_json']} {r['files'] or ''}".strip())
        for r in db.execute('''SELECT e.id,e.title,e.details,e.starts_at FROM schedule_events e
                    JOIN schedule_event_members m ON m.event_id=e.id WHERE m.user_id=?
                    ORDER BY e.updated_at DESC LIMIT 2000''',(user_id,)):
            add('schedule',r['id'],r['title'],f"{r['details']} {r['starts_at']}")
        finance=_state(db,'finance_state',user_id)
        for category in ('expenses','incomes','mandatoryExpenses','debts'):
            for idx,r in enumerate((finance.get(category) or [])[:3000]):
                if not isinstance(r,dict): continue
                title=r.get('title') or r.get('person') or r.get('category') or 'Операция'
                add('finance',r.get('id') or idx,title,f"{r.get('category') or ''} {r.get('amount') or ''} {r.get('date') or ''}")
        training=_state(db,'training_state',user_id)
        for idx,r in enumerate((training.get('history') or [])[-1500:]):
            if not isinstance(r,dict):continue
            add('training',r.get('id') or idx,r.get('workout') or 'Тренировка',f"{r.get('dateKey') or r.get('date') or ''} {r.get('status') or ''}")
        for idx,r in enumerate((training.get('goals') or [])[:1000]):
            if not isinstance(r,dict):continue
            add('training',r.get('id') or idx,r.get('name') or 'Цель',str(r.get('description') or ''))
    return results
