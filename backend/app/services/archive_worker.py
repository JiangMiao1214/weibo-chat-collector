"""One globally leased coordinator runs at most three independent archive downloads."""
from concurrent.futures import ThreadPoolExecutor
import logging
import threading
import time
from uuid import uuid4

from ..database import connection_context
from ..settings import get_settings
from .archive_queue import claim_batch
from .archive_publish import publish
from .archive_storage import ArchiveError
from .archive_outcomes import ArchiveSkipped
from .archive_download import download
from .archive_reuse import reusable
from .web_urls import safe_url
from .archive_policy import trust_hosts


class ArchivePaused(Exception):
    pass


class ArchiveWorker:
    def __init__(self, connection_factory=connection_context, settings_provider=get_settings, downloader=download):
        self.connection_factory = connection_factory
        self.settings_provider = settings_provider
        self.downloader = downloader
        self.owner = uuid4().hex
        self.stop_event = threading.Event()
        self.thread = None
        self.run_lock = threading.Lock()

    def start(self):
        if not self.thread or not self.thread.is_alive():
            self.owner = uuid4().hex
            self.stop_event.clear()
            self.thread = threading.Thread(target=self.loop, name='local-archive-worker', daemon=True)
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=35)

    def loop(self):
        while not self.stop_event.is_set():
            try:
                self.run_once(limit=3)
            except Exception:
                logging.getLogger(__name__).error('归档队列本地处理失败，将稍后检查队列')
            self.stop_event.wait(2)

    def check(self, job):
        if self.stop_event.is_set():
            raise ArchivePaused()
        with self.connection_factory() as connection:
            active = connection.execute('''SELECT 1 FROM archive_jobs j JOIN messages m ON m.id=j.message_id
                JOIN archive_groups g ON g.group_id=m.group_id WHERE j.id=? AND g.paused=0 AND m.is_deleted=0''', (job['id'],)).fetchone()
            owned = connection.execute('UPDATE archive_runtime SET expires_at=? WHERE owner=? AND expires_at>?',
                                       (time.time() + 90, self.owner, time.time())).rowcount
            if not active or not owned:
                raise ArchivePaused()

    def finish(self, connection, job, result, resources):
        publish(connection, job, result, resources)

    def fail(self, connection, job, error):
        if isinstance(error, ArchivePaused):
            state, reason, retry_at = 'queued', None, 0
        elif isinstance(error, ArchiveSkipped):
            state, reason, retry_at = 'skipped', str(error), 0
        elif isinstance(error, ArchiveError):
            state = 'blocked' if error.blocked else 'queued' if error.retryable and job['attempts'] < 2 else 'failed'
            reason, retry_at = str(error), time.time() + 60 * (job['attempts'] + 1)
            if error.blocked:
                connection.execute('UPDATE archive_groups SET paused=1,reason=? WHERE account_id=?', (reason, job['account_id']))
        else:
            state, reason, retry_at = 'failed', '本地处理失败，请检查磁盘或恢复归档', 0
        connection.execute('UPDATE archive_jobs SET status=?,error=?,retry_at=? WHERE id=?', (state, reason, retry_at, job['id']))
        target = safe_url(getattr(error, 'failure_url', None))
        if target:
            connection.execute('INSERT OR REPLACE INTO archive_failures(job_id,failure_url) VALUES(?,?)', (job['id'], target))

    def process_job(self, job):
        try:
            self.check(job)
            settings = self.settings_provider()
            with self.connection_factory() as connection:
                cached = reusable(connection, job, settings.attachments_dir)
                hosts = [row[0] for row in connection.execute('SELECT host FROM archive_https_hosts WHERE account_id=?', (job['account_id'],))]
            with trust_hosts(hosts):
                result, resources = cached or self.downloader(job, settings, lambda: self.check(job))
            self.check(job)
            with self.connection_factory() as connection:
                # This write locks out concurrent pause/delete before publishing the result.
                owned = connection.execute('UPDATE archive_runtime SET expires_at=? WHERE owner=? AND expires_at>?',
                                           (time.time() + 90, self.owner, time.time())).rowcount
                active = connection.execute('''SELECT 1 FROM archive_groups g JOIN messages m ON m.group_id=g.group_id
                    WHERE g.group_id=? AND g.account_id=? AND g.paused=0 AND m.id=? AND m.is_deleted=0''',
                    (job['group_id'], job['account_id'], job['message_id'])).fetchone()
                if not owned or not active:
                    raise ArchivePaused()
                self.finish(connection, job, result, resources)
        except Exception as error:
            with self.connection_factory() as connection:
                if connection.execute('SELECT 1 FROM archive_runtime WHERE owner=? AND expires_at>?', (self.owner, time.time())).fetchone():
                    self.fail(connection, job, error)

    def run_once(self, limit=1):
        if not self.run_lock.acquire(blocking=False):
            return False
        try:
            with self.connection_factory() as connection:
                jobs = claim_batch(connection, self.owner, limit=limit)
            if not jobs:
                return False
            if len(jobs) == 1:
                self.process_job(jobs[0])
            else:
                with ThreadPoolExecutor(max_workers=3, thread_name_prefix='archive-download') as pool:
                    futures = [pool.submit(self.process_job, job) for job in jobs]
                    for future in futures:
                        future.result()
            return True
        finally:
            try:
                with self.connection_factory() as connection:
                    connection.execute('UPDATE archive_runtime SET owner=NULL, expires_at=0,next_at=? WHERE owner=?',
                                       (time.time() + 5, self.owner))
            finally:
                self.run_lock.release()


_worker = ArchiveWorker()


def get_archive_worker():
    return _worker
