"""Backup/restore/cleanup only operate on disposable database and files."""
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile

import test_archives_http as fixtures
from app.services.archive_storage import save_stream


class ArchiveMaintenanceTests(unittest.TestCase):
    setUp = fixtures.ArchivesHttpTests.setUp
    restore_overrides = staticmethod(fixtures.ArchivesHttpTests.restore_overrides)

    def service(self):
        self.assertIsNotNone(importlib.util.find_spec('app.services.archive_maintenance'), 'backup and restore service is missing')
        return importlib.import_module('app.services.archive_maintenance')

    def test_backup_restore_preserves_db_and_bytes_and_pauses_restored_groups(self):
        service = self.service()
        result = save_stream(self.settings.attachments_dir, 1, [b'fixture-content'], 'image/png')
        self.db.execute("INSERT INTO archive_groups(account_id,group_id,paused) VALUES(1,11,1)")
        self.db.execute("INSERT INTO archive_jobs(message_id,source_url,kind,status,local_path,file_size,content_hash) VALUES(1,'https://fixture.invalid/x','image','stored',?,?,?)", (result['local_path'], result['file_size'], result['content_hash']))
        self.db.commit()
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp) / 'copy.zip'
            service.backup(self.db, self.settings.attachments_dir, bundle)
            service.verify_bundle(bundle)
            target = Path(temp) / 'restored'
            service.restore(bundle, target)
            self.assertEqual((target / 'attachments' / result['local_path']).read_bytes(), b'fixture-content')
            with sqlite3.connect(target / 'database.sqlite3') as db:
                self.assertEqual(db.execute('SELECT paused FROM archive_groups').fetchone()[0], 1)
            with self.assertRaisesRegex(ValueError, '存在'):
                service.restore(bundle, target)
            self.assertEqual((self.settings.attachments_dir / result['local_path']).read_bytes(), b'fixture-content')

    def test_bad_bundle_and_path_traversal_never_publish_restore(self):
        service = self.service()
        with tempfile.TemporaryDirectory() as temp:
            bundle, target = Path(temp) / 'bad.zip', Path(temp) / 'restored'
            with zipfile.ZipFile(bundle, 'w') as archive:
                archive.writestr('../escape', b'bad')
                archive.writestr('manifest.json', json.dumps({'version': 1, 'files': {}}))
            with self.assertRaises(ValueError):
                service.restore(bundle, target)
            self.assertFalse(target.exists())
            self.assertFalse((Path(temp) / 'escape').exists())

    def test_cleanup_preview_preserves_referenced_and_shared_files_requires_quiet_worker(self):
        service = self.service()
        used = save_stream(self.settings.attachments_dir, 1, [b'used'], 'image/png')
        orphan = save_stream(self.settings.attachments_dir, 1, [b'orphan'], 'image/png')
        self.db.execute("INSERT INTO archive_jobs(message_id,source_url,kind,status,local_path) VALUES(1,'https://fixture.invalid/u','image','stored',?)", (used['local_path'],))
        self.db.commit()
        preview = service.cleanup(self.db, self.settings.attachments_dir)
        self.assertEqual(preview['count'], 1)
        self.assertTrue((self.settings.attachments_dir / orphan['local_path']).exists())
        result = service.cleanup(self.db, self.settings.attachments_dir, confirm=preview['token'])
        self.assertEqual(result['removed'], 1)
        self.assertTrue((self.settings.attachments_dir / used['local_path']).exists())
        self.assertFalse((self.settings.attachments_dir / orphan['local_path']).exists())
        self.db.execute("INSERT INTO archive_groups(account_id,group_id,paused) VALUES(1,11,0)")
        self.db.commit()
        with self.assertRaisesRegex(ValueError, '暂停'):
            service.cleanup(self.db, self.settings.attachments_dir)

    def test_tampered_file_fails_hash_validation(self):
        service = self.service()
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp) / 'bad.zip'
            with zipfile.ZipFile(bundle, 'w') as archive:
                archive.writestr('database.sqlite3', b'bad')
                archive.writestr('manifest.json', json.dumps({'version': 1, 'files': {'database.sqlite3':
                    {'size': 3, 'sha256': hashlib.sha256(b'yes').hexdigest()}}}))
            with self.assertRaises(ValueError):
                service.verify_bundle(bundle)

    def test_http_backup_restore_and_cleanup_are_explicit_local_operations(self):
        with tempfile.TemporaryDirectory() as temp:
            self.settings.database_path = Path(temp) / 'source.sqlite3'
            response = self.client.post('/api/archive-maintenance/backup')
            self.assertEqual(response.status_code, 200, response.text)
            bundle = Path(response.json()['path'])
            self.assertTrue(bundle.is_file())
            restored = self.client.post('/api/archive-maintenance/restore', content=bundle.read_bytes(), headers={'Content-Type': 'application/zip'})
            self.assertEqual(restored.status_code, 200, restored.text)
            self.assertTrue((Path(restored.json()['destination']) / 'database.sqlite3').exists())
            self.assertEqual(self.client.post('/api/archive-maintenance/cleanup', json={}).status_code, 200)
            self.assertEqual(self.client.post('/api/archive-maintenance/cleanup', json={'token': 'stale'}).status_code, 409)

    def test_backup_file_database_is_consistent_and_rejects_missing_originals(self):
        service = self.service()
        from app.database import apply_database_migrations
        with tempfile.TemporaryDirectory() as temp:
            source_path = Path(temp) / 'source.sqlite3'
            with sqlite3.connect(source_path) as db:
                db.row_factory = sqlite3.Row
                db.executescript('\n'.join(self.db.iterdump()))
                db.commit()
                service.backup(db, self.settings.attachments_dir, Path(temp) / 'valid.zip')
                self.assertTrue((Path(temp) / 'valid.zip').exists())
            self.db.execute("INSERT INTO archive_jobs(message_id,source_url,kind,status,local_path) VALUES(1,'https://fixture.invalid/missing','image','stored','archives/1/no-file')")
            self.db.commit()
            with self.assertRaisesRegex(ValueError, '缺失'):
                service.backup(self.db, self.settings.attachments_dir, Path(temp) / 'invalid.zip')
            self.assertFalse((Path(temp) / 'invalid.zip').exists())

    def test_cleanup_removes_only_expired_resume_files_and_rejects_symlink_roots(self):
        import os
        import time
        service = self.service()
        resume = self.settings.attachments_dir / 'archives/1/.resume-99'
        resume.mkdir(parents=True)
        for name in ('body', 'meta.json'):
            (resume / name).write_bytes(b'old')
            os.utime(resume / name, (time.time()-90000, time.time()-90000))
        os.utime(resume, (time.time()-90000, time.time()-90000))
        preview = service.cleanup(self.db, self.settings.attachments_dir)
        self.assertEqual(preview['count'], 2)
        service.cleanup(self.db, self.settings.attachments_dir, confirm=preview['token'])
        self.assertFalse(resume.exists())
        archives = self.settings.attachments_dir / 'archives'
        archives.rename(self.settings.attachments_dir / 'original-archives')
        with tempfile.TemporaryDirectory() as outside:
            archives.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, '符号链接'):
                service.cleanup(self.db, self.settings.attachments_dir)
            archives.unlink()

    def test_restore_rejects_missing_archived_files_even_with_valid_manifest(self):
        service = self.service()
        from app.services.archive_bundles import write_bundle
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / 'db.sqlite3'
            self.db.execute("INSERT INTO archive_jobs(message_id,source_url,kind,status,local_path) VALUES(1,'https://fixture.invalid/missing','image','stored','archives/1/none')")
            self.db.commit()
            database.write_bytes(self.db.serialize())
            bundle = Path(temp) / 'backup.zip'
            write_bundle(database, self.settings.attachments_dir, bundle)
            with self.assertRaisesRegex(ValueError, '缺失'):
                service.restore(bundle, Path(temp) / 'restored')
            self.assertFalse((Path(temp) / 'restored').exists())
