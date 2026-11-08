import hashlib
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import test_archives_http as fixtures
from app.services.archive_queue import discover, claim
from app.services.archive_worker import ArchiveWorker
from app.services.archive_storage import ArchiveError, save_stream
from app.database import apply_database_migrations

# Reuse setup only; do not duplicate the HTTP tests through inheritance.
import unittest


class ArchiveWorkerTests(unittest.TestCase):
    setUp = fixtures.ArchivesHttpTests.setUp
    restore_overrides = staticmethod(fixtures.ArchivesHttpTests.restore_overrides)
    control = fixtures.ArchivesHttpTests.control

    @contextmanager
    def connection(self):
        with self.db:
            yield self.db

    def fake_download(self, job, settings, check):
        check()
        return save_stream(settings.attachments_dir, job['account_id'], [b'fake-original-bytes'], 'image/png', check), []

    def prepare(self):
        self.db.execute("UPDATE attachments SET source_url='https://fixture.invalid/a.png' WHERE message_id=1")
        self.db.commit()
        self.control()
        return ArchiveWorker(self.connection, lambda: self.settings, self.fake_download)

    def release_delay(self):
        self.db.execute('UPDATE archive_runtime SET next_at=0')
        self.db.commit()

    def test_discovery_covers_history_new_commits_links_and_ignores_deleted_and_other_groups(self):
        worker = self.prepare()
        self.db.execute("UPDATE messages SET content_text='参考 https://weibo.com/123/Abc' WHERE id=1")
        self.db.commit()
        self.assertTrue(worker.run_once())
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM archive_jobs').fetchone()[0], 2)
        self.db.execute("INSERT INTO messages (id,account_id,group_id,user_id,sent_at,message_type,content_text) VALUES (10,1,11,1,'2026-09-13','text','https://fixture.invalid/new.pdf')")
        self.db.commit()
        self.release_delay()
        worker.run_once()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM archive_jobs').fetchone()[0], 3)
        discover(self.db)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM archive_jobs').fetchone()[0], 3)

    def test_original_bytes_stored_and_open_offline_without_cookie_or_network(self):
        worker = self.prepare()
        worker.run_once()
        job = self.db.execute('SELECT * FROM archive_jobs').fetchone()
        self.assertEqual(job['status'], 'stored')
        self.assertEqual((self.settings.attachments_dir / job['local_path']).read_bytes(), b'fake-original-bytes')
        self.assertEqual(job['content_hash'], hashlib.sha256(b'fake-original-bytes').hexdigest())
        with patch('app.services.attachment_images.fetch_attachment_image', side_effect=AssertionError('network forbidden')):
            response = self.client.get('/api/archives/1/content?account_id=1&group_id=11')
            self.assertEqual(response.content, b'fake-original-bytes')
            self.assertEqual(self.client.get('/api/attachments/1/image?account_id=1&group_id=11').content, b'fake-original-bytes')
        for params in ('account_id=2&group_id=11', 'account_id=1&group_id=12', 'account_id=1&group_id=11&deleted_only=true'):
            self.assertEqual(self.client.get('/api/archives/1/content?' + params).status_code, 404)
        self.db.execute('UPDATE messages SET is_deleted=1 WHERE id=1')
        self.db.commit()
        self.assertEqual(self.client.get('/api/archives/1/content?account_id=1&group_id=11').status_code, 404)
        self.assertEqual(self.client.get('/api/archives/1/content?account_id=1&group_id=11&deleted_only=true').content, b'fake-original-bytes')

    def test_pause_during_download_does_not_publish_partial_and_resume_keeps_finished(self):
        worker = self.prepare()
        def paused(job, settings, check):
            self.control('pause')
            return self.fake_download(job, settings, check)
        worker.downloader = paused
        worker.run_once()
        self.assertEqual(self.db.execute('SELECT status FROM archive_jobs').fetchone()[0], 'queued')
        self.assertEqual(list(self.settings.attachments_dir.rglob('.part-*')), [])
        worker.downloader = self.fake_download
        self.control('resume')
        self.release_delay()
        worker.run_once()
        self.assertEqual(self.db.execute('SELECT status FROM archive_jobs').fetchone()[0], 'stored')
        self.release_delay()
        self.assertFalse(worker.run_once())

    def test_login_failure_pauses_account_and_manual_resume_retries(self):
        worker = self.prepare()
        def denied(*args):
            raise ArchiveError('登录失效', blocked=True)
        worker.downloader = denied
        worker.run_once()
        self.assertTrue(self.control('pause').json()['paused'])
        self.assertEqual(self.db.execute('SELECT status FROM archive_jobs').fetchone()[0], 'blocked')
        self.assertEqual(self.control('resume').status_code, 200)
        worker.downloader = self.fake_download
        self.release_delay()
        worker.run_once()
        self.assertEqual(self.db.execute('SELECT status FROM archive_jobs').fetchone()[0], 'stored')

    def test_network_retry_is_bounded_and_lease_blocks_other_workers(self):
        worker = self.prepare()
        def failed(*args):
            raise ArchiveError('暂时断网', retryable=True)
        worker.downloader = failed
        for _ in range(3):
            self.release_delay()
            self.db.execute('UPDATE archive_jobs SET retry_at=0')
            self.db.commit()
            worker.run_once()
        self.assertEqual(self.db.execute('SELECT status,attempts FROM archive_jobs').fetchone()[:], ('failed', 3))
        self.control('resume')
        self.db.execute('UPDATE archive_runtime SET owner=NULL,expires_at=0,next_at=0')
        self.assertIsNotNone(claim(self.db, 'owner-a', now=100))
        self.assertIsNone(claim(self.db, 'owner-b', now=101))
        self.assertIsNotNone(claim(self.db, 'owner-b', now=200))

    def test_files_deduplicate_per_account_and_survive_metadata_deletion(self):
        worker = self.prepare()
        self.db.execute("INSERT INTO attachments(message_id,attachment_type,source_url) VALUES(1,'image','https://fixture.invalid/b.png')")
        self.db.commit()
        worker.run_once()
        self.release_delay()
        worker.run_once()
        paths = [row[0] for row in self.db.execute('SELECT local_path FROM archive_jobs')]
        self.assertEqual(len(set(paths)), 1)
        self.assertEqual(len([p for p in self.settings.attachments_dir.rglob('*') if p.is_file()]), 1)
        other = save_stream(self.settings.attachments_dir, 2, [b'fake-original-bytes'], 'image/png')
        self.assertNotEqual(other['local_path'], paths[0])
        self.db.execute('DELETE FROM archive_jobs')
        self.assertTrue((self.settings.attachments_dir / paths[0]).exists())

    def test_migration_is_idempotent_and_discovery_rolls_back_with_transaction(self):
        self.prepare()
        apply_database_migrations(self.db)
        self.db.commit()
        self.db.execute('SAVEPOINT isolation')
        discover(self.db)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM archive_jobs').fetchone()[0], 1)
        self.db.execute('ROLLBACK TO isolation')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM archive_jobs').fetchone()[0], 0)
        self.db.execute('RELEASE isolation')

    def test_deleted_during_final_publish_is_not_marked_stored(self):
        worker = self.prepare()
        original = worker.check
        calls = []
        def check(job):
            original(job)
            calls.append(1)
            if len(calls) == 3:
                self.db.execute('UPDATE messages SET is_deleted=1 WHERE id=1')
                self.db.commit()
        worker.check = check
        worker.run_once()
        self.assertNotEqual(self.db.execute('SELECT status FROM archive_jobs').fetchone()[0], 'stored')

    def test_snapshot_reports_pending_shared_resource_until_media_is_stored(self):
        worker = self.prepare()
        self.db.execute("UPDATE messages SET content_text='https://weibo.com/123/Abc' WHERE id=1")
        self.db.commit()
        def article(job, settings, check):
            result, _ = self.fake_download(job, settings, check)
            return result, [{'url': 'https://fixture.invalid/a.png', 'kind': 'image'}]
        # Existing root image is shared by the article; no duplicated download job.
        discover(self.db)
        self.db.execute("UPDATE archive_jobs SET retry_at=9999999999 WHERE kind='image'")
        self.db.commit()
        worker.downloader = article
        worker.run_once()
        data = self.client.get('/api/messages/1').json()
        link = next(item for item in data['archives'] if item['kind'] == 'link')
        self.assertEqual(link.get('pending_resources'), 1)
        worker.downloader = self.fake_download
        self.db.execute('UPDATE archive_jobs SET retry_at=0')
        self.release_delay()
        worker.run_once()
        link = next(item for item in self.client.get('/api/messages/1').json()['archives'] if item['kind'] == 'link')
        self.assertEqual(link['pending_resources'], 0)

    def test_start_again_resumes_blocked_items_instead_of_leaving_them_stuck(self):
        worker = self.prepare()
        def denied(*args):
            raise ArchiveError('登录失效', blocked=True)
        worker.downloader = denied
        worker.run_once()
        self.control('start')
        self.assertEqual(self.db.execute('SELECT status FROM archive_jobs').fetchone()[0], 'queued')

    def test_inaccessible_article_is_skipped_and_queue_continues_without_retry_on_resume(self):
        from app.services.archive_download import download
        worker = self.prepare()
        self.db.execute("UPDATE attachments SET attachment_type='link',source_url='https://weibo.com/123/Abc' WHERE message_id=1")
        self.db.execute("INSERT INTO attachments(message_id,attachment_type,source_url) VALUES(1,'image','https://fixture.invalid/next.png')")
        self.db.commit()
        worker.downloader = download
        with patch('app.services.archive_pages.get_json', return_value={'ok': 0, 'msg': '暂无查看权限'}):
            worker.run_once()
        row = self.db.execute("SELECT * FROM archive_jobs WHERE kind='link'").fetchone()
        self.assertEqual(row['status'], 'skipped')
        self.assertIsNone(row['local_path'])
        self.assertEqual(self.db.execute('SELECT paused FROM archive_groups').fetchone()[0], 0)
        self.assertEqual(self.client.get('/api/archives/status?account_id=1&group_id=11').json()['counts']['skipped'], 1)
        self.assertEqual(self.client.get(f"/api/archives/{row['id']}/content?account_id=1&group_id=11").status_code, 409)
        worker.downloader = self.fake_download
        self.release_delay()
        self.assertTrue(worker.run_once())
        self.assertEqual(self.db.execute("SELECT status FROM archive_jobs WHERE kind='image'").fetchone()[0], 'stored')
        self.control('resume')
        self.control('start')
        self.assertEqual(self.db.execute("SELECT status,attempts FROM archive_jobs WHERE kind='link'").fetchone()[:], ('skipped', 1))

    def test_share_link_only_in_saved_payload_is_discovered(self):
        import json
        worker = self.prepare()
        payload = {'url_objects': [{'url_ori': 'https://weibo.com/123/Abc'}]}
        self.db.execute('UPDATE messages SET raw_payload=? WHERE id=1', (json.dumps(payload),))
        self.db.commit()
        worker.run_once()
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM archive_jobs WHERE kind='link'").fetchone()[0], 1)
