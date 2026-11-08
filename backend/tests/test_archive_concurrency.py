"""Real thread scheduling against isolated SQLite and event-controlled fake downloads."""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import tempfile
import threading
import unittest

import test_archives_http as fixtures
from app.database import apply_database_migrations
from app.services.archive_queue import status, claim_batch
from app.services.archive_storage import save_stream, ArchiveError
from app.services.archive_worker import ArchiveWorker


class ArchiveConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'isolated.sqlite'
        self.settings = SimpleNamespace(attachments_dir=Path(self.temp.name) / 'files')
        with self.connection() as c:
            apply_database_migrations(c)
            c.execute("INSERT INTO weibo_accounts(id,display_name) VALUES(1,'fake')")
            c.execute("INSERT INTO chat_groups(id,account_id,name) VALUES(11,1,'fake')")
            c.execute("INSERT INTO chat_users(id,display_name) VALUES(1,'fake')")
            c.execute('INSERT INTO archive_groups(account_id,group_id) VALUES(1,11)')
            for i in range(1, 7):
                c.execute("INSERT INTO messages(id,account_id,group_id,user_id,sent_at,message_type,content_text) VALUES(?,1,11,1,'2026-09-13','image','fake')", (i,))
                c.execute("INSERT INTO attachments(message_id,attachment_type,source_url) VALUES(?,'image',?)", (i, f'https://fixture.invalid/{i}.png'))
        self.release = threading.Event()
        self.three_started = threading.Event()
        self.lock = threading.Lock()
        self.started = []
        self.worker = ArchiveWorker(self.connection, lambda: self.settings, self.download)
        self.addCleanup(self.cleanup_worker)

    @contextmanager
    def connection(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def cleanup_worker(self):
        self.worker.stop_event.set()
        self.release.set()
        self.worker.stop()

    def download(self, job, settings, check):
        with self.lock:
            self.started.append(job['id'])
            if len(self.started) == 3:
                self.three_started.set()
        if not self.release.wait(5):
            raise AssertionError('test did not release fake downloads')
        check()
        return save_stream(settings.attachments_dir, job['account_id'], [b'fake-original'], 'image/png', check), []

    def test_three_downloads_are_visible_and_second_worker_cannot_take_a_fourth(self):
        self.worker.start()
        self.assertTrue(self.three_started.wait(3), 'expected three downloads to start concurrently')
        with self.connection() as c:
            snapshot = status(c, 1, 11)
        self.assertEqual(snapshot['counts']['downloading'], 3)
        self.assertEqual(len(snapshot['active_jobs']), 3)
        self.assertEqual(snapshot['counts']['queued'], 3)
        other = ArchiveWorker(self.connection, lambda: self.settings, self.download)
        self.assertFalse(other.run_once())
        self.assertEqual(len(set(self.started)), 3)
        # Finish just this batch; do not wait for another background poll.
        self.worker.stop_event.set()
        self.release.set()
        self.worker.stop()

    def test_pause_during_three_downloads_prevents_publish_and_keeps_queue_resumable(self):
        self.worker.start()
        self.assertTrue(self.three_started.wait(3), 'expected three downloads to start concurrently')
        with self.connection() as c:
            c.execute("UPDATE archive_groups SET paused=1,reason='manual' WHERE account_id=1")
        self.release.set()
        self.worker.stop()
        with self.connection() as c:
            self.assertEqual(dict(c.execute('SELECT status,COUNT(*) FROM archive_jobs GROUP BY status')), {'queued': 6})
        self.assertEqual(list(self.settings.attachments_dir.rglob('*')), [])

    def test_successful_batch_keeps_three_records_one_file_and_cooldown(self):
        self.worker.thread = threading.Thread(target=lambda: self.worker.run_once(limit=3), daemon=True)
        self.worker.thread.start()
        self.assertTrue(self.three_started.wait(3))
        self.release.set()
        self.worker.thread.join(5)
        self.assertFalse(self.worker.thread.is_alive())
        with self.connection() as c:
            self.assertEqual(dict(c.execute('SELECT status,COUNT(*) FROM archive_jobs GROUP BY status')), {'queued': 3, 'stored': 3})
            paths = {r[0] for r in c.execute("SELECT local_path FROM archive_jobs WHERE status='stored'")}
            self.assertEqual(len(paths), 1)
        self.assertEqual(len([p for p in self.settings.attachments_dir.rglob('*') if p.is_file()]), 1)
        self.assertFalse(self.worker.run_once(limit=3))

    def test_account_denial_stops_peer_downloads_without_marking_them_failed(self):
        denied = threading.Event()
        original_fail = self.worker.fail
        def fail(c, job, error):
            original_fail(c, job, error)
            if isinstance(error, ArchiveError) and error.blocked:
                denied.set()
        def download(job, settings, check):
            with self.lock:
                self.started.append(job['id'])
                if len(self.started) == 3:
                    self.three_started.set()
            if not self.three_started.wait(3):
                raise AssertionError('missing peers')
            if job['message_id'] == 1:
                raise ArchiveError('fake HTTP 429', blocked=True)
            if not denied.wait(3):
                raise AssertionError('missing account pause')
            check()
            raise AssertionError('peer must stop after account denial')
        self.worker.downloader = download
        self.worker.fail = fail
        self.assertTrue(self.worker.run_once(limit=3))
        with self.connection() as c:
            self.assertEqual(dict(c.execute('SELECT status,COUNT(*) FROM archive_jobs GROUP BY status')), {'queued': 5, 'blocked': 1})
            self.assertEqual(c.execute('SELECT paused FROM archive_groups').fetchone()[0], 1)
        self.assertFalse(self.settings.attachments_dir.exists())

    def test_expired_batch_recovers_without_allowing_old_owner_to_publish(self):
        import time
        with self.connection() as c:
            jobs = claim_batch(c, 'old', now=time.time() - 200)
        with self.connection() as c:
            recovered = claim_batch(c, 'new')
        self.assertEqual([job['id'] for job in recovered], [job['id'] for job in jobs])
        self.worker.owner = 'old'
        self.worker.process_job(jobs[0])
        with self.connection() as c:
            self.assertEqual(dict(c.execute('SELECT status,COUNT(*) FROM archive_jobs GROUP BY status')), {'queued': 3, 'downloading': 3})
            self.assertEqual(c.execute('SELECT owner FROM archive_runtime').fetchone()[0], 'new')
        self.assertFalse(self.started)
