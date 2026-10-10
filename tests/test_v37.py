"""V37 SQLite concurrency and real Mini App API regression tests.

Run: cd TRC && python -m unittest discover -s tests -v
No production database or Telegram bot is accessed.
"""
import hashlib
import hmac
import importlib
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlencode

from fastapi.testclient import TestClient
import finance_db
import product_db
import intelligence


class V37AuthRaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Reuse the same Telegram-SDK test stub that the prior release uses if
        # SDK is not installed in the test environment.
        from test_v36 import HardeningTests
        HardeningTests.setUpClass()
        cls.backend = importlib.import_module('main')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = finance_db.DB_PATH
        self.old_attachment_path = product_db.ATTACHMENT_DIR
        finance_db.DB_PATH = Path(self.tmp.name) / 'svgtracker.db'
        product_db.ATTACHMENT_DIR = Path(self.tmp.name) / 'private_files'
        finance_db.init_db()
        product_db.init_product_db()
        intelligence.init_intelligence_db()

    def tearDown(self):
        finance_db.DB_PATH = self.old_db_path
        product_db.ATTACHMENT_DIR = self.old_attachment_path
        self.tmp.cleanup()

    def headers(self, telegram_id):
        user = json.dumps({'id': telegram_id, 'first_name': 'Новый', 'username': 'svg_test'},
                          ensure_ascii=False, separators=(',', ':'))
        payload = {'auth_date': str(int(time.time())), 'user': user}
        msg = '\n'.join(f'{k}={payload[k]}' for k in sorted(payload))
        key = hmac.new(b'WebAppData', self.backend.BOT_TOKEN.encode(), hashlib.sha256).digest()
        payload['hash'] = hmac.new(key, msg.encode(), hashlib.sha256).hexdigest()
        return {'X-Telegram-Init-Data': urlencode(payload)}

    def test_exact_original_unique_error_no_longer_happens(self):
        # V36 intermittently produced UNIQUE(users.telegram_id) in this test.
        for batch in range(5):
            tid = 950001 + batch
            barrier = threading.Barrier(20)
            def one(_):
                barrier.wait(timeout=15)
                return finance_db.get_or_create_user({'id': tid, 'first_name': 'Test'})
            with ThreadPoolExecutor(max_workers=20) as pool:
                results = list(pool.map(one, range(20)))
            self.assertEqual(len(set(results)), 1)
            with finance_db.connect() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM users WHERE telegram_id=?',
                                            (str(tid),)).fetchone()[0], 1)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM user_settings WHERE user_id=?',
                                            (results[0],)).fetchone()[0], 1)

    def test_simultaneous_actual_http_state_requests(self):
        tid = 960001
        barrier = threading.Barrier(20)
        headers = self.headers(tid)
        def one(i):
            barrier.wait(timeout=15)
            with TestClient(self.backend.app, raise_server_exceptions=False) as client:
                endpoint = '/api/training/state' if i % 2 else '/api/finance/state'
                response = client.get(endpoint, headers=headers)
                return endpoint, response.status_code, response.json()
        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(one, range(20)))
        for endpoint, status, data in results:
            self.assertEqual(status, 200, f'{endpoint}: {data}')
            self.assertEqual(data.get('status'), 'ok')
            self.assertFalse(data['exists'])
        with finance_db.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM users WHERE telegram_id=?',
                                        (str(tid),)).fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM user_settings').fetchone()[0], 1)
        # A second visit should not reinsert or reset the user's settings.
        with TestClient(self.backend.app) as client:
            response = client.get('/api/finance/state', headers=headers)
            self.assertEqual(response.status_code, 200)

    def test_profile_data_and_settings_preserved_when_visited_again(self):
        first = finance_db.get_or_create_user({
            'id': 970001, 'first_name': 'Первый', 'username': 'first_name', 'photo_url': 'pic'
        })
        finance_db.set_bot_notifications(first, False)
        second = finance_db.get_or_create_user({'id': 970001, 'first_name': None})
        self.assertEqual(first, second)
        with finance_db.connect() as db:
            row = db.execute('SELECT first_name,username,photo_url FROM users WHERE id=?',
                             (first,)).fetchone()
            self.assertEqual(row['first_name'], 'Первый')
            self.assertEqual(row['username'], 'first_name')
            self.assertEqual(row['photo_url'], 'pic')
        self.assertFalse(finance_db.get_user_settings(first)['bot_notifications'])
        finance_db.get_or_create_user({'id': 970001, 'first_name': 'Обновлённый'})
        with finance_db.connect() as db:
            self.assertEqual(db.execute('SELECT first_name FROM users WHERE id=?',
                                        (first,)).fetchone()[0], 'Обновлённый')

    def test_two_distinct_users_never_share_data(self):
        barrier = threading.Barrier(20)
        def one(i):
            barrier.wait(timeout=15)
            return i % 2, finance_db.get_or_create_user({'id': 980001 + i % 2})
        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(one, range(20)))
        users = {k: set() for k in (0, 1)}
        for k, uid in results:
            users[k].add(uid)
        self.assertEqual([len(v) for v in users.values()], [1, 1])
        self.assertNotEqual(next(iter(users[0])), next(iter(users[1])))

    def test_missing_settings_are_repaired_without_recreating_account(self):
        uid = finance_db.get_or_create_user({'id': 990001, 'first_name': 'Old'})
        with finance_db.connect() as db:
            db.execute('DELETE FROM user_settings WHERE user_id=?', (uid,))
        self.assertEqual(finance_db.get_or_create_user({'id': 990001}), uid)
        with finance_db.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM users WHERE telegram_id=?',
                                        ('990001',)).fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM user_settings WHERE user_id=?',
                                        (uid,)).fetchone()[0], 1)

    def test_existing_training_and_finance_records_survive_relogin(self):
        tid = 991001
        uid = finance_db.get_or_create_user({'id': tid, 'first_name': 'Old'})
        finance_db.save_training_state(uid, {'goals': [{'id': 'goal-persisted'}]})
        finance_db.save_finance_state(uid, {'expenses': [{'id': 'expense-persisted', 'amount': 99}]})
        with TestClient(self.backend.app, raise_server_exceptions=False) as client:
            training = client.get('/api/training/state', headers=self.headers(tid))
            finance = client.get('/api/finance/state', headers=self.headers(tid))
            self.assertEqual(training.status_code, 200)
            self.assertEqual(finance.status_code, 200)
            self.assertEqual(training.json()['state']['goals'][0]['id'], 'goal-persisted')
            self.assertEqual(finance.json()['state']['expenses'][0]['id'], 'expense-persisted')
        with finance_db.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM users WHERE telegram_id=?',
                                        (str(tid),)).fetchone()[0], 1)

    def test_readiness_detects_broken_database(self):
        with TestClient(self.backend.app, raise_server_exceptions=False) as client:
            healthy = client.get('/api/test')
            self.assertEqual(healthy.status_code, 200, healthy.text)
            with finance_db.connect() as db:
                db.execute('DROP TABLE finance_state')
            broken = client.get('/api/test')
            self.assertEqual(broken.status_code, 503)

if __name__ == '__main__':
    unittest.main()
