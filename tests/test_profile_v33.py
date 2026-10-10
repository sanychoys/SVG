"""Run: cd TRC && python -m unittest discover -s tests -v"""
import tempfile
import unittest
from pathlib import Path
import finance_db
import product_db


class ProfileSecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.previous_db=finance_db.DB_PATH
        finance_db.DB_PATH=Path(self.tmp.name)/'db.sqlite'
        finance_db.init_db()
        product_db.init_product_db()
        self.a=finance_db.get_or_create_user({'id':9001,'first_name':'Alpha','username':'alpha_v33'})
        self.b=finance_db.get_or_create_user({'id':9002,'first_name':'Beta','username':'beta_v33'})

    def tearDown(self):
        finance_db.DB_PATH=self.previous_db
        self.tmp.cleanup()

    def test_sessions_are_user_scoped_and_tokens_are_hidden(self):
        current=finance_db.create_web_session(self.a)
        old=finance_db.create_web_session(self.a)
        other=finance_db.create_web_session(self.b)
        mine=finance_db.list_user_web_sessions(self.a,current['token'])
        self.assertEqual(len(mine),2)
        self.assertEqual(sum(bool(item['current']) for item in mine),1)
        for item in mine:
            self.assertNotIn('token',item)
            self.assertEqual(len(item['id']),20)
        # Attempt to revoke a different account's session must not work.
        foreign=finance_db.list_user_web_sessions(self.b)[0]['id']
        self.assertEqual(finance_db.revoke_user_web_sessions(self.a,'one',foreign,current['token']),0)
        self.assertIsNotNone(finance_db.get_user_by_web_session(other['token']))
        # Current browser may revoke only other sessions.
        self.assertEqual(finance_db.revoke_user_web_sessions(self.a,'others',current_token=current['token']),1)
        self.assertIsNone(finance_db.get_user_by_web_session(old['token']))
        self.assertIsNotNone(finance_db.get_user_by_web_session(current['token']))

    def test_single_session_revoke_and_validation(self):
        active=finance_db.create_web_session(self.a)
        stale=finance_db.create_web_session(self.a)
        current_id=[s['id'] for s in finance_db.list_user_web_sessions(self.a,active['token']) if s['current']][0]
        with self.assertRaises(ValueError):
            finance_db.revoke_user_web_sessions(self.a,'one',current_id,active['token'])
        with self.assertRaises(ValueError):
            finance_db.revoke_user_web_sessions(self.a,'all',current_token=active['token'])
        with self.assertRaises(ValueError):
            finance_db.revoke_user_web_sessions(self.a,'one','../../invalid',active['token'])
        single_id=[s['id'] for s in finance_db.list_user_web_sessions(self.a,active['token']) if not s['current']][0]
        self.assertEqual(finance_db.revoke_user_web_sessions(self.a,'one',single_id,active['token']),1)
        self.assertIsNone(finance_db.get_user_by_web_session(stale['token']))

    def test_privacy_default_is_private_and_persists(self):
        self.assertFalse(product_db.get_notification_preferences(self.a)['share_busy'])
        self.assertTrue(product_db.update_notification_preferences(self.a,{'share_busy':True})['share_busy'])
        self.assertTrue(product_db.get_notification_preferences(self.a)['share_busy'])
        self.assertFalse(product_db.get_notification_preferences(self.b)['share_busy'])
        with self.assertRaises(ValueError):
            product_db.update_notification_preferences(self.a,{'share_busy':'true'})


if __name__=='__main__':
    unittest.main()
