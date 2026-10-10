"""V35 regression tests; run with python -m unittest discover -s tests -v."""
import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timezone
from fastapi import FastAPI
from fastapi.testclient import TestClient
import finance_db, product_db, intelligence
from product_api import build_product_router

class IntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.old=finance_db.DB_PATH
        self.oldfiles=product_db.ATTACHMENT_DIR
        finance_db.DB_PATH=Path(self.temp.name)/'data.sqlite'
        product_db.ATTACHMENT_DIR=Path(self.temp.name)/'attachments'
        finance_db.init_db();product_db.init_product_db();intelligence.init_intelligence_db()
        self.a=finance_db.get_or_create_user({'id':9301,'first_name':'A'})
        self.b=finance_db.get_or_create_user({'id':9302,'first_name':'B'})
        finance_db.save_finance_state(self.a,{'monthlyBudgets':{'2026-10':{'income':1000}},'categories':[{'id':'cat_1','name':'Food'}],
          'expenses':[{'id':'a1','amount':300,'date':'2026-10-10','categoryId':'cat_1'},{'id':'a2','amount':200,'date':'2026-09-10','categoryId':'cat_1'}],
          'incomes':[{'amount':100,'date':'2026-10-10'}],
          'mandatoryExpenses':[{'amount':100,'monthKey':'2026-10','date':'2026-10-01','paid':True,'categoryId':'cat_1'},
                               {'amount':800,'monthKey':'2026-10','date':'2026-10-01','paid':False,'categoryId':'cat_1'}]})

    def tearDown(self):
        finance_db.DB_PATH=self.old
        product_db.ATTACHMENT_DIR=self.oldfiles
        self.temp.cleanup()

    def test_financial_analytics_paid_only_and_budget(self):
        x=intelligence.financial_analytics(self.a,'2026-10',now=datetime(2026,10,10,20,tzinfo=timezone.utc))
        c=x['current']
        self.assertEqual(c['total_spent'],400)
        self.assertEqual(c['paid_mandatory'],100)
        self.assertEqual(c['budget'],1000)
        self.assertEqual(c['income'],100)
        self.assertEqual(c['available'],700)
        self.assertEqual(c['categories'][0]['amount'],400)
        self.assertEqual(x['previous']['expenses'],200)
        self.assertEqual(len(x['trend']),6)

    def test_finance_and_assistant_isolation(self):
        today=datetime(2026,10,10,20,tzinfo=timezone.utc)
        self.assertEqual(intelligence.financial_analytics(self.b,'2026-10',now=today)['current']['total_spent'],0)
        self.assertIn('400',intelligence.assistant_answer(self.a,'Сколько потратил?',now=today)['answer'])
        self.assertNotIn('400',intelligence.assistant_answer(self.b,'Сколько потратил?',now=today)['answer'])
        self.assertFalse(intelligence.assistant_answer(self.a,'помощь',now=today)['external_ai'])

    def test_rules_opt_in_settings_and_owner_scoping(self):
        self.assertFalse(any(r['enabled'] for r in intelligence.list_rules(self.a)))
        self.assertEqual(intelligence.update_rule(self.a,'daily_expense',250,True)[1]['enabled'],True)
        self.assertFalse(any(r['enabled'] for r in intelligence.list_rules(self.b)))
        with self.assertRaises(ValueError):intelligence.update_rule(self.a,'unsafe_script',1,True)
        with self.assertRaises(ValueError):intelligence.update_rule(self.a,'daily_expense',0,True)
        with self.assertRaises(ValueError):intelligence.update_rule(self.a,'daily_expense',250,'yes')
        now=datetime(2026,10,10,20,tzinfo=timezone.utc)
        due=[x for x in intelligence.collect_due_automations(now) if x['user_id']==self.a]
        self.assertEqual(len(due),1)
        product_db.mark_reminder_sent(self.a,due[0]['key'])
        self.assertFalse([x for x in intelligence.collect_due_automations(now) if x['user_id']==self.a])
        finance_db.set_bot_notifications(self.a,False)
        self.assertFalse([x for x in intelligence.collect_due_automations(now) if x['user_id']==self.a])

    def test_api_validation_isolation_and_reports(self):
        app=FastAPI()
        def auth(req,_):return ({'first_name':'Test'},int(req.headers.get('x-test-user',str(self.a))))
        class Bot:pass
        app.include_router(build_product_router(authenticate=auth,bot=Bot(),main_keyboard=lambda:None,
          get_user_settings=finance_db.get_user_settings,get_user_telegram_id=finance_db.get_user_telegram_id,logger=None))
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/analytics/activity').status_code,200)
            self.assertEqual(client.get('/api/analytics/finance?month=abc').status_code,422)
            self.assertEqual(client.get('/api/analytics/finance?month=2026-10').json()['analytics']['current']['total_spent'],400)
            self.assertEqual(client.get('/api/analytics/finance?month=2026-10',headers={'x-test-user':str(self.b)}).json()['analytics']['current']['total_spent'],0)
            self.assertEqual(client.get('/api/automations').json()['rules'][0]['enabled'],False)
            self.assertEqual(client.put('/api/automations/budget_percent',json={'enabled':True,'threshold':80}).status_code,200)
            self.assertEqual(client.get('/api/automations',headers={'x-test-user':str(self.b)}).json()['rules'][0]['enabled'],False)
            self.assertEqual(client.put('/api/automations/budget_percent',json={'enabled':'yes','threshold':80}).status_code,422)
            self.assertEqual(client.post('/api/assistant',json={'question':'помощь'}).status_code,200)
            self.assertEqual(client.post('/api/assistant',json={'question':'Z'*250}).status_code,422)

if __name__=='__main__': unittest.main()
