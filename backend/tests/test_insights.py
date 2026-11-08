import tempfile
import unittest
from pathlib import Path
import test_quote_context_http as fixtures
from app.main import app
from app.settings import get_settings, Settings
from app.services.archive_storage import save_stream


class InsightsTests(unittest.TestCase):
    insert = fixtures.QuoteContextHttpTests.insert

    def setUp(self):
        fixtures.QuoteContextHttpTests.setUp(self)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        app.dependency_overrides[get_settings] = lambda: Settings(attachments_dir=self.root)

    def archive(self, message=1, status='stored'):
        file = save_stream(self.root, 1, [b'fake-image'], 'image/png')
        row = self.db.execute('''INSERT INTO archive_jobs(message_id,source_url,kind,status,local_path,content_hash,file_size)
            VALUES (?,'https://fixture.invalid/photo?token=PRIVATE','image',?,?,?,?)''',
            (message,status,file['local_path'],file['content_hash'],file['file_size']))
        return row.lastrowid

    def test_dashboard_scope_trends_unique_real_storage_and_deleted_isolation(self):
        self.archive(1); self.archive(10)
        r=self.client.get('/api/insights/dashboard?account_id=1&group_id=11&period=day')
        self.assertEqual(r.status_code,200,r.text)
        data=r.json()
        self.assertEqual(data['message_count'],2)
        self.assertEqual(data['storage']['bytes'],10)
        self.assertEqual(data['storage']['files'],1)
        self.assertEqual(data['storage'].get('by_kind',{}).get('image'),{'files':1,'bytes':10})
        self.assertEqual(len(data['trend']),2)
        self.assertEqual(data['types'],[{'name':'text','count':2}])
        self.assertEqual(self.client.get('/api/insights/dashboard?account_id=2&group_id=11').status_code,404)
        deleted=self.client.get('/api/insights/dashboard?account_id=1&group_id=11&deleted_only=true').json()
        self.assertEqual(deleted['message_count'],1)
        self.assertEqual(deleted['storage']['bytes'],0)

    def test_log_transitions_are_durable_scoped_and_export_redacts_secrets(self):
        job=self.archive(status='queued')
        self.db.execute("UPDATE archive_jobs SET status='downloading' WHERE id=?",(job,))
        self.db.execute("UPDATE archive_jobs SET status='failed',error='Cookie: PRIVATE' WHERE id=?",(job,))
        self.db.execute("INSERT INTO archive_failures VALUES (?, 'https://fixture.invalid/deny?signature=SECRET')",(job,))
        self.db.commit()
        r=self.client.get('/api/insights/logs?account_id=1&group_id=11')
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual([e['result'] for e in r.json()['items']],['failed','downloading','queued'])
        self.assertEqual(r.json()['items'][0]['message_id'],1)
        export=self.client.get('/api/insights/logs/export?account_id=1&group_id=11')
        self.assertNotIn('PRIVATE',export.text);self.assertNotIn('SECRET',export.text)
        self.assertIn('fixture.invalid',export.text)
        self.assertEqual(self.client.get('/api/insights/logs?account_id=2').json()['total'],0)
        self.assertEqual(self.client.get('/api/insights/logs?result=failed').json()['total'],1)
        self.assertEqual(self.client.get('/api/insights/logs?limit=1').json()['has_more'],True)

    def test_snapshot_text_and_media_completion_are_separate(self):
        parent=self.db.execute("INSERT INTO archive_jobs(message_id,source_url,kind,status) VALUES (1,'https://fixture.invalid/post','link','stored')").lastrowid
        child=self.archive(status='failed')
        self.db.execute('INSERT INTO archive_resources VALUES (?,?)',(parent,child))
        data=self.client.get('/api/insights/dashboard?account_id=1&group_id=11').json()
        self.assertEqual(data['pages'],{'text_stored':1,'media_complete':0})

    def test_scoped_resource_action_can_open_original_signed_link_without_exporting_it(self):
        job=self.archive(status='failed')
        event=self.db.execute("SELECT id FROM activity_events WHERE task_id=? AND category='archive'",(job,)).fetchone()[0]
        response=self.client.get(f'/api/insights/events/{event}/resource?account_id=1&group_id=11')
        self.assertEqual(response.status_code,200,response.text)
        self.assertIn('token=PRIVATE',response.json()['source_url'])
        self.assertEqual(self.client.get(f'/api/insights/events/{event}/resource?account_id=2&group_id=11').status_code,404)
        self.db.execute('UPDATE messages SET is_deleted=1 WHERE id=1')
        self.assertEqual(self.client.get(f'/api/insights/events/{event}/resource?account_id=1&group_id=11').status_code,404)

    def test_log_retention_time_filters_and_transaction_rollback(self):
        job=self.archive(status='queued');self.db.commit()
        self.db.execute("UPDATE archive_jobs SET status='downloading' WHERE id=?",(job,))
        self.db.rollback()
        data=self.client.get('/api/insights/logs').json()
        self.assertEqual([r['result'] for r in data['items']],['queued'])
        self.assertEqual(self.client.get('/api/insights/logs?message_date_from=2026-08-20').json()['total'],0)
        self.assertEqual(self.client.get('/api/insights/logs?message_date_to=2026-08-02').json()['total'],1)
        self.db.execute("INSERT INTO activity_events(id,occurred_at,account_id,group_id,category,action,result) VALUES(10050,'2000-01-01',1,11,'archive','old','failed')")
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM activity_events').fetchone()[0],0)
        self.db.execute("INSERT INTO activity_events(account_id,group_id,category,action,result) VALUES(1,11,'archive','start','queued')")
        self.db.commit()
        self.assertEqual(self.client.get('/api/insights/logs?date_from=2099-01-01').json()['total'],0)
        self.assertEqual(self.client.get('/api/insights/logs?account_id=2').json()['total'],0)
