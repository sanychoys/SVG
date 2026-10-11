"""V42 phone-first admin deployment safety regression checks."""
import importlib
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from test_v36 import HardeningTests


class DeployRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        HardeningTests.setUpClass()
        cls.backend = importlib.import_module('main')
        cls.helper = importlib.import_module('deploy.admin_deploy')

    def test_noop_probe_does_not_restart_production(self):
        calls = []
        def fake_run(command, **kwargs):
            calls.append(command)
            return SimpleNamespace(returncode=0, stdout='Running as unit svgtracker-probe-test.service', stderr='')
        with patch.object(self.backend.subprocess, 'run', side_effect=fake_run):
            result = self.backend.admin_systemd_probe_text()
        self.assertIn('systemd-run: OK', result)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], 'systemd-run')
        self.assertTrue(calls[0][-1].endswith('true'))
        self.assertNotIn('restart', ' '.join(calls[0]))

    def test_launch_failure_is_detailed_and_does_not_retry_automatically(self):
        calls = []
        def fail_run(command, **kwargs):
            calls.append(command)
            return SimpleNamespace(returncode=1, stdout='', stderr='Failed to connect to bus')
        with patch.object(self.backend.subprocess, 'run', side_effect=fail_run), \
             patch.object(self.backend, '_systemd_unit_seen', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'Failed to connect to bus'):
                self.backend.admin_launch_transient(['deploy','/tmp/nonexistent.zip'], 'deploy')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], 'systemd-run')

    def test_systemd_transport_error_with_unit_present_does_not_launch_again(self):
        with patch.object(self.backend.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout='', stderr='connection closed')) as run, \
             patch.object(self.backend, '_systemd_unit_seen', return_value=True):
            unit = self.backend.admin_launch_transient(['deploy', '/tmp/placeholder.zip'], 'deploy')
        self.assertTrue(unit.startswith('svgtracker-deploy-'))
        self.assertEqual(run.call_count, 1)

    def test_exclusive_worker_blocks_parallel_operations_and_keeps_inode(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state, backups, log, lock = self.helper.STATE_DIR, self.helper.BACKUP_DIR, self.helper.LOG_DIR, self.helper.DEPLOY_LOCK
            self.helper.STATE_DIR = root / 'state'
            self.helper.BACKUP_DIR = root / 'backups'
            self.helper.LOG_DIR = root / 'state/logs'
            self.helper.DEPLOY_LOCK = self.helper.STATE_DIR / 'deploy.lock'
            results = []
            started, release = threading.Event(), threading.Event()
            def slow():
                started.set()
                self.assertTrue(release.wait(4))
                return 0
            def run():
                results.append(self.helper._exclusive_worker('deploy', slow, None))
            try:
                worker = threading.Thread(target=run)
                worker.start()
                self.assertTrue(started.wait(2))
                with patch.object(self.helper, 'telegram_notify'):
                    blocked = self.helper._exclusive_worker('rollback', lambda: 0, None)
                self.assertEqual(blocked, 4)
                release.set()
                worker.join(4)
                self.assertEqual(results, [0])
                self.assertTrue(self.helper.DEPLOY_LOCK.exists())
                self.assertEqual(self.helper._exclusive_worker('deploy', lambda: 7, None), 7)
            finally:
                release.set()
                self.helper.STATE_DIR, self.helper.BACKUP_DIR, self.helper.LOG_DIR, self.helper.DEPLOY_LOCK = state, backups, log, lock

    def test_phone_diagnostics_do_not_include_secrets(self):
        with patch.object(self.backend, 'admin_latest_status', return_value={'status': 'success', 'updated_at':'now','archive':'v41.zip','github_commit':'abcd'}), \
             patch.object(self.backend.urllib_request, 'urlopen') as urlopen, \
             patch.object(self.backend.subprocess, 'run', return_value=SimpleNamespace(stdout='active')):
            response = urlopen.return_value.__enter__.return_value
            response.read.return_value = b'{"status":"ok","version":"41"}'
            response.status = 200
            text = self.backend.admin_deploy_check_text()
        self.assertIn('успешно', text)
        self.assertIn('41', text)
        self.assertNotIn(self.backend.BOT_TOKEN, text)

if __name__ == '__main__':
    unittest.main()
