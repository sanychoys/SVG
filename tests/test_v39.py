"""V39 API launch, legacy Telegram authorization and singleton polling regressions."""
import fcntl
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from test_v37 import V37AuthRaceTests


class MiniAppLaunchV39(V37AuthRaceTests):
    def setUp(self):
        super().setUp()
        self.client = TestClient(self.backend.app, raise_server_exceptions=False)
        self.header = self.headers(76540112)

    def tearDown(self):
        self.client.close()
        super().tearDown()

    def test_identity_preflight_authenticated_and_unsigned(self):
        self.assertEqual(self.client.get('/api/user').status_code, 401)
        response = self.client.get('/api/user', headers=self.header)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['telegram_id'], '76540112')
        self.assertEqual(self.client.get('/api/test').json()['version'], '39')

    def test_legacy_authenticate_accepts_only_signed_telegram_initdata(self):
        self.assertEqual(self.client.post('/api/authenticate', headers=self.header).status_code, 200)
        signed = self.header['X-Telegram-Init-Data']
        response = self.client.post('/api/authenticate', json={'initData': signed})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['authenticated'])
        self.assertEqual(response.json()['telegram_id'], '76540112')
        self.assertEqual(self.client.post('/api/authenticate', json={'user': {'id': 76540112}}).status_code, 401)
        self.assertEqual(self.client.post('/api/authenticate', json={'initData': signed[:-2] + 'aa'}).status_code, 401)
        self.assertEqual(self.client.post('/api/authenticate', data='not JSON').status_code, 400)

    def test_legacy_large_body_rejected(self):
        response = self.client.post('/api/authenticate', content=b'{' + b' ' * 16000 + b'}',
                                    headers={'content-type': 'application/json'})
        self.assertEqual(response.status_code, 413)

    def test_duplicate_same_host_service_lock_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(self.backend, 'ADMIN_STATE_DIR', Path(directory)):
                handle = self.backend.lock_single_svgtracker_instance()
                try:
                    self.assertTrue((Path(directory) / 'service-instance.lock').is_file())
                    with (Path(directory) / 'service-instance.lock').open('r+') as second:
                        with self.assertRaises(BlockingIOError):
                            fcntl.flock(second.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    handle.close()
                with (Path(directory) / 'service-instance.lock').open('r+') as free:
                    fcntl.flock(free.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(free.fileno(), fcntl.LOCK_UN)


class DeployHealthV39(unittest.TestCase):
    def test_new_release_version_detected(self):
        path = Path(__file__).resolve().parents[1] / 'deploy' / 'admin_deploy.py'
        spec = importlib.util.spec_from_file_location('deploy_for_test', path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertEqual(mod.release_app_version(path.parents[1]), '39')


if __name__ == '__main__':
    unittest.main()
