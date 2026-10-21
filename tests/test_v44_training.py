"""V44 training manual session integration: data drives real reports and search.
No production DB, Telegram or external network is accessed.
"""
import unittest
from fastapi.testclient import TestClient
from test_v37 import V37AuthRaceTests


class ManualWorkoutJourney(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        V37AuthRaceTests.setUpClass.__func__(cls)

    setUp = V37AuthRaceTests.setUp
    tearDown = V37AuthRaceTests.tearDown
    headers = V37AuthRaceTests.headers

    def test_manual_duration_add_edit_delete_recalculates_reports(self):
        with TestClient(self.backend.app, raise_server_exceptions=False) as client:
            auth = self.headers(99660011)
            other = self.headers(99660012)
            key = '2026-10-21'
            session = {
                'id': 'manual_' + key, 'source': 'manual', 'status': 'completed',
                'dateKey': key, 'date': '2026-10-21T00:00:00+00:00',
                'started': '2026-10-21T00:00:00+00:00', 'ended': '2026-10-21T01:30:00+00:00',
                'updatedAt': '2026-10-22T00:00:00Z', 'duration': 5400,
                'workout': 'Плавание', 'exercises': []
            }
            save = client.put('/api/training/state', headers=auth, json={
                'state': {'history': [session], 'attendance': {key: 'done'},
                          'sync': {'tombstones': {'workouts': {}}}}
            })
            self.assertEqual(save.status_code, 200, save.text)
            report = client.get('/api/reports?period=daily&date=' + key, headers=auth)
            self.assertEqual(report.status_code, 200, report.text)
            self.assertEqual(report.json()['report']['training_minutes'], 90)
            self.assertEqual(report.json()['report']['workouts'], 1)
            weekly = client.get('/api/reports?period=weekly&date=' + key, headers=auth)
            self.assertEqual(weekly.json()['report']['training_minutes'], 90)
            search = client.get('/api/search?q=Плавание', headers=auth)
            self.assertTrue(any(item.get('kind') == 'training' for item in search.json()['results']))
            self.assertEqual(client.get('/api/search?q=Плавание', headers=other).json()['results'], [])
            # Editing is an upsert of the same history ID, not an extra session.
            session['duration'] = 7200
            session['updatedAt'] = '2026-10-22T00:30:00Z'
            changed = client.put('/api/training/state', headers=auth, json={'state': {'history': [session], 'attendance': {key: 'done'}}})
            self.assertEqual(changed.status_code, 200, changed.text)
            report = client.get('/api/reports?period=daily&date=' + key, headers=auth).json()['report']
            self.assertEqual((report['workouts'], report['training_minutes']), (1, 120))
            # Tombstone remains stored server-side, preventing old offline copies
            # from restoring a deleted manual record on the client.
            removed = client.put('/api/training/state', headers=auth, json={'state': {'history': [], 'attendance': {}, 'sync': {'tombstones': {'workouts': {'manual_' + key: '2026-10-22T01:00:00Z'}}}}})
            self.assertEqual(removed.status_code, 200, removed.text)
            state = client.get('/api/training/state', headers=auth).json()['state']
            self.assertEqual(state['history'], [])
            self.assertIn('manual_' + key, state['sync']['tombstones']['workouts'])
            report = client.get('/api/reports?period=daily&date=' + key, headers=auth).json()['report']
            self.assertEqual((report['workouts'], report['training_minutes']), (0, 0))


if __name__ == '__main__':
    unittest.main()
