"""SVGTracker V35: account-scoped analytics, opt-in rule engine, deterministic assistant.
No external AI calls, secrets, user prompts or conversation histories stored.
"""
import calendar
import json
import re
import uuid
from collections import defaultdict
from contextlib import closing
from datetime import date, datetime, timedelta, timezone, time
from zoneinfo import ZoneInfo
from finance_db import connect, utc_now
from product_db import _timezone_for_row, _occurrences
from insights import _state, _date_value, _number, build_report

RULE_TYPES = {
    'budget_percent': {'label':'Бюджет близок к лимиту','min':50,'max':100,'default':80,'unit':'%'},
    'daily_expense': {'label':'Траты за день достигли суммы','min':1,'max':100000000,'default':1000,'unit':'₽'},
    'tomorrow_events': {'label':'События на завтра вечером','min':1,'max':1,'default':1,'unit':'вкл'},
}


def init_intelligence_db():
    with closing(connect()) as db, db:
        db.execute('''CREATE TABLE IF NOT EXISTS automation_rules(
          id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, rule_type TEXT NOT NULL,
          threshold REAL NOT NULL, enabled INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT NOT NULL, UNIQUE(user_id,rule_type),
          FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE)''')
        db.execute('CREATE INDEX IF NOT EXISTS idx_automation_user ON automation_rules(user_id,enabled)')


def list_rules(user_id):
    with closing(connect()) as db, db:
        rows = {r['rule_type']:r for r in db.execute('SELECT * FROM automation_rules WHERE user_id=?',(user_id,))}
    return [{'type':key,'label':cfg['label'],'threshold':float(rows[key]['threshold']) if key in rows else cfg['default'],
             'enabled':bool(rows[key]['enabled']) if key in rows else False,'unit':cfg['unit'],
             'min':cfg['min'],'max':cfg['max']} for key,cfg in RULE_TYPES.items()]


def update_rule(user_id, rule_type, threshold, enabled):
    cfg=RULE_TYPES.get(rule_type)
    if not cfg or type(enabled) is not bool or type(threshold) not in (int,float):
        raise ValueError('Неверные параметры автоматизации')
    value=float(threshold)
    if not (cfg['min']<=value<=cfg['max']) or not value.is_integer():
        raise ValueError('Значение за пределами допустимого диапазона')
    with closing(connect()) as db, db:
        if not db.execute('SELECT 1 FROM users WHERE id=?',(user_id,)).fetchone():
            raise ValueError('Пользователь не найден')
        db.execute('''INSERT INTO automation_rules(id,user_id,rule_type,threshold,enabled,updated_at)
              VALUES(?,?,?,?,?,?) ON CONFLICT(user_id,rule_type) DO UPDATE SET
              threshold=excluded.threshold,enabled=excluded.enabled,updated_at=excluded.updated_at''',
              (uuid.uuid4().hex,user_id,rule_type,int(value),int(enabled),utc_now()))
    return list_rules(user_id)


def _month_shift(month, offset):
    y,m=map(int,month.split('-'))
    index=y*12+m-1+offset
    return f'{index//12:04d}-{index%12+1:02d}'


def _finance_metrics(state, month, today):
    expenses = [e for e in state.get('expenses',[]) if isinstance(e,dict) and _date_value(e.get('date')).startswith(month)]
    incomes = [e for e in state.get('incomes',[]) if isinstance(e,dict) and _date_value(e.get('date')).startswith(month)]
    mandatory = [e for e in state.get('mandatoryExpenses',[]) if isinstance(e,dict) and
                 (str(e.get('monthKey') or _date_value(e.get('date'))[:7])==month)]
    # A mandatory payment is only counted when recorded as paid.
    paid = [e for e in mandatory if e.get('paid') is True]
    spent = round(sum(abs(_number(e.get('amount'))) for e in expenses),2)
    paid_sum = round(sum(abs(_number(e.get('amount'))) for e in paid),2)
    earned = round(sum(abs(_number(e.get('amount'))) for e in incomes),2)
    budgets=state.get('monthlyBudgets') or {}
    budget_rec=budgets.get(month) or {}
    budget=_number(budget_rec.get('income') if isinstance(budget_rec,dict) else budget_rec)
    if not budget and month==today[:7]: budget=_number(state.get('monthlyIncome'))
    by_id = {str(cat.get('id')):str(cat.get('name') or 'Без категории')[:45] for cat in state.get('categories',[]) if isinstance(cat,dict)}
    categories=defaultdict(float)
    for item in expenses+paid:
        name=by_id.get(str(item.get('categoryId')),str(item.get('category') or 'Без категории')[:45])
        categories[name]+=abs(_number(item.get('amount')))
    groups=[{'name':name,'amount':round(amount,2)} for name,amount in sorted(categories.items(),key=lambda x:-x[1])[:12]]
    daily=defaultdict(float)
    for item in expenses:
        dt=_date_value(item.get('date'))
        if dt.startswith(month):daily[dt]+=abs(_number(item.get('amount')))
    # Paid mandatory expenses can be dated by their payment date, if present.
    for item in paid:
        dt=_date_value(item.get('paidAt') or item.get('date'))
        if dt.startswith(month):daily[dt]+=abs(_number(item.get('amount')))
    return {'month':month,'expenses':spent,'paid_mandatory':paid_sum,'total_spent':round(spent+paid_sum,2),
      'income':earned,'budget':round(max(0,budget),2),'available':round(budget+earned-spent-paid_sum,2),
      'expense_count':len(expenses)+len(paid),'categories':groups,
      'days':[{'date':f'{month}-{day:02d}','amount':round(daily.get(f'{month}-{day:02d}',0),2)}
              for day in range(1,calendar.monthrange(*map(int,month.split('-')))[1]+1)]}


