"""Archive repairs and reuse use isolated fixtures; no real downloads."""
import unittest
from unittest.mock import patch, MagicMock
from types import SimpleNamespace

import test_archive_worker as fixtures
from app.services.archive_network import open_source
from app.services.archive_storage import ArchiveError


class ArchiveImprovementsTests(unittest.TestCase):
    setUp = fixtures.ArchiveWorkerTests.setUp
    restore_overrides = staticmethod(fixtures.ArchiveWorkerTests.restore_overrides)
    control = fixtures.ArchiveWorkerTests.control
    connection = fixtures.ArchiveWorkerTests.connection
    prepare = fixtures.ArchiveWorkerTests.prepare
    fake_download = fixtures.ArchiveWorkerTests.fake_download
    release_delay = fixtures.ArchiveWorkerTests.release_delay

    def test_same_resource_http_https_and_other_message_reuses_saved_file(self):
        worker = self.prepare()
        self.db.execute("UPDATE attachments SET source_url='http://wx1.sinaimg.cn/sample.png' WHERE message_id=1")
        self.db.execute("INSERT INTO attachments(message_id,attachment_type,source_url) VALUES(2,'image','https://wx1.sinaimg.cn/sample.png')")
        self.db.commit()
        self.db.execute("UPDATE messages SET group_id=11 WHERE id=2")
        self.db.commit()
        worker.downloader = MagicMock(side_effect=self.fake_download)
        worker.run_once()
        self.release_delay()
        worker.run_once()
        self.assertEqual(worker.downloader.call_count, 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM archive_jobs WHERE status='stored'").fetchone()[0], 2)

    def test_explicit_retry_of_skipped_job_is_scoped_and_keeps_account_paused(self):
        self.prepare()
        self.db.execute("INSERT INTO archive_jobs(id,message_id,source_url,kind,status,attempts) VALUES(81,1,'https://fixture.invalid/x','link','skipped',3)")
        self.db.commit()
        self.control('pause')
        url = '/api/archives/81/retry'
        self.assertEqual(self.client.post(url, json={'account_id': 2, 'group_id': 11}).status_code, 404)
        response = self.client.post(url, json={'account_id': 1, 'group_id': 11})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['paused'])
        self.assertEqual(self.db.execute('SELECT status,attempts FROM archive_jobs WHERE id=81').fetchone()[:], ('queued', 0))

    def test_integrity_check_and_repair_only_requeues_missing_or_corrupt_files(self):
        worker = self.prepare()
        worker.run_once()
        row = self.db.execute('SELECT * FROM archive_jobs').fetchone()
        path = self.settings.attachments_dir / row['local_path']
        path.write_bytes(b'corrupt')
        self.control('pause')
        response = self.client.post('/api/archives/repair', json={'account_id': 1, 'group_id': 11})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['repaired'], 1)
        self.assertEqual(self.db.execute('SELECT status FROM archive_jobs').fetchone()[0], 'queued')
        self.assertEqual(self.db.execute('SELECT paused FROM archive_groups').fetchone()[0], 1)
        self.assertEqual(self.db.execute('SELECT download_status FROM attachments WHERE id=1').fetchone()[0], 'pending')

    def test_redirect_failure_keeps_actual_failure_target(self):
        conn = MagicMock()
        conn.getresponse.return_value = SimpleNamespace(status=302, headers={'Location': 'https://other.invalid/article'})
        with patch('socket.getaddrinfo', return_value=[(0, 0, 0, '', ('198.18.0.60', 443))]), \
             patch('app.services.archive_network.PinnedHTTPS', return_value=conn):
            try:
                with open_source('https://t.cn/example', 1, 'fake-auth'):
                    self.fail('unsupported fake IP should fail')
            except ArchiveError as error:
                self.assertEqual(getattr(error, 'failure_url', None), 'https://other.invalid/article')

    def test_trust_failed_host_is_exact_scoped_and_does_not_resume(self):
        self.prepare()
        self.control('pause')
        self.db.execute("INSERT INTO archive_jobs(id,message_id,source_url,kind,status,error) VALUES(82,1,'https://t.cn/test','link','failed','DNS Fake-IP')")
        self.db.execute("INSERT INTO archive_failures(job_id,failure_url) VALUES(82,'https://other.invalid/article')")
        self.db.commit()
        response = self.client.post('/api/archives/82/trust-host', json={'account_id': 1, 'group_id': 11})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['paused'])
        self.assertEqual(self.db.execute('SELECT host FROM archive_https_hosts WHERE account_id=1').fetchone()[0], 'other.invalid')
        self.assertEqual(self.client.post('/api/archives/82/trust-host', json={'account_id': 2, 'group_id': 11}).status_code, 404)

    def test_dynamic_page_can_save_explicit_manual_text_as_inert_labeled_snapshot(self):
        self.prepare()
        self.control('pause')
        self.db.execute("INSERT INTO archive_jobs(id,message_id,source_url,kind,status) VALUES(88,1,'https://dynamic.invalid/page','link','failed')")
        self.db.commit()
        response = self.client.post('/api/archives/88/snapshot', json={'account_id': 1, 'group_id': 11,
            'text': '用户手动复制的完整正文。' * 5 + '<script>alert(1)</script>'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['paused'])
        saved = self.client.get('/api/archives/88/content?account_id=1&group_id=11')
        self.assertIn('手动正文快照', saved.text)
        self.assertIn('不含关联媒体', saved.text)
        self.assertNotIn('<script>', saved.text)
        self.assertEqual(self.client.post('/api/archives/88/snapshot', json={'account_id': 2, 'group_id': 11, 'text': '内容'*40}).status_code, 404)
