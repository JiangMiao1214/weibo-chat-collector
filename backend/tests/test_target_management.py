"""Management uses only fixture databases, fake auth paths and no workers."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import test_messages_http as fixtures
from app.main import app
from app.settings import get_settings


class TargetManagementTests(unittest.TestCase):
    restore_overrides = staticmethod(fixtures.MessagesHttpTests.restore_overrides)

    def setUp(self):
        fixtures.MessagesHttpTests.setUp(self)
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.auth = Path(self.temp.name)
        app.dependency_overrides[get_settings] = lambda: SimpleNamespace(weibo_api_auth_dir=self.auth)
        self.addCleanup(patch.stopall)
        self.manager = patch('app.services.browser_login_manager.get_browser_login_manager').start().return_value
        self.manager.get_active_session.return_value = None
        # Legacy multi-group account: preserve already-bound history under the new limit.
        self.db.execute("UPDATE chat_groups SET source_group_id='123' WHERE id=11")
        self.db.commit()

    def account(self, **extra):
        return self.client.patch('/api/management/accounts/1', json={
            'display_name': '更新账号', 'notes': '仅本地备注', 'is_active': True, **extra})

    def group(self, **extra):
        return self.client.patch('/api/management/groups/11', json={
            'account_id': 1, 'name': '更新群名', 'description': '备注',
            'source_group_id': '123', 'is_active': True, **extra})

    def test_summary_counts_are_scoped_and_do_not_read_cookie_values(self):
        r = self.client.get('/api/management')
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        first = next(a for a in data['accounts'] if a['id'] == 1)
        group = next(g for g in data['groups'] if g['id'] == 11)
        self.assertEqual(first['message_count'], 2)
        self.assertEqual(group['message_count'], 1)
        self.assertEqual(group['attachment_count'], 2)
        self.assertEqual(group['first_message_at'], '2026-08-23 10:00:00')
        self.assertFalse(first['cookie_profile']['exists'])
        self.assertNotIn('raw_payload', r.text)

    def test_create_account_and_only_one_group_without_remote_binding(self):
        r = self.client.post('/api/management/accounts', json={'display_name': '新账号', 'notes': '备注'})
        self.assertEqual(r.status_code, 201, r.text)
        aid = r.json()['id']
        for name, expected in (('群一', 201), ('群二', 409)):
            r = self.client.post('/api/management/groups', json={'account_id': aid, 'name': name})
            self.assertEqual(r.status_code, expected, r.text)
            if expected == 201:
                self.assertIsNone(r.json()['source_group_id'])
        self.assertEqual(self.client.post('/api/management/accounts', json={'display_name': '  '}).status_code, 422)

    def test_edit_and_identity_guard_preserve_history(self):
        self.assertEqual(self.account().status_code, 200)
        self.assertEqual(self.group().status_code, 200)
        self.assertEqual(self.group(source_group_id='456').status_code, 409)
        self.assertEqual(self.group(source_group_id=None).status_code, 409)
        self.assertEqual(self.group(account_id=2).status_code, 409)
        self.assertEqual(self.db.execute('SELECT account_id,source_group_id FROM chat_groups WHERE id=11').fetchone()[:], (1, '123'))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0], 5)
        self.assertEqual(self.group(source_group_id='abc').status_code, 422)

    def test_duplicate_binding_is_scoped_to_account(self):
        self.assertEqual(self.group().status_code, 200)
        empty = self.client.post('/api/management/accounts', json={'display_name': '新账号'}).json()['id']
        for aid, expected in ((1, 409), (empty, 201)):
            r = self.client.post('/api/management/groups', json={'account_id': aid, 'name': '新群', 'source_group_id': '123'})
            self.assertEqual(r.status_code, expected, r.text)

    def test_disabling_stops_queued_jobs_pauses_archive_without_deleting_history(self):
        self.db.execute("INSERT INTO collection_jobs(account_id,group_id,range_start,range_end,status,collector_type) VALUES(1,11,'a','b','queued','weibo_api_v2')")
        self.db.execute('INSERT INTO archive_groups(account_id,group_id) VALUES(1,11)')
        self.db.commit()
        r = self.account(is_active=False)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.db.execute('SELECT status FROM collection_jobs').fetchone()[0], 'stopped')
        self.assertEqual(self.db.execute('SELECT paused FROM archive_groups').fetchone()[0], 1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0], 5)
        self.assertEqual(self.client.post('/api/archives/control',json={'account_id':1,'group_id':11,'action':'resume'}).status_code,409)
        self.assertEqual(self.account().status_code,200)
        self.assertEqual(self.db.execute('SELECT paused FROM archive_groups').fetchone()[0],1)

    def test_running_collection_requires_safe_stop_before_disable(self):
        self.db.execute("INSERT INTO collection_jobs(account_id,group_id,range_start,range_end,status) VALUES(1,11,'a','b','running')")
        self.db.commit()
        self.assertEqual(self.account(is_active=False).status_code,409)
        self.assertEqual(self.db.execute('SELECT is_active FROM weibo_accounts WHERE id=1').fetchone()[0],1)

    def test_remove_credentials_requires_confirmation_and_leaves_other_account_and_history(self):
        for aid in (1,2): (self.auth/f'account-{aid}.cookies.json').write_text('[]')
        url='/api/management/accounts/1/credentials'
        self.assertEqual(self.client.delete(url).status_code,422)
        r=self.client.delete(url+'?confirmed=true')
        self.assertEqual(r.status_code,200,r.text)
        self.assertFalse((self.auth/'account-1.cookies.json').exists())
        self.assertTrue((self.auth/'account-2.cookies.json').exists())
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],5)

    def test_browser_check_precedes_database_write_lock_to_avoid_lock_inversion(self):
        def check():
            self.assertFalse(self.db.in_transaction, 'browser lock must be checked before database write lock')
            return None
        self.manager.get_active_session.side_effect = check
        self.assertEqual(self.account(is_active=False).status_code,200)

    def test_active_browser_blocks_disable_and_credential_removal(self):
        self.manager.get_active_session.return_value={'account_id':1}
        self.assertEqual(self.account(is_active=False).status_code,409)
        self.assertEqual(self.client.delete('/api/management/accounts/1/credentials?confirmed=true').status_code,409)

    def test_group_disable_preserves_other_groups_and_paused_archives(self):
        self.db.executemany('INSERT INTO archive_groups(account_id,group_id) VALUES(1,?)',[(11,),(12,)])
        self.db.commit()
        self.assertEqual(self.group(is_active=False).status_code,200)
        self.assertEqual(dict(self.db.execute('SELECT group_id,paused FROM archive_groups')),{11:1,12:0})
        self.assertEqual(self.db.execute('SELECT is_active FROM weibo_accounts WHERE id=1').fetchone()[0],1)

    def test_disabled_target_blocks_old_task_confirm_resume_and_archive_claim(self):
        from app.services.archive_queue import claim_batch
        self.db.execute('UPDATE weibo_accounts SET is_active=0 WHERE id=1')
        self.db.execute('INSERT INTO archive_groups(account_id,group_id) VALUES(1,11)')
        self.db.execute("INSERT INTO archive_jobs(message_id,source_url,kind) VALUES(1,'https://fixture.invalid/image','image')")
        self.db.execute("INSERT INTO collection_jobs(id,account_id,group_id,range_start,range_end,status,collector_type) VALUES(100,1,11,'a','b','stopped','weibo_api_v2')")
        self.db.commit()
        self.assertEqual(claim_batch(self.db,'test-inactive',now=100),[])
        self.db.commit()
        self.assertEqual(self.client.post('/api/collection-jobs/100/resume').status_code,409)
        self.db.execute("UPDATE collection_jobs SET status='awaiting_confirmation' WHERE id=100")
        self.db.commit()
        with patch('app.api.collection_jobs.CookieProfileStore') as store:
            store.return_value.cookie_header.return_value='SUB=fake'
            self.assertEqual(self.client.post('/api/collection-jobs/100/confirm').status_code,409)


if __name__ == '__main__': unittest.main()
