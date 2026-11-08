import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
import test_quote_context_http as fixtures
from app.main import app
from app.settings import Settings, get_settings


class OcrTests(unittest.TestCase):
    insert=fixtures.QuoteContextHttpTests.insert

    def setUp(self):
        fixtures.QuoteContextHttpTests.setUp(self)
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.root=Path(folder.name)
        self.settings=Settings(attachments_dir=self.root,database_path=self.root/'test.sqlite3')
        app.dependency_overrides[get_settings]=lambda:self.settings

    def test_ocr_defaults_off_and_scope_is_required(self):
        r=self.client.get('/api/ocr/status?account_id=1&group_id=11')
        self.assertEqual(r.status_code,200,r.text)
        self.assertFalse(r.json()['enabled'])
        self.assertEqual(self.client.get('/api/ocr/status?account_id=2&group_id=11').status_code,404)

    def test_empty_search_stays_separate_from_message_text(self):
        r=self.client.get('/api/ocr/search?account_id=1&group_id=11&q=完整原文')
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['total'],0)
        self.assertEqual(self.client.get('/api/ocr/search?q=').status_code,422)

    def archived(self, message=1, account=1, data=b'isolated-image'):
        from app.services.archive_storage import save_stream
        file=save_stream(self.root,account,[data],'image/png')
        return self.db.execute("""INSERT INTO archive_jobs(message_id,source_url,kind,status,local_path,content_hash,file_size)
            VALUES (?,?,'image','stored',?,?,?)""",(message,f'https://fixture.invalid/{message}',file['local_path'],file['content_hash'],file['file_size'])).lastrowid

    def worker(self,recognizer):
        from contextlib import contextmanager
        from app.services.ocr_worker import OcrWorker
        @contextmanager
        def connection():
            with self.db:
                yield self.db
        return OcrWorker(connection,lambda:self.settings,recognizer)

    def enable(self,account=1,group=11,action='start'):
        from app.services.ocr_queue import control
        control(self.db,account,group,action)
        self.db.commit()

    def test_history_incremental_reuse_search_and_deletion_are_scoped(self):
        from unittest.mock import Mock
        first=self.archived()
        recognize=Mock(return_value='施工图纸 节点 A 混凝土 C30')
        worker=self.worker(recognize)
        self.assertFalse(worker.run_once())
        self.enable()
        self.assertTrue(worker.run_once())
        self.archived(10)
        self.assertTrue(worker.run_once())
        self.assertEqual(recognize.call_count,1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM ocr_results').fetchone()[0],1)
        result=self.client.get('/api/ocr/search?account_id=1&group_id=11&q=施工图纸').json()
        self.assertEqual(result['total'],2)
        self.assertTrue(all('/content?' in hit['image_url'] for hit in result['items']))
        self.assertEqual(self.client.get('/api/ocr/search?account_id=2&q=节点').json()['total'],0)
        self.db.execute('UPDATE messages SET is_deleted=1 WHERE id=1');self.db.commit()
        self.assertEqual(self.client.get('/api/ocr/search?account_id=1&group_id=11&q=节点').json()['total'],1)
        self.assertEqual(self.client.get('/api/ocr/search?account_id=1&group_id=11&q=节点&deleted_only=true').json()['total'],1)
        self.assertEqual(self.client.get('/api/ocr/search?account_id=1&group_id=12&q=节点').json()['total'],0)
        self.assertEqual(self.client.get('/api/ocr/search?account_id=1&q=施工图纸&limit=1').json()['has_more'],False)
        self.archived(21,2);self.enable(2,21);worker.run_once()
        self.assertEqual(recognize.call_count,2,'different accounts must not share OCR cache')

    def test_pause_delete_and_expired_owner_do_not_publish_results(self):
        from app.services.ocr_queue import claim,publish
        image=self.archived();self.enable()
        def pause(path,settings):
            self.enable(action='pause')
            return '不得发布'
        worker=self.worker(pause);worker.run_once()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM ocr_results').fetchone()[0],0)
        self.assertFalse(worker.run_once())
        self.enable(action='resume')
        job=claim(self.db,'old');self.db.commit()
        self.db.execute('UPDATE ocr_runtime SET expires_at=0');self.db.commit()
        new=claim(self.db,'new');self.db.commit()
        publish(self.db,'old',job,'旧进程');self.db.commit()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM ocr_results').fetchone()[0],0)
        self.db.execute('UPDATE messages SET is_deleted=1 WHERE id=1');self.db.commit()
        publish(self.db,'new',new,'已删除');self.db.commit()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM ocr_results').fetchone()[0],0)

    def test_corruption_fails_without_recognizer_and_retry_preserves_pause(self):
        from unittest.mock import Mock
        job=self.archived();self.enable()
        row=self.db.execute('SELECT local_path FROM archive_jobs').fetchone()
        (self.root/row[0]).write_bytes(b'corrupt')
        recognize=Mock(return_value='wrong')
        worker=self.worker(recognize);self.assertTrue(worker.run_once())
        self.assertEqual(recognize.call_count,0)
        data=self.client.get('/api/ocr/status?account_id=1&group_id=11').json()
        self.assertEqual(data['errors'][0]['error_code'],'hash_mismatch')
        self.enable(action='pause');self.enable(action='retry')
        self.assertFalse(worker.run_once())
        self.assertTrue(self.client.get('/api/ocr/status?account_id=1&group_id=11').json()['paused'])

    def test_engine_failure_never_uses_arbitrary_exception_as_log_text(self):
        self.archived();self.enable()
        def fail(path,settings):raise RuntimeError('Cookie: private-test-value')
        self.worker(fail).run_once()
        response=self.client.get('/api/ocr/status?account_id=1&group_id=11')
        self.assertNotIn('private-test-value',response.text)
        self.assertIn('local_error',response.text)

    def test_control_validates_local_engine_and_fts_treats_query_as_literal(self):
        with patch('app.api.ocr.prepare_engine',return_value='fake'):
            r=self.client.post('/api/ocr/control',json={'account_id':1,'group_id':11,'action':'start'})
            self.assertEqual(r.status_code,200,r.text)
        self.archived()
        self.worker(lambda path,settings:'图纸 \"ABC\" 100%').run_once()
        r=self.client.get('/api/ocr/search',params={'q':'"ABC"'})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['total'],1)
        self.assertEqual(self.client.get('/api/ocr/search',params={'q':'不存在 OR 图纸'}).json()['total'],0)

    def test_backup_restores_ocr_paused_and_keeps_searchable_text(self):
        import sqlite3
        from app.services.archive_maintenance import backup,restore
        self.archived();self.enable();self.worker(lambda path,settings:'中文基准样例').run_once()
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        bundle=Path(folder.name)/'copy.zip';backup(self.db,self.root,bundle)
        destination=self.root/'restored';restore(bundle,destination)
        with sqlite3.connect(destination/'database.sqlite3') as restored:
            self.assertEqual(restored.execute('SELECT paused FROM ocr_groups').fetchone()[0],1)
            self.assertEqual(restored.execute('SELECT text FROM ocr_results').fetchone()[0],'中文基准样例')

    def test_management_pause_keeps_ocr_paused_until_explicit_resume(self):
        from app.services.target_management import pause_target
        self.enable()
        pause_target(self.db,1,11)
        self.assertEqual(self.db.execute('SELECT paused FROM ocr_groups').fetchone()[0],1)
