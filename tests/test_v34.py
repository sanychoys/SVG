"""Run with python -m unittest discover -s tests -v"""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
import finance_db
import product_db
import insights
from fastapi import FastAPI
from fastapi.testclient import TestClient
from product_api import build_product_router

class V34Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.old_db=finance_db.DB_PATH
        self.old_attach=product_db.ATTACHMENT_DIR
        finance_db.DB_PATH=Path(self.tmp.name)/'db.sqlite'
        product_db.ATTACHMENT_DIR=Path(self.tmp.name)/'files'
        finance_db.init_db();product_db.init_product_db()
        self.a=finance_db.get_or_create_user({'id':7301,'first_name':'Alpha'})
        self.b=finance_db.get_or_create_user({'id':7302,'first_name':'Beta'})

    def tearDown(self):
        finance_db.DB_PATH=self.old_db
        product_db.ATTACHMENT_DIR=self.old_attach
        self.tmp.cleanup()

    def test_notes_tags_folders_favorites_archive_and_isolation(self):
        note=product_db.save_note(self.a,'note_1',{'title':'Личный проект','body':'Эскиз','folder':'Работа','tags':['идея','дизайн'],'favorite':True,'pinned':False,'archived':False})
        self.assertEqual(note['tags'],['идея','дизайн']);self.assertTrue(note['favorite'])
        self.assertEqual(note['folder'],'Работа');self.assertEqual(len(product_db.list_notes(self.a)),1)
        self.assertFalse(product_db.save_note(self.b,'note_1',{'title':'изменение'}))
        self.assertEqual(product_db.list_notes(self.b),[])
        self.assertEqual([r['kind'] for r in insights.search_user_data(self.a,'эскиз')],['note'])
        self.assertEqual(insights.search_user_data(self.b,'эскиз'),[])
        product_db.save_note(self.a,'note_1',{'title':'Личный проект','body':'Эскиз','folder':'Работа','tags':['идея'],'favorite':True,'archived':True})
        self.assertEqual(len(product_db.list_notes(self.a)),0)
        self.assertEqual(len(product_db.list_notes(self.a,True)),1)

    def test_search_data_is_not_cross_account(self):
        finance_db.save_finance_state(self.a,{'expenses':[{'id':'exp1','title':'Проезд','amount':150,'date':'2026-10-10'}]})
        self.assertEqual(insights.search_user_data(self.b,'проезд'),[])
        result=insights.search_user_data(self.a,'проезд')
        self.assertEqual(len(result),1);self.assertEqual(result[0]['kind'],'finance')

    def test_report_uses_saved_data_and_week(self):
        finance_db.save_finance_state(self.a,{'expenses':[{'amount':199,'date':'2026-10-10','title':'food'}], 'incomes':[{'amount':1000,'date':'2026-10-10'}]})
        finance_db.save_training_state(self.a,{'history':[{'status':'completed','dateKey':'2026-10-10','duration':1800,'workout':'Cardio'}]})
        product_db.save_note(self.a,'note_2',{'title':'План','body':'текст'})
        r=insights.build_report(self.a,'daily',anchor='2026-10-10',now=datetime(2026,10,10,16,tzinfo=timezone.utc))
        self.assertEqual(r['expenses'],199);self.assertEqual(r['income'],1000)
        self.assertEqual(r['workouts'],1);self.assertEqual(r['training_minutes'],30)
        weekly=insights.build_report(self.a,'weekly',anchor='2026-10-10',now=datetime(2026,10,10,16,tzinfo=timezone.utc))
        self.assertEqual(weekly['start'],'2026-10-05');self.assertEqual(weekly['end'],'2026-10-11')
        self.assertEqual(weekly['expenses'],199)

    def test_api_search_reports_and_note_validation(self):
        app=FastAPI()
        def auth(request,ignored):
            uid=int(request.headers.get('x-test-user',str(self.a)))
            return ({'first_name':'User'},uid)
        class Bot: pass
        app.include_router(build_product_router(authenticate=auth,bot=Bot(),main_keyboard=lambda:None,
                   get_user_settings=finance_db.get_user_settings,get_user_telegram_id=finance_db.get_user_telegram_id,logger=None))
        with TestClient(app) as client:
            res=client.post('/api/notes',json={'title':'My notes','body':'top secret','tags':['mine'],'folder':'Private','favorite':True})
            self.assertEqual(res.status_code,200,res.text)
            n=res.json()['note'];self.assertEqual(n['folder'],'Private')
            self.assertTrue(n['favorite']);self.assertEqual(n['tags'],['mine'])
            self.assertEqual(client.get('/api/search?q=top%20secret').json()['results'][0]['id'],n['id'])
            self.assertEqual(client.get('/api/search?q=top%20secret',headers={'x-test-user':str(self.b)}).json()['results'],[])
            self.assertEqual(client.get('/api/reports?period=daily').status_code,200)
            self.assertEqual(client.get('/api/reports?period=foo').status_code,422)
            self.assertEqual(client.post('/api/notes',json={'title':'oops','tags':'wrong'}).status_code,422)
            self.assertEqual(client.put('/api/notes/'+n['id'],headers={'x-test-user':str(self.b)},json={'title':'steal'}).status_code,403)

    def test_reports_opt_in_and_no_duplicates(self):
        now=datetime(2026,10,11,21,0,tzinfo=timezone.utc)
        self.assertFalse(any(r['user_id']==self.a for r in insights.collect_due_reports(now)))
        product_db.update_notification_preferences(self.a,{'daily_report':True,'weekly_report':True})
        due=[r for r in insights.collect_due_reports(now) if r['user_id']==self.a]
        self.assertEqual({r['period'] for r in due},{'daily','weekly'})
        for r in due:product_db.mark_reminder_sent(r['user_id'],r['key'])
        self.assertFalse(any(r['user_id']==self.a for r in insights.collect_due_reports(now)))
        product_db.update_notification_preferences(self.b,{'daily_report':True})
        finance_db.set_bot_notifications(self.b,False)
        self.assertFalse(any(r['user_id']==self.b for r in insights.collect_due_reports(now)))

if __name__=='__main__':unittest.main()
