"""V47: per-entry reminder policies, authorization, cooldown/dedupe, previous-data compat."""
import unittest
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI
from fastapi.testclient import TestClient
import finance_db, product_db
from product_api import build_product_router
from test_v34 import V34Tests


class V47ReminderTests(V34Tests):
    def make_client(self):
        app=FastAPI()
        def auth(request,_):
            return ({'first_name':'Test'},int(request.headers.get('x-test-user',str(self.a))))
        class Bot: pass
        app.include_router(build_product_router(authenticate=auth,bot=Bot(),main_keyboard=lambda:None,
            get_user_settings=finance_db.get_user_settings,get_user_telegram_id=finance_db.get_user_telegram_id,logger=None))
        return TestClient(app)

    def test_additive_migration_keeps_existing_event_and_note(self):
        # Simulate a V46 schema by removing V47-only fields while retaining data.
        with finance_db.connect() as db:
            # Recreate a minimal pair of tables matching the old schema, without user data.
            db.execute('DROP TABLE schedule_event_members')
            db.execute('DROP TABLE schedule_events')
            db.execute('DROP TABLE notes')
            db.execute("""CREATE TABLE schedule_events(
                id TEXT PRIMARY KEY, owner_user_id INTEGER NOT NULL,title TEXT NOT NULL,details TEXT NOT NULL DEFAULT '',
                starts_at TEXT NOT NULL,ends_at TEXT,all_day INTEGER DEFAULT 0,recurrence TEXT DEFAULT 'none',
                recurrence_until TEXT,recurrence_timezone TEXT DEFAULT 'UTC',repeat_day_offsets TEXT DEFAULT '[]',
                reminder_minutes INTEGER,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""")
            db.execute("""CREATE TABLE notes(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,title TEXT NOT NULL DEFAULT '',body TEXT NOT NULL DEFAULT '',
                pinned INTEGER DEFAULT 0,archived INTEGER DEFAULT 0,reminder_at TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
                folder TEXT DEFAULT '',tags_json TEXT DEFAULT '[]',favorite INTEGER DEFAULT 0)""")
            db.execute("INSERT INTO notes(id,user_id,title,body,created_at,updated_at) VALUES('old-note',?,?,?, 'now','now')",(self.a,'Прежняя заметка','Данные'))
            db.execute("INSERT INTO schedule_events(id,owner_user_id,title,starts_at,created_at,updated_at) VALUES('old-event',?,?,?, 'now','now')",(self.a,'Прежнее событие','2026-10-22T18:00:00Z'))
        product_db.init_product_db()
        with finance_db.connect() as db:
            note=db.execute("SELECT * FROM notes WHERE id='old-note'").fetchone()
            ev=db.execute("SELECT * FROM schedule_events WHERE id='old-event'").fetchone()
            self.assertEqual(note['body'],'Данные')
            self.assertEqual(ev['title'],'Прежнее событие')
            self.assertIsNone(ev['reminder_repeat_count'])
            self.assertIsNone(note['reminder_repeat_interval'])

    def test_account_policy_defaults_and_invalid_options(self):
        prefs=product_db.get_notification_preferences(self.a)
        self.assertEqual((prefs['default_reminder_count'],prefs['default_reminder_interval']),(1,5))
        changed=product_db.update_notification_preferences(self.a,{'default_reminder_count':4,'default_reminder_interval':10})
        self.assertEqual((changed['default_reminder_count'],changed['default_reminder_interval']),(4,10))
        self.assertEqual(product_db.get_notification_preferences(self.b)['default_reminder_count'],1)
        for bad in (0,7,'3',True,100):
            with self.assertRaises(ValueError):product_db.update_notification_preferences(self.a,{'default_reminder_count':bad})
        for bad in (1,2,6,'5',True):
            with self.assertRaises(ValueError):product_db.update_notification_preferences(self.a,{'default_reminder_interval':bad})
        with self.make_client() as c:
            r=c.put('/api/notifications/preferences',json={'preferences':{'default_reminder_count':3,'default_reminder_interval':5}})
            self.assertEqual(r.status_code,200,r.text)
            self.assertEqual(r.json()['preferences']['default_reminder_count'],3)
            self.assertEqual(c.put('/api/notifications/preferences',json={'preferences':{'default_reminder_count':100}}).status_code,422)

    def test_schedule_repeat_overrides_defaults_and_logs(self):
        now=datetime(2026,10,22,11,20,tzinfo=timezone.utc)
        with self.make_client() as c:
            ev={'title':'Экзамен','starts_at':now.isoformat(), 'ends_at':(now+timedelta(hours=1)).isoformat(),
                'reminder_minutes':10,'reminder_repeat_count':3,'reminder_repeat_interval':5,'recurrence':'none','recurrence_timezone':'UTC'}
            response=c.post('/api/schedule/events',json=ev)
            self.assertEqual(response.status_code,200,response.text)
            created=response.json()['event'];eid=created['id']
            self.assertEqual((created['reminder_repeat_count'],created['reminder_repeat_interval']),(3,5))
            # Due at T-10, T-5 and T, no duplicate after logging.
            for when,index in [(now-timedelta(minutes=10),0),(now-timedelta(minutes=5),1),(now,2)]:
                due=[x for x in product_db.collect_due_reminders(when,lookback_minutes=1) if x.get('kind')=='schedule' and eid in x['key']]
                self.assertEqual([x['repeat_attempt'] for x in due],[index],(when,due))
                product_db.mark_reminder_sent(self.a,due[0]['key'])
                self.assertFalse([x for x in product_db.collect_due_reminders(when,lookback_minutes=1) if eid in x['key']])
            self.assertFalse([x for x in product_db.collect_due_reminders(now+timedelta(minutes=5),lookback_minutes=1) if eid in x['key']])
            # Another user cannot amend this event; opt-out overrides per-event count.
            self.assertEqual(c.put('/api/schedule/events/'+eid,headers={'x-test-user':str(self.b)},json=ev).status_code,403)
            changed={**ev,'reminder_repeat_count':2}
            self.assertEqual(c.put('/api/schedule/events/'+eid,json=changed).status_code,200)
            product_db.update_notification_preferences(self.a,{'personal_reminders':False})
            self.assertFalse([x for x in product_db.collect_due_reminders(now+timedelta(minutes=5)) if eid in x['key']])

    def test_notes_repeat_default_and_per_record(self):
        now=datetime(2026,10,22,11,30,tzinfo=timezone.utc)
        product_db.update_notification_preferences(self.a,{'default_reminder_count':3,'default_reminder_interval':5})
        note=product_db.save_note(self.a,'note_v47',{'title':'Лекарства','reminder_at':now.isoformat(),'body':'',
            'reminder_repeat_count':2,'reminder_repeat_interval':10})
        self.assertEqual((note['reminder_repeat_count'],note['reminder_repeat_interval']),(2,10))
        due=[r for r in product_db.collect_due_reminders(now,1) if r.get('kind')=='note' and r['title']=='Лекарства']
        self.assertEqual([r['repeat_attempt'] for r in due],[0])
        product_db.mark_reminder_sent(self.a,due[0]['key'])
        due=[r for r in product_db.collect_due_reminders(now+timedelta(minutes=10),1) if r.get('kind')=='note' and r['title']=='Лекарства']
        self.assertEqual([r['repeat_attempt'] for r in due],[1])
        self.assertEqual(product_db.list_notes(self.b),[])
        product_db.update_notification_preferences(self.a,{'note_reminders':False})
        self.assertFalse([r for r in product_db.collect_due_reminders(now+timedelta(minutes=10),1) if r.get('kind')=='note'])
        with self.make_client() as c:
            self.assertEqual(c.post('/api/notes',json={'title':'x','reminder_repeat_count':9}).status_code,422)
            self.assertEqual(c.post('/api/notes',json={'title':'x','reminder_repeat_interval':1}).status_code,422)
            self.assertEqual(c.post('/api/notes',json={'title':'x','reminder_repeat_count':True}).status_code,422)

    def test_training_plan_daily_reminder_opt_in_and_skip_completed(self):
        now=datetime(2026,10,22,18,0,tzinfo=timezone.utc)
        finance_db.save_training_state(self.a, {'plan':{'Чт':'Силовая'},'history':[],'planMeta':{'effectiveFrom':'2026-10-01'},'planOverrides':{}})
        self.assertFalse([x for x in product_db.collect_due_reminders(now,1) if x.get('kind')=='training'])
        product_db.update_notification_preferences(self.a,{'training_reminders':True,'training_reminder_time':'18:00'})
        due=[x for x in product_db.collect_due_reminders(now,1) if x.get('kind')=='training' and x['user_id']==self.a]
        self.assertEqual(len(due),1)
        self.assertEqual(due[0]['title'],'Силовая')
        product_db.mark_reminder_sent(self.a,due[0]['key'])
        self.assertFalse([x for x in product_db.collect_due_reminders(now,1) if x.get('kind')=='training'])
        with self.assertRaises(ValueError):product_db.update_notification_preferences(self.a,{'training_reminder_time':'24:99'})
        with self.assertRaises(ValueError):product_db.update_notification_preferences(self.a,{'training_reminder_time':'x'})
        finance_db.save_training_state(self.a,{'plan':{'Чт':'Силовая'},'history':[],
            'planOverrides':{'2026-10-22':{'type':'cancel'}},'planMeta':{'effectiveFrom':'2026-10-01'}})
        self.assertFalse([x for x in product_db.collect_due_reminders(now,1) if x.get('kind')=='training'])
        finance_db.save_training_state(self.a,{'plan':{'Чт':'Силовая'},'history':[{'dateKey':'2026-10-22','status':'completed'}],
            'planMeta':{'effectiveFrom':'2026-10-01'}})
        self.assertFalse([x for x in product_db.collect_due_reminders(now,1) if x.get('kind')=='training'])

    def test_disabled_event_and_original_single_default(self):
        now=datetime(2026,10,22,12,0,tzinfo=timezone.utc)
        ev=product_db.create_schedule_event(self.a,'classic_event',{'title':'Занятие','starts_at':now.isoformat(),
            'recurrence':'none','reminder_minutes':0})
        self.assertIsNone(ev['reminder_repeat_count'])
        self.assertIsNone(ev['reminder_repeat_interval'])
        due=product_db.collect_due_reminders(now,1)
        self.assertEqual(len([r for r in due if 'classic_event' in r['key']]),1)
        product_db.mark_reminder_sent(self.a, next(r['key'] for r in due if 'classic_event' in r['key']))
        self.assertFalse([r for r in product_db.collect_due_reminders(now,1) if 'classic_event' in r['key']])
        # Existing user can globally disable notifications even for explicit per-event overrides.
        finance_db.set_bot_notifications(self.a,False)
        self.assertFalse([r for r in product_db.collect_due_reminders(now+timedelta(minutes=5),1) if r['user_id']==self.a])

if __name__=='__main__':unittest.main()
