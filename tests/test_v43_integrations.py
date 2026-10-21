"""V43 end-to-end API regression tests against isolated temporary SQLite files.

Covers the flows that earlier tests did not exercise as a complete journey.
No real Telegram messages, production database or network calls are used.
"""
from __future__ import annotations
import json
import uuid
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
import finance_db
from test_v37 import V37AuthRaceTests


class FullFeatureJourneys(V37AuthRaceTests):
    def setUp(self):
        super().setUp()
        self.client = TestClient(self.backend.app, raise_server_exceptions=False)
        self.a = self.headers(84300001)
        self.b = self.headers(84300002)

    def tearDown(self):
        self.client.close()
        super().tearDown()

    def test_notes_create_update_archive_search_and_delete(self):
        created = self.client.post('/api/notes', headers=self.a, json={
            'title': 'Тестовая заметка', 'body': 'молочный шоколад',
            'tags': ['дом', 'дела'], 'folder': 'Личное', 'favorite': True,
            'pinned': True,
        })
        self.assertEqual(created.status_code, 200, created.text)
        note = created.json()['note']
        nid = note['id']
        self.assertEqual(note['folder'], 'Личное')
        self.assertEqual(note['tags'], ['дом', 'дела'])
        self.assertTrue(note['favorite'])
        self.assertEqual(len(self.client.get('/api/search?q=шоколад', headers=self.a).json()['results']), 1)
        self.assertEqual(self.client.get('/api/search?q=шоколад', headers=self.b).json()['results'], [])
        response = self.client.put(f'/api/notes/{nid}', headers=self.a, json={
            'title': 'Архив', 'body': 'молочный шоколад', 'archived': True, 'folder': 'Старое', 'tags': []
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get('/api/notes', headers=self.a).json()['notes'], [])
        self.assertEqual(len(self.client.get('/api/notes?archived=true', headers=self.a).json()['notes']), 1)
        self.assertEqual(self.client.put(f'/api/notes/{nid}', headers=self.b, json={'title':'no'}).status_code, 403)
        self.assertEqual(self.client.delete(f'/api/notes/{nid}', headers=self.b).status_code, 404)
        self.assertEqual(self.client.delete(f'/api/notes/{nid}', headers=self.a).status_code, 200)
        self.assertEqual(self.client.put(f'/api/notes/{nid}', headers=self.a, json={'title':'resurrect'}).status_code, 404)
        self.assertEqual(self.client.get('/api/notes?archived=true', headers=self.a).json()['notes'], [])

    def test_chunked_attachment_round_trip_and_user_isolation(self):
        nid = self.client.post('/api/notes', headers=self.a, json={'title':'Файлы'}).json()['note']['id']
        token = uuid.uuid4().hex
        headers = dict(self.a, **{
            'X-Upload-Token': token, 'X-Chunk-Index': '0', 'X-Chunk-Total':'2',
            'X-File-Name':'example.txt', 'X-File-Size':'11', 'Content-Type':'text/plain',
        })
        first = self.client.post(f'/api/notes/{nid}/attachments/chunks', headers=headers, content=b'hello ')
        self.assertEqual(first.status_code, 200, first.text)
        headers['X-Chunk-Index'] = '1'
        last = self.client.post(f'/api/notes/{nid}/attachments/chunks', headers=headers, content=b'world')
        self.assertEqual(last.status_code, 200, last.text)
        files = self.client.get('/api/notes', headers=self.a).json()['notes'][0]['attachments']
        self.assertEqual(len(files), 1)
        attachment_id = files[0]['id']
        url=f'/api/notes/{nid}/attachments/{attachment_id}'
        self.assertEqual(self.client.get(url, headers=self.a).content, b'hello world')
        self.assertEqual(self.client.get(url, headers=self.b).status_code, 404)
        self.assertEqual(self.client.delete(url, headers=self.b).status_code, 404)
        self.assertEqual(self.client.delete(url, headers=self.a).status_code, 200)
        self.assertEqual(self.client.get(url, headers=self.a).status_code, 404)

    def test_schedule_weekly_create_update_and_isolation(self):
        event = {
            'title':'Лекция', 'details':'Зал 1',
            'starts_at':'2026-10-19T10:00:00Z', 'ends_at':'2026-10-19T11:00:00Z',
            'recurrence':'weekly', 'repeat_day_offsets':[0,2],
            'recurrence_timezone':'Europe/Helsinki', 'reminder_minutes':30,
        }
        created=self.client.post('/api/schedule/events', headers=self.a, json=event)
        self.assertEqual(created.status_code,200,created.text)
        nid=created.json()['event']['id']
        self.assertEqual(len(self.client.get('/api/schedule/events',headers=self.a).json()['events']),1)
        self.assertEqual(len(self.client.get('/api/schedule/events',headers=self.b).json()['events']),0)
        self.assertEqual(self.client.put(f'/api/schedule/events/{nid}',headers=self.b,json=event).status_code,403)
        event['title']='Лекция, новая'
        self.assertEqual(self.client.put(f'/api/schedule/events/{nid}',headers=self.a,json=event).status_code,200)
        self.assertEqual(self.client.get('/api/schedule/events',headers=self.a).json()['events'][0]['title'], 'Лекция, новая')
        self.assertEqual(self.client.delete(f'/api/schedule/events/{nid}',headers=self.a).status_code,200)
        self.assertEqual(self.client.get('/api/schedule/events',headers=self.a).json()['events'],[])

    def test_finance_training_and_analytics_from_persisted_state(self):
        self.assertEqual(self.client.put('/api/finance/state',headers=self.a,json={'state':{
            'version':5,'categories':[{'id':'cat_food','name':'Еда'}],
            'expenses':[{'id':'e1','categoryId':'cat_food','amount':300,'date':'2026-10-22'}],
            'incomes':[{'id':'i1','amount':500,'date':'2026-10-22'}],
            'monthlyBudgets':{'2026-10':{'income':2000}},
        }}).status_code,200)
        self.assertEqual(self.client.put('/api/training/state',headers=self.a,json={'state':{
            'goals':[{'id':'g1','name':'Плавание'}],
            'history':[{'id':'s1','status':'completed','dateKey':'2026-10-22','duration':1800}],
        }}).status_code,200)
        finance=self.client.get('/api/analytics/finance?month=2026-10',headers=self.a)
        self.assertEqual(finance.status_code,200, finance.text)
        self.assertEqual(finance.json()['analytics']['current']['total_spent'],300)
        self.assertEqual(finance.json()['analytics']['current']['income'],500)
        self.assertEqual(finance.json()['analytics']['current']['budget'],2000)
        training=self.client.get('/api/training/state',headers=self.a)
        self.assertEqual(training.json()['state']['goals'][0]['id'],'g1')
        self.assertFalse(self.client.get('/api/finance/state',headers=self.b).json()['exists'])
        report=self.client.get('/api/reports?period=daily&date=2026-10-22',headers=self.a)
        self.assertEqual(report.status_code,200, report.text)
        self.assertEqual(report.json()['report']['expenses'],300)
        self.assertEqual(report.json()['report']['workouts'],1)
        assistant=self.client.post('/api/assistant',headers=self.a,json={'question':'Что по бюджету?'})
        self.assertEqual(assistant.status_code,200, assistant.text)
        self.assertEqual(assistant.json()['external_ai'],False)
        self.assertIn('2 000',assistant.json()['answer'])

    def test_reject_invalid_finance_structure_without_overwriting_saved_data(self):
        good = {'state': {'expenses': [{'id': 'keep', 'amount': 12, 'date': '2026-10-22'}]}}
        self.assertEqual(self.client.put('/api/finance/state', headers=self.a, json=good).status_code, 200)
        bad = {'state': {'expenses': {'not': 'a list'}, 'monthlyBudgets': ['invalid']}}
        self.assertEqual(self.client.put('/api/finance/state', headers=self.a, json=bad).status_code, 422)
        saved = self.client.get('/api/finance/state', headers=self.a).json()['state']
        self.assertEqual(saved['expenses'][0]['id'], 'keep')

    def test_automations_settings_export_and_shortcut(self):
        rules=self.client.get('/api/automations',headers=self.a)
        self.assertEqual(rules.status_code,200,rules.text)
        updated=self.client.put('/api/automations/daily_expense',headers=self.a,json={'enabled':True,'threshold':1200})
        self.assertEqual(updated.status_code,200,updated.text)
        preferences=self.client.put('/api/notifications/preferences',headers=self.a,json={'preferences':{'daily_report':True}})
        self.assertEqual(preferences.status_code,200,preferences.text)
        key=self.client.post('/api/shortcut/token',headers=self.a).json()['token']
        with patch.object(self.backend.bot, 'send_message', new_callable=AsyncMock):
            receipt=self.client.post('/api/shortcut/finance/transaction',headers={'Authorization':'Bearer '+key},json={
                'type':'expense','amount':99,'category_name':'Еда','date':'2026-10-22'
            })
        self.assertEqual(receipt.status_code,200,receipt.text)
        self.assertTrue(receipt.json()['ok'])
        exported=self.client.get('/api/profile/export',headers=self.a)
        self.assertEqual(exported.status_code,200,exported.text)
        data=exported.json()
        self.assertEqual(data['format'],'svgtracker-export-v1')
        self.assertEqual(data['notification_preferences']['daily_report'], True)
        self.assertIn('training',data)
        self.assertNotIn(key,exported.text)
        self.assertEqual(self.client.get('/api/profile/export',headers=self.b).status_code,200)
        self.assertNotIn('84300001',self.client.get('/api/profile/export',headers=self.b).text)

if __name__ == '__main__': unittest.main()

# Regression for midnight-boundary double counting in reports/automations.
def test_v43_midnight_events_belong_only_to_the_next_day(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    import finance_db, product_db, intelligence, insights
    monkeypatch.setattr(finance_db,'DB_PATH',tmp_path/'test.db')
    monkeypatch.setattr(product_db,'ATTACHMENT_DIR',tmp_path/'attachments')
    finance_db.init_db(); product_db.init_product_db(); intelligence.init_intelligence_db()
    user_id=finance_db.get_or_create_user({'id':84309876,'first_name':'Midnight'})
    product_db.create_schedule_event(user_id,'midnight-v43',{
        'title':'Завтра в полночь', 'details':'', 'starts_at':'2026-10-23T00:00:00Z',
        'ends_at':'2026-10-23T01:00:00Z', 'recurrence':'none',
        'repeat_day_offsets':[0],'recurrence_timezone':'UTC','participant_ids':[],
        'reminder_minutes':None,
    })
    now=datetime(2026,10,22,18,tzinfo=timezone.utc)
    today=insights.build_report(user_id,'daily','2026-10-22',now)
    tomorrow=insights.build_report(user_id,'daily','2026-10-23',now)
    assert today['events']==0
    assert tomorrow['events']==1


def test_v43_legacy_finance_corruption_does_not_break_related_modules(tmp_path, monkeypatch):
    """A damaged pre-upgrade snapshot must not crash reports or bot reminders."""
    from datetime import datetime, timezone
    import finance_db, product_db, intelligence, insights
    monkeypatch.setattr(finance_db, 'DB_PATH', tmp_path / 'legacy.db')
    monkeypatch.setattr(product_db, 'ATTACHMENT_DIR', tmp_path / 'files')
    finance_db.init_db(); product_db.init_product_db(); intelligence.init_intelligence_db()
    user_id = finance_db.get_or_create_user({'id': 84309877, 'first_name': 'Legacy'})
    finance_db.save_finance_state(user_id, {
        'expenses': {'incorrect': 'old data'}, 'incomes': [None, {'amount': 15, 'date': '2026-10-22'}],
        'mandatoryExpenses': [{'amount': 20, 'paid': True, 'date': '2026-10-22'}, None],
        'debts': [None], 'categories': 'broken', 'monthlyBudgets': ['bad'],
    })
    finance_db.save_training_state(user_id, {'history': {'not': 'a list'}, 'goals': [None]})
    now = datetime(2026, 10, 22, 19, tzinfo=timezone.utc)
    assert insights.build_report(user_id, 'daily', '2026-10-22', now)['income'] == 15
    assert intelligence.financial_analytics(user_id, '2026-10', now)['current']['income'] == 15
    assert isinstance(insights.search_user_data(user_id, 'на'), list)
    assert isinstance(product_db.collect_due_reminders(now), list)