def financial_analytics(user_id, month=None, now=None):
    now=now or datetime.now(timezone.utc)
    with closing(connect()) as db, db:
        settings=db.execute('SELECT timezone_name,timezone_offset_minutes FROM user_settings WHERE user_id=?',(user_id,)).fetchone()
        if not settings: raise ValueError('Пользователь не найден')
        today=now.astimezone(_timezone_for_row(settings)).date().isoformat()
        default_month=today[:7]
        month=month or default_month
        if not isinstance(month,str) or not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',month): raise ValueError('Неверный месяц')
        if abs((int(month[:4])*12+int(month[5:]))-(int(default_month[:4])*12+int(default_month[5:])))>120:
            raise ValueError('Месяц за пределами диапазона')
        state=_state(db,'finance_state',user_id)
    current=_finance_metrics(state,month,today)
    prev=_finance_metrics(state,_month_shift(month,-1),today)
    timeline=[_finance_metrics(state,_month_shift(month,i),today) for i in range(-5,1)]
    return {'current':current,'previous':prev,'trend':[
      {'month':m['month'],'spent':m['total_spent'],'income':m['income']} for m in timeline]}


def activity_analytics(user_id, now=None):
    now=now or datetime.now(timezone.utc)
    with closing(connect()) as db, db:
        row=db.execute('SELECT timezone_name,timezone_offset_minutes FROM user_settings WHERE user_id=?',(user_id,)).fetchone()
        if not row:raise ValueError('Пользователь не найден')
        today=now.astimezone(_timezone_for_row(row)).date()
    days=[build_report(user_id,'daily',today-timedelta(days=offset),now) for offset in range(6,-1,-1)]
    total={key:sum(d[key] for d in days) for key in ('expenses','income','workouts','training_minutes','notes_updated','events')}
    active=sum(bool(d['workouts'] or d['notes_updated'] or d['expense_count'] or d['income_count'] or d['events']) for d in days)
    return {'start':days[0]['start'],'end':days[-1]['start'],'days':days,'totals':total,'active_days':active,
            'note':'События считаются по расписанию, остальные показатели — по сохранённым записям.'}


