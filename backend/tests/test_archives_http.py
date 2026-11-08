"""Offline archive contracts: isolated SQLite/files, no real Cookie or network."""
import tempfile
from pathlib import Path
import unittest

import test_messages_http as fixtures
from app.main import app
from app.settings import get_settings


class ArchivesHttpTests(unittest.TestCase):
    restore_overrides = staticmethod(fixtures.MessagesHttpTests.restore_overrides)

    def setUp(self):
        fixtures.MessagesHttpTests.setUp(self)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        from types import SimpleNamespace
        self.settings = SimpleNamespace(attachments_dir=Path(self.temp.name),
                                        weibo_api_auth_dir=Path(self.temp.name) / 'fake-auth')
        app.dependency_overrides[get_settings] = lambda: self.settings

    def control(self, action='start', **extra):
        return self.client.post('/api/archives/control', json={
            'account_id': 1, 'group_id': 11, 'action': action, **extra})

    def test_explicit_group_enable_and_pause_are_persistent_and_scope_checked(self):
        response = self.control()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['enabled'])
        self.assertFalse(response.json()['paused'])
        self.assertTrue(self.control('pause').json()['paused'])
        self.assertFalse(self.control('resume').json()['paused'])
        self.assertEqual(self.control(account_id=2).status_code, 404)
        self.assertEqual(self.control(group_id=0).status_code, 422)
        self.assertEqual(self.control('bad').status_code, 422)

    def test_status_is_read_only_and_does_not_start_downloads(self):
        response = self.client.get('/api/archives/status?account_id=1&group_id=11')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()['enabled'])
        self.assertEqual(response.json()['counts'], {})
        self.assertEqual(self.client.get('/api/archives/status?account_id=2&group_id=11').status_code, 404)

    def test_error_rows_expose_own_access_url_and_active_jobs_only_in_selected_group(self):
        for jid, mid, state in ((21, 1, 'failed'), (22, 1, 'downloading'), (23, 3, 'failed'), (24, 4, 'failed')):
            self.db.execute('INSERT INTO archive_jobs(id,message_id,source_url,kind,status,error) VALUES(?,?,?,\'link\',?,?)',
                            (jid, mid, f'https://fixture.invalid/{jid}', state, 'fake error' if state == 'failed' else None))
        self.db.commit()
        data = self.client.get('/api/archives/status?account_id=1&group_id=11').json()
        self.assertEqual([(row['id'], row.get('source_url')) for row in data['errors']], [(21, 'https://fixture.invalid/21')])
        self.assertEqual([(row['id'], row['source_url']) for row in data['active_jobs']], [(22, 'https://fixture.invalid/22')])

    def test_content_refuses_unknown_archive_and_requires_scope(self):
        self.assertEqual(self.client.get('/api/archives/999/content?account_id=1&group_id=11').status_code, 404)
        self.assertEqual(self.client.get('/api/archives/999/content').status_code, 422)

    def test_manual_skip_is_scoped_and_preserves_pause_and_saved_files(self):
        self.control()
        self.control('pause')
        self.db.execute("INSERT INTO archive_jobs(id,message_id,source_url,kind,status) VALUES(101,1,'https://weibo.com/123/Abc','link','blocked')")
        self.db.commit()
        url = '/api/archives/101/skip'
        for account, group in ((2, 11), (1, 12)):
            self.assertEqual(self.client.post(url, json={'account_id': account, 'group_id': group}).status_code, 404)
        result = self.client.post(url, json={'account_id': 1, 'group_id': 11})
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json()['paused'])
        self.assertEqual(result.json()['counts']['skipped'], 1)
        self.assertIn('用户确认', result.json()['errors'][0]['error'])
        self.control('resume')
        self.assertEqual(self.db.execute('SELECT status FROM archive_jobs WHERE id=101').fetchone()[0], 'skipped')
        for state in ('stored', 'downloading', 'queued'):
            self.db.execute('UPDATE archive_jobs SET status=? WHERE id=101', (state,))
            self.db.commit()
            self.assertEqual(self.client.post(url, json={'account_id': 1, 'group_id': 11}).status_code, 409)
            self.assertEqual(self.db.execute('SELECT status FROM archive_jobs WHERE id=101').fetchone()[0], state)
        self.db.execute("UPDATE archive_jobs SET status='blocked' WHERE id=101")
        self.db.execute('UPDATE messages SET is_deleted=1 WHERE id=1')
        self.db.commit()
        self.assertEqual(self.client.post(url, json={'account_id': 1, 'group_id': 11}).status_code, 404)

    def test_snapshot_local_resources_are_scoped_inert_and_report_pending_media(self):
        import json
        from app.services.archive_storage import save_stream
        from app.services.archive_download import SNAPSHOT_MIME
        snapshot = {'title': '<script>FAKE</script>', 'text': '<img src=x onerror=FAKE()>模拟正文',
                    'resources': [{'url': 'https://fixture.invalid/photo.png', 'kind': 'image'},
                                  {'url': 'https://fixture.invalid/v.mp4', 'kind': 'video'}]}
        result = save_stream(self.settings.attachments_dir, 1, [json.dumps(snapshot).encode()], SNAPSHOT_MIME)
        self.db.execute('''INSERT INTO archive_jobs(id,message_id,source_url,kind,status,local_path,mime_type)
            VALUES (1,1,'https://weibo.com/123/Abc','link','stored',?,?)''', (result['local_path'], SNAPSHOT_MIME))
        image = save_stream(self.settings.attachments_dir, 1, [b'fake-image'], 'image/png')
        self.db.execute('''INSERT INTO archive_jobs(id,message_id,source_url,kind,status,local_path,mime_type)
            VALUES(2,1,'https://fixture.invalid/photo.png','image','stored',?,'image/png')''', (image['local_path'],))
        self.db.commit()
        response = self.client.get('/api/archives/1/content?account_id=1&group_id=11')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('<script>', response.text)
        self.assertNotIn('<img src=x', response.text)
        self.assertIn('关联媒体尚未保存', response.text)
        self.assertIn('/api/archives/2/content?account_id=1&amp;group_id=11', response.text)
        self.assertNotIn('https://fixture.invalid', response.text)
        self.assertIn("default-src 'none'", response.headers['content-security-policy'])

    def test_archive_cannot_read_outside_root_or_another_accounts_files(self):
        from app.services.archive_storage import save_stream
        other = save_stream(self.settings.attachments_dir, 2, [b'other-account'], 'image/png')
        self.db.execute('''INSERT INTO archive_jobs(id,message_id,source_url,kind,status,local_path,mime_type)
            VALUES(1,1,'https://fixture.invalid/x','image','stored',?,'image/png')''', (other['local_path'],))
        self.db.commit()
        self.assertEqual(self.client.get('/api/archives/1/content?account_id=1&group_id=11').status_code, 404)
        self.db.execute("UPDATE archive_jobs SET local_path='../outside' WHERE id=1")
        self.db.commit()
        self.assertEqual(self.client.get('/api/archives/1/content?account_id=1&group_id=11').status_code, 404)
