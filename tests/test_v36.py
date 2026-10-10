"""V36 security / navigation regressions. Run: python -m unittest discover -s tests -v"""
import importlib
import os
import sqlite3
import stat
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from urllib.parse import urlencode
from fastapi.testclient import TestClient

class HardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if 'config' not in sys.modules:
            fake=types.ModuleType('config')
            fake.BOT_TOKEN='12345:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
            sys.modules['config']=fake
        try:
            import aiogram  # noqa: F401 - use production framework when available
        except ImportError:
            # Test the HTTP layer in minimal CI images without Telegram SDK.
            aiogram=types.ModuleType('aiogram')
            filters=types.ModuleType('aiogram.filters')
            types_mod=types.ModuleType('aiogram.types')
            errors_mod=types.ModuleType('aiogram.types.error_event')
            class Dummy:
                def __init__(self,*args,**kwargs):pass
                def __getattr__(self,_):return self
                def __eq__(self,_):return self
                def __call__(self,*args,**kwargs):return self
                def startswith(self,_):return self
                def message(self,*a,**kw):return lambda fn:fn
                def callback_query(self,*a,**kw):return lambda fn:fn
                def errors(self,*a,**kw):return lambda fn:fn
            aiogram.Bot=Dummy
            aiogram.Dispatcher=Dummy
            aiogram.F=Dummy()
            filters.Command=Dummy
            for name in ('BotCommand','BotCommandScopeChat','CallbackQuery','InlineKeyboardButton','InlineKeyboardMarkup','Message','WebAppInfo'):
                setattr(types_mod,name,Dummy)
            errors_mod.ErrorEvent=Dummy
            sys.modules.update({'aiogram':aiogram,'aiogram.filters':filters,'aiogram.types':types_mod,'aiogram.types.error_event':errors_mod})
        cls.backend=importlib.import_module('main')

    def setUp(self):
        import finance_db
        self.temp=tempfile.TemporaryDirectory()
        self.original=finance_db.DB_PATH
        finance_db.DB_PATH=Path(self.temp.name)/'test.sqlite'
        finance_db.init_db()
        self.client=TestClient(self.backend.app)

    def tearDown(self):
        import finance_db
        self.client.close()
        finance_db.DB_PATH=self.original
        self.temp.cleanup()

    def test_csrf_cookie_writes(self):
        headers={'Cookie':'svgtracker_session=fake_token'}
        self.assertEqual(self.client.post('/api/auth/logout',headers=headers).status_code,403)
        self.assertEqual(self.client.post('/api/auth/logout',headers={**headers,'Origin':'https://attacker.example'}).status_code,403)
        self.assertEqual(self.client.post('/api/auth/logout',headers={**headers,'Origin':'https://starslix.ru.attacker.example'}).status_code,403)
        self.assertEqual(self.client.post('/api/auth/logout',headers={**headers,'Origin':self.backend.PUBLIC_BASE_URL}).status_code,200)

    def test_no_cache_and_hardening_headers(self):
        r=self.client.get('/api/auth/session')
        self.assertEqual(r.status_code,200)
        self.assertIn('no-store',r.headers['cache-control'])
        self.assertEqual(r.headers['x-content-type-options'],'nosniff')
        self.assertEqual(r.headers['x-frame-options'],'DENY')
        self.assertEqual(r.headers['referrer-policy'],'no-referrer')

    def test_bodies_checked_before_processing(self):
        r=self.client.post('/api/diagnostics/frontend',headers={'Content-Length':str(3000000)})
        self.assertEqual(r.status_code,413)

    def test_telegram_initdata_rejects_duplicates_and_future(self):
        import hashlib,hmac,json
        token=self.backend.BOT_TOKEN
        user=json.dumps({'id':14001},separators=(',',':'))
        def make(date):
            pairs={'auth_date':str(date),'user':user}
            msg='\n'.join(f'{k}={pairs[k]}' for k in sorted(pairs))
            key=hmac.new(b'WebAppData',token.encode(),hashlib.sha256).digest()
            pairs['hash']=hmac.new(key,msg.encode(),hashlib.sha256).hexdigest()
            return urlencode(pairs)
        self.assertEqual(self.backend.verify_telegram_init_data(make(int(time.time())))['id'],14001)
        with self.assertRaises(self.backend.HTTPException):
            self.backend.verify_telegram_init_data(make(int(time.time()))+'&auth_date=0')
        with self.assertRaises(self.backend.HTTPException):
            self.backend.verify_telegram_init_data(make(int(time.time())+5000))

    def test_connections_closed_on_context_exit(self):
        import finance_db
        with finance_db.connect() as db:
            db.execute('SELECT 1').fetchone()
        with self.assertRaises(sqlite3.ProgrammingError):db.execute('SELECT 1')

    def test_home_actions_exist_and_are_valid(self):
        root=Path(__file__).resolve().parent.parent
        html=(root/'index.html').read_text()
        self.assertEqual(html.count('class="home-tool-action"'),4)
        js=(root/'product.js').read_text()
        for action in ('openGlobalSearch','openSVGAssistant','openActivityAnalytics','showReport'):
            self.assertIn('onclick="'+action+'(',html)
            self.assertIn('function '+action+'(',js)

if __name__=='__main__':unittest.main()
