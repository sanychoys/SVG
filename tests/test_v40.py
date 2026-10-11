"""V40 Mini App normal startup and asset resilience with preserved Shortcuts."""
import pathlib
import unittest
from fastapi.testclient import TestClient
from test_v37 import V37AuthRaceTests


class V40StartupTests(V37AuthRaceTests):
    def setUp(self):
        super().setUp()
        self.client = TestClient(self.backend.app, raise_server_exceptions=False)

    def tearDown(self):
        self.client.close()
        super().tearDown()

    def test_client_assets_served_from_api_with_exact_types(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        for name, content_type in (
            ('style.css', 'text/css'), ('product.css', 'text/css'),
            ('script.js', 'text/javascript'), ('product.js', 'text/javascript'),
        ):
            result = self.client.get(f'/api/client/{name}?v=40')
            self.assertEqual(result.status_code, 200, name)
            self.assertTrue(result.headers['content-type'].startswith(content_type), name)
            self.assertEqual(result.content, (root / name).read_bytes())
            self.assertIn('no-store', result.headers['cache-control'])
            self.assertEqual(result.headers['x-content-type-options'], 'nosniff')

    def test_client_assets_never_expose_private_source(self):
        for name in ('main.py', 'config.py', 'svgtracker.db', '.env', 'key.py',
                     'tests', '.git', 'deploy', 'anything.js', 'index.html'):
            self.assertEqual(self.client.get('/api/client/'+name).status_code, 404, name)
        self.assertEqual(self.client.get('/api/client/../main.py').status_code, 404)

    def test_existing_iphone_shortcuts_require_signed_user(self):
        self.assertEqual(self.client.get('/api/shortcut/setup').status_code, 401)
        response = self.client.get('/api/shortcut/setup', headers=self.headers(87550998))
        self.assertEqual(response.status_code, 200)
        self.assertIn('options_url', response.json())
        self.assertIn('transaction_url', response.json())

    def test_frontend_is_restored_and_web_gate_explicit(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        html = (root / 'index.html').read_text()
        js = (root / 'script.js').read_text()
        self.assertIn('/api/client/script.js?v=40', html)
        self.assertIn('/api/client/style.css?v=40', html)
        self.assertIn("classList.add('svg-miniapp','svg-data-pending')", html)
        self.assertIn('svg-miniapp', html)
        self.assertIn('initData', html)
        self.assertIn('function openShortcutSetup', js)
        self.assertNotIn("fetch('/api/user', {\n          headers: telegramApiHeaders(false), cache: 'no-store'", js)
        self.assertEqual(self.client.get('/api/test').json()['version'], '40')


if __name__ == '__main__': unittest.main()
