"""V48: readable export and persistent Telegram administrator rights."""
import asyncio
import importlib
import json
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from admin_access import AdminRegistry
from account_export import export_html, export_text


class ReadableExportTests(unittest.TestCase):
    def test_russian_sections_no_json_dump_and_escaped_user_content(self):
        data = {
            'exported_at':'2026-10-22T12:00:00+00:00',
            'account':{'first_name':'<script>alert(1)</script>', 'username':'user'},
            'training':{'history':[{'title':'Силовая', 'durationMinutes':45}]},
            'finance':{'expenses':[{'title':'Кофе', 'amount':235.5}]},
            'notes':[{'title':'Заметка','body':'<img src=x onerror=alert(1)>'}],
            'schedule':[{'title':'День рождения'}],
            'settings':{}, 'notification_preferences':{}, 'friends':{},'automation_rules':[]
        }
        result=export_html(data)
        self.assertIn('lang="ru"',result)
        self.assertIn('Копия данных аккаунта',result)
        self.assertIn('235,50',result)
        self.assertIn('Силовая',result)
        self.assertIn('Расписание',result)
        self.assertNotIn('<script>',result)
        self.assertNotIn('<img src=x onerror=',result)
        self.assertIn('&lt;img src=x onerror=alert(1)&gt;',result)
        self.assertIn('@media print',result)
        self.assertNotIn('https://',result)
        txt=export_text(data)
        self.assertIn('ТРЕНИРОВКИ',txt)
        self.assertIn('Заметка',txt)
        self.assertIn('Сумма: 235,50',txt)

    def test_varied_empty_deep_and_long_data(self):
        payload={'exported_at':None}
        for key in ('account','settings','notification_preferences','friends','training','finance','schedule','notes','automation_rules'):
            payload[key]={'nested':{'info':[{},[],None,True,False,0,'ä and &']}}
        html=export_html(payload)
        self.assertIn('Нет записей',html)
        self.assertIn('ä and &amp;',html)
        self.assertGreater(len(export_text(payload)),100)

    def test_http_export_scoped_auth_content_type_and_legacy_json(self):
        from test_v43_integrations import FullFeatureJourneys
        FullFeatureJourneys.setUpClass()
        test=FullFeatureJourneys('runTest')
        test.setUp()
        try:
            original=test.client.get('/api/profile/export?format=json', headers=test.a)
            self.assertEqual(original.status_code,200)
            self.assertEqual(original.json()['format'],'svgtracker-export-v1')
            readable=test.client.get('/api/profile/export?format=html', headers=test.a)
            self.assertEqual(readable.status_code,200,readable.text)
            self.assertIn('text/html',readable.headers['content-type'])
            self.assertIn('.html',readable.headers['content-disposition'])
            self.assertIn('no-store',readable.headers['cache-control'])
            self.assertIn("default-src 'none'",readable.headers['content-security-policy'])
            self.assertIn('Копия данных аккаунта',readable.text)
            text=test.client.get('/api/profile/export?format=txt',headers=test.a)
            self.assertEqual(text.status_code,200)
            self.assertIn('text/plain',text.headers['content-type'])
            self.assertEqual(test.client.get('/api/profile/export?format=xml',headers=test.a).status_code,422)
            self.assertNotIn('84300001', test.client.get('/api/profile/export?format=html',headers=test.b).text)
            self.assertIn(test.client.get('/api/profile/export?format=html',headers=test.b).status_code,(200,))
            self.assertIn(test.client.get('/api/profile/export?format=html').status_code,(401,403))
        finally:
            test.tearDown()


class AdminAccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'secure'
        self.registry=AdminRegistry(10001,self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_owner_is_permanent_and_add_remove_survive_reload(self):
        self.assertEqual(self.registry.list_ids(),[10001])
        self.assertTrue(self.registry.is_admin('10001'))
        self.assertFalse(self.registry.is_admin('10002'))
        self.assertTrue(self.registry.change(10001,10002,'grant'))
        self.assertTrue(self.registry.is_admin(10002))
        rebooted=AdminRegistry(10001,self.path)
        self.assertTrue(rebooted.is_admin(10002))
        self.assertFalse(rebooted.change(10002,10002,'grant'))
        self.assertTrue(rebooted.change(10002,10003,'grant'))
        self.assertTrue(rebooted.change(10003,10002,'revoke'))
        self.assertFalse(rebooted.is_admin(10002))
        with self.assertRaises(PermissionError):rebooted.change(10002,10004,'grant')
        with self.assertRaises(ValueError):rebooted.change(10003,10001,'revoke')
        with self.assertRaises(ValueError):rebooted.change(10003,10003,'revoke')
        self.assertTrue(rebooted.is_admin(10001))
        self.assertEqual(len(self.registry.audit_path.read_text().splitlines()),3)

    def test_invalid_ids_corrupted_file_and_mode(self):
        for invalid in ['-1','abc','1 2','1.5',0,True,2**65,'1e3','1\n2']:
            self.assertFalse(self.registry.is_admin(invalid),invalid)
            with self.assertRaises(ValueError):self.registry.change(10001,invalid,'grant')
        self.registry.change(10001,10002,'grant')
        self.assertEqual(stat.S_IMODE(self.registry.path.stat().st_mode),0o600)
        self.assertEqual(stat.S_IMODE(self.registry.lock_path.stat().st_mode),0o600)
        self.assertEqual(stat.S_IMODE(self.registry.audit_path.stat().st_mode),0o600)
        self.registry.path.write_text('{broken-json', encoding='utf-8')
        self.assertFalse(self.registry.is_admin(10002))
        self.assertTrue(self.registry.is_admin(10001))
        with self.assertRaises(RuntimeError):self.registry.change(10001,10005,'grant')
        self.assertEqual(self.registry.path.read_text(),'{broken-json')

    def test_parallel_updates_not_lost(self):
        errors=[]
        def worker(i):
            try:self.registry.change(10001,i,'grant')
            except Exception as e:errors.append(e)
        threads=[threading.Thread(target=worker,args=(20000+i,)) for i in range(12)]
        for t in threads:t.start()
        for t in threads:t.join(timeout=5)
        self.assertFalse(errors,errors)
        self.assertEqual(len(self.registry.list_ids()),13)
        self.assertEqual(len(json.loads(self.registry.path.read_text())['admins']),13)


class AdminBotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        from test_v36 import HardeningTests
        HardeningTests.setUpClass()
        self.backend=importlib.import_module('main')
        self.registry=AdminRegistry(self.backend.ADMIN_TELEGRAM_ID,Path(self.tmp.name))
        self.patch=patch.object(self.backend,'ADMIN_REGISTRY',self.registry)
        self.patch.start()

    async def asyncTearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    async def test_unprivileged_admin_add_denied_and_private_required(self):
        msg=SimpleNamespace(from_user=SimpleNamespace(id=999),chat=SimpleNamespace(type='private'),text='/admin_add 1234',answer=AsyncMock())
        await self.backend.admin_add_command(msg)
        msg.answer.assert_not_called()
        msg.from_user.id=self.registry.owner_id
        msg.chat.type='group'
        await self.backend.admin_add_command(msg)
        self.assertIn('только',msg.answer.await_args.args[0])
        self.assertFalse(self.registry.is_admin(1234))

    async def test_confirmation_actor_id_checked_and_revoked_user_denied(self):
        owner=self.registry.owner_id
        msg=SimpleNamespace(from_user=SimpleNamespace(id=owner),chat=SimpleNamespace(type='private'),text='/admin_add 1234',answer=AsyncMock())
        def kb_button(**kwargs):return SimpleNamespace(**kwargs)
        def kb_markup(**kwargs):return SimpleNamespace(**kwargs)
        with patch.object(self.backend,'InlineKeyboardButton',side_effect=kb_button),patch.object(self.backend,'InlineKeyboardMarkup',side_effect=kb_markup):
            await self.backend.admin_add_command(msg)
        self.assertFalse(self.registry.is_admin(1234))
        markup=msg.answer.await_args.kwargs['reply_markup']
        token=markup.inline_keyboard[0][0].callback_data.split(':')[-1]
        class C:
            def __init__(self,user):
                self.from_user=SimpleNamespace(id=user)
                self.message=SimpleNamespace(chat=SimpleNamespace(type='private'),answer=AsyncMock(),edit_text=AsyncMock())
                self.data=f'admin:role_confirm:{token}'
                self.answer=AsyncMock()
        user=C(999)
        await self.backend.admin_callback(user)
        self.assertFalse(self.registry.is_admin(1234))
        approved=C(owner)
        with patch.object(self.backend,'refresh_admin_commands',new_callable=AsyncMock),patch.object(self.backend.bot,'send_message',new_callable=AsyncMock):
            await self.backend.admin_callback(approved)
        self.assertTrue(self.registry.is_admin(1234))
        await self.backend.admin_callback(approved)
        self.assertTrue(self.registry.is_admin(1234))
        self.registry.change(owner,1234,'revoke')
        fake=SimpleNamespace(from_user=SimpleNamespace(id=1234),chat=SimpleNamespace(type='private'),text='/admin_remove 456',answer=AsyncMock())
        await self.backend.admin_remove_command(fake)
        fake.answer.assert_not_called()