def assistant_answer(user_id, question, now=None):
    if not isinstance(question,str) or len(question)>240:raise ValueError('Запрос слишком длинный')
    q=question.casefold().strip()
    if not q:raise ValueError('Введите запрос')
    finance=financial_analytics(user_id,now=now)['current']
    today=build_report(user_id,'daily',now=now)
    week=build_report(user_id,'weekly',now=now)
    fmt=lambda x:f'{x:,.0f}'.replace(',',' ')
    if any(word in q for word in ('помощь','умеешь','команды','возможност')):
        answer='Я работаю по правилам, без нейросети. Можешь спросить: «Что сегодня?», «Сколько потратил?», «Итоги недели», «Финансовый обзор» или «Что по бюджету?». Данные беру только из твоего SVGTracker.'
    elif any(word in q for word in ('сегодня','сейчас','день')) and not any(word in q for word in ('потрат','расход','бюджет')):
        answer=f"Сегодня в записях: тренировок — {today['workouts']}, событий расписания — {today['events']}, обновлено заметок — {today['notes_updated']}. Расходы — {fmt(today['expenses'])} ₽, доходы — {fmt(today['income'])} ₽."
    elif any(word in q for word in ('потрат','расход')) and 'сегодня' in q:
        answer=f"Сегодня записано расходов на {fmt(today['expenses'])} ₽. Это сумма обычных расходов в сегодняшних записях."
    elif any(word in q for word in ('недел','7 дней','семь дней')):
        answer=f"За текущую календарную неделю: {week['workouts']} тренировок, {week['training_minutes']} мин, {week['events']} событий, {week['notes_updated']} обновлений заметок. Расходы: {fmt(week['expenses'])} ₽."
    elif any(word in q for word in ('категор','куда уход','больше всего')):
        top=finance['categories'][:3]
        answer='Самые крупные категории расходов за месяц: '+(', '.join(f"{r['name']} — {fmt(r['amount'])} ₽" for r in top) if top else 'в этом месяце категорий расходов пока нет.')
    elif any(word in q for word in ('бюджет','остат','лимит')):
        answer=(f"Плановый бюджет месяца: {fmt(finance['budget'])} ₽; учтено поступлений: {fmt(finance['income'])} ₽; расходы с оплаченной обязательной частью: {fmt(finance['total_spent'])} ₽; расчётный остаток: {fmt(finance['available'])} ₽." if finance['budget'] else 'Месячный плановый бюджет не настроен. Укажи сумму в «Финансы → Настроить бюджет», чтобы я мог показать остаток.')
    elif any(word in q for word in ('потрат','расход','финанс','деньг','доход')):
        answer=f"В этом месяце записано расходов на {fmt(finance['total_spent'])} ₽, из них оплаченные обязательные — {fmt(finance['paid_mandatory'])} ₽. Отдельно записано доходов: {fmt(finance['income'])} ₽."
    elif any(word in q for word in ('привет','здравствуй','начать')):
        answer='Привет! Я SVG Assistant — помощник на основе простых правил и данных проекта. Напиши «Помощь», чтобы увидеть доступные запросы.'
    else:
        answer='Пока не умею разбирать такой запрос. Я не нейросеть: использую проверенные команды и реальные записи. Попробуй «Что сегодня?», «Итоги недели» или «Что по бюджету?». '
    return {'answer':answer.strip(),'mode':'rules','external_ai':False}


def collect_due_automations(now=None):
    now=now or datetime.now(timezone.utc)
    due=[]
    with closing(connect()) as db, db:
        active=db.execute('''SELECT a.user_id,a.rule_type,a.threshold,u.telegram_id,s.timezone_name,
           s.timezone_offset_minutes FROM automation_rules a
           JOIN user_settings s ON s.user_id=a.user_id JOIN users u ON u.id=a.user_id
           WHERE a.enabled=1 AND s.bot_notifications=1 AND u.telegram_id IS NOT NULL''').fetchall()
        for rule in active:
            tz=_timezone_for_row(rule)
            local=now.astimezone(tz)
            # All notifications run in the evening, with per-user local timezone.
            if not 19<=local.hour<=22:continue
            today=local.date().isoformat()
            typ=rule['rule_type'];text=None;key=f'automation:{typ}:{today}'
            if typ in ('budget_percent','daily_expense'):
                result=_finance_metrics(_state(db,'finance_state',rule['user_id']),today[:7],today)
                if typ=='budget_percent' and result['budget']>0 and result['total_spent']>=result['budget']*rule['threshold']/100:
                    key=f'automation:{typ}:{today[:7]}'
                    text=f"SVGTracker · Бюджет\nВ этом месяце потрачено {result['total_spent']:g} ₽ из плановых {result['budget']:g} ₽. Порог: {int(rule['threshold'])}%."
                elif typ=='daily_expense':
                    today_sum=next((d['amount'] for d in result['days'] if d['date']==today),0)
                    if today_sum>=rule['threshold']:
                        text=f"SVGTracker · Расходы сегодня\nЗаписано трат на {today_sum:g} ₽. Твой порог: {int(rule['threshold'])} ₽."
            elif typ=='tomorrow_events':
                tomorrow=local.date()+timedelta(days=1)
                start=datetime.combine(tomorrow,time.min,tzinfo=tz).astimezone(timezone.utc)
                end=datetime.combine(tomorrow+timedelta(days=1),time.min,tzinfo=tz).astimezone(timezone.utc)
                count=0
                for event in db.execute('''SELECT e.* FROM schedule_events e JOIN schedule_event_members m
                                          ON m.event_id=e.id WHERE m.user_id=?''',(rule['user_id'],)):
                    try:count+=len(_occurrences(event,start,end))
                    except (ValueError,OverflowError,TypeError):continue
                if count:text=f'SVGTracker · Завтра\nВ расписании на завтра {count} событий. Открой календарь, чтобы посмотреть подробности.'
            if text and not db.execute('SELECT 1 FROM bot_reminder_log WHERE user_id=? AND reminder_key=?',(rule['user_id'],key)).fetchone():
                due.append({'user_id':rule['user_id'],'telegram_id':rule['telegram_id'],'key':key,'text':text})
    return due
