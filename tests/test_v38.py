"""V38 iPhone Shortcuts onboarding: auth, token rotation, isolated accounts, API contract."""
import importlib
import json
import time
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import finance_db
from test_v37 import V37AuthRaceTests


class IPhoneShortcutsTests(V37AuthRaceTests):
    def setUp(self):
        super().setUp()
        self.client = TestClient(self.backend.app, raise_server_exceptions=False)
        self.header_a = self.headers(87550001)
        self.header_b = self.headers(87550002)

    def tearDown(self):
        self.client.close()
        super().tearDown()

    def test_unauthenticated_cannot_issue_or_read(self):
        for path, method in (('/api/shortcut/setup', 'get'),
                             ('/api/shortcut/token', 'post'),
                             ('/api/shortcut/revoke', 'post')):
            response = getattr(self.client, method)(path)
            self.assertEqual(response.status_code, 401, (path, response.text))

    def test_generate_once_and_revoke_no_raw_token_reexposure(self):
        initial = self.client.get('/api/shortcut/setup', headers=self.header_a)
        self.assertEqual(initial.status_code, 200)
        self.assertFalse(initial.json()['active'])
        self.assertNotIn('token', initial.json())
        generated = self.client.post('/api/shortcut/token', headers=self.header_a)
        self.assertEqual(generated.status_code, 200, generated.text)
        key = generated.json()['token']
        self.assertGreater(len(key), 30)
        active = self.client.get('/api/shortcut/setup', headers=self.header_a)
        self.assertTrue(active.json()['active'])
        self.assertNotIn(key, active.text)
        self.assertNotIn('token', active.json())
        self.assertTrue(active.json()['options_url'].startswith('https://'))
        self.assertTrue(active.json()['transaction_url'].startswith('https://'))
        # Token can access only the intended shortcut API.
        choices = self.client.get('/api/shortcut/finance/options', headers={'Authorization': 'Bearer '+key})
        self.assertEqual(choices.status_code, 200)
        self.assertTrue(choices.json()['category_names'])
        revoked = self.client.post('/api/shortcut/revoke', headers=self.header_a)
        self.assertEqual(revoked.status_code, 200)
        self.assertFalse(revoked.json()['active'])
        self.assertEqual(self.client.get('/api/shortcut/finance/options', headers={'Authorization': 'Bearer '+key}).status_code, 401)
        self.assertFalse(self.client.get('/api/shortcut/setup', headers=self.header_a).json()['active'])

    def test_rotation_rejects_old_token_and_isolates_accounts(self):
        old = self.client.post('/api/shortcut/token', headers=self.header_a).json()['token']
        new = self.client.post('/api/shortcut/token', headers=self.header_a).json()['token']
        self.assertNotEqual(old, new)
        self.assertEqual(self.client.get('/api/shortcut/finance/options', headers={'Authorization': 'Bearer '+old}).status_code, 401)
        self.assertEqual(self.client.get('/api/shortcut/finance/options', headers={'Authorization': 'Bearer '+new}).status_code, 200)
        self.assertFalse(self.client.get('/api/shortcut/setup', headers=self.header_b).json()['active'])
        self.client.post('/api/shortcut/revoke', headers=self.header_b)
        self.assertEqual(self.client.get('/api/shortcut/finance/options', headers={'Authorization': 'Bearer '+new}).status_code, 200)
        other = self.client.post('/api/shortcut/token', headers=self.header_b).json()['token']
        self.assertNotEqual(new, other)
        self.client.post('/api/shortcut/revoke', headers=self.header_a)
        self.assertEqual(self.client.get('/api/shortcut/finance/options', headers={'Authorization': 'Bearer '+other}).status_code, 200)

    def test_saving_expense_through_shortcut_uses_existing_finances(self):
        key = self.client.post('/api/shortcut/token', headers=self.header_a).json()['token']
        auth = {'Authorization': 'Bearer '+key}
        data = {'type': 'expense', 'category_name': 'Еда', 'amount': 125,
                'date': '2026-10-11'}
        with patch.object(self.backend.bot, 'send_message', new_callable=AsyncMock):
            response = self.client.post('/api/shortcut/finance/transaction', headers=auth, json=data)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['ok'])
        state = self.client.get('/api/finance/state', headers=self.header_a).json()['state']
        self.assertEqual(len(state['expenses']), 1)
        self.assertEqual(state['expenses'][0]['amount'], 125)
        other = self.client.get('/api/finance/state', headers=self.header_b).json()
        self.assertFalse(other['exists'])

    def test_only_real_icloud_share_urls_are_exposed(self):
        prior = self.backend.SHORTCUT_ICLOUD_URL
        try:
            self.backend.SHORTCUT_ICLOUD_URL = 'https://example.com/shortcuts/abcdef0123456789abcdef0123456789'
            self.assertIsNone(self.client.get('/api/shortcut/setup', headers=self.header_a).json()['icloud_url'])
            self.backend.SHORTCUT_ICLOUD_URL = 'https://www.icloud.com/shortcuts/abcdef0123456789abcdef0123456789'
            self.assertEqual(self.client.get('/api/shortcut/setup', headers=self.header_a).json()['icloud_url'], self.backend.SHORTCUT_ICLOUD_URL)
            self.backend.SHORTCUT_ICLOUD_URL += '?bad=1'
            self.assertIsNone(self.client.get('/api/shortcut/setup', headers=self.header_a).json()['icloud_url'])
        finally:
            self.backend.SHORTCUT_ICLOUD_URL = prior

    def test_token_not_returned_by_profile_or_ordinary_state(self):
        token = self.client.post('/api/shortcut/token', headers=self.header_a).json()['token']
        for endpoint in ('/api/profile', '/api/training/state', '/api/finance/state'):
            response = self.client.get(endpoint, headers=self.header_a)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(token, response.text)


if __name__ == '__main__': unittest.main()
