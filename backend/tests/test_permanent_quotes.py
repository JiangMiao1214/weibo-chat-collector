"""Explicit source IDs/manual choices persist; unverified raw fields never infer links."""
import unittest
import test_quote_context_http as fixtures
from app.importer import insert_message


class PermanentQuoteTests(unittest.TestCase):
    insert = fixtures.QuoteContextHttpTests.insert
    lookup = fixtures.QuoteContextHttpTests.lookup

    def setUp(self):
        fixtures.QuoteContextHttpTests.setUp(self)
        self.db.execute("UPDATE messages SET source_message_id='origin-1' WHERE id=1")
        self.db.commit()

    def bind(self,target=1,**extra):
        return self.client.put('/api/messages/10/reference',json={
            'account_id':1,'group_id':11,'deleted_only':False,'target_message_id':target,'confirmed':True,**extra})

    def test_manual_choice_resolves_duplicate_text_persists_and_can_be_removed(self):
        self.insert(2,'完整原文','2026-08-02 10:00:00')
        r=self.bind()
        self.assertEqual(r.status_code,200,r.text)
        data=self.lookup().json()
        self.assertEqual(data['match_method'],'manual')
        self.assertEqual(data['target']['id'],1)
        self.db.commit()
        self.db.execute("UPDATE messages SET content_text='原文已更正' WHERE id=1")
        self.assertEqual(self.lookup().json()['target']['id'],1)
        r=self.client.delete('/api/messages/10/reference?account_id=1&group_id=11&confirmed=true')
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.lookup().json()['match_method'],'full_text')

    def test_manual_binding_checks_scope_time_and_confirmation(self):
        self.assertEqual(self.bind(confirmed=False).status_code,422)
        for target in (10,12,21,30,999):
            self.assertIn(self.bind(target).status_code,(404,409))
        self.insert(2,'未来','2026-08-24 10:00:00')
        self.assertEqual(self.bind(2).status_code,409)
        self.assertEqual(self.bind(account_id=2).status_code,404)

    def test_soft_delete_does_not_leak_target_or_fall_back_to_similar_text(self):
        self.assertEqual(self.bind().status_code,200)
        self.insert(2,'完整原文','2026-08-02 10:00:00')
        self.db.execute('UPDATE messages SET is_deleted=1 WHERE id=1')
        data=self.lookup().json()
        self.assertEqual(data['status'],'not_found')
        self.assertIsNone(data['target'])
        self.db.execute('UPDATE messages SET is_deleted=0 WHERE id=1')
        self.assertEqual(self.lookup().json()['target']['id'],1)

    def test_normalized_import_source_id_matches_same_second_and_late_target(self):
        raw={'source_message_id':'reply-1','reply_to_source_message_id':'late-1',
             'sent_at':'2026-08-23 10:00:00','content_text':'回复没有引用文本'}
        mid=insert_message(self.db,1,11,1,None,raw,'test-hash')
        data=self.lookup(source=mid).json()
        self.assertEqual(data['match_method'],'source_id')
        self.assertEqual(data['status'],'not_found')
        self.insert(40,'晚入库原文','2026-08-23 10:00:00')
        self.db.execute("UPDATE messages SET source_message_id='late-1' WHERE id=40")
        self.assertEqual(self.lookup(source=mid).json()['target']['id'],40)
        self.assertIsNotNone(self.db.execute('SELECT 1 FROM message_references WHERE message_id=?',(mid,)).fetchone())

    def test_raw_weibo_status_reply_fields_are_not_chat_references(self):
        raw={'source_message_id':'reply-2','sent_at':'2026-08-23 10:00:00','content_text':'普通链接',
             'raw_payload':{'url_objects':[{'status':{'in_reply_to_status_id':'origin-1'}}]}}
        mid=insert_message(self.db,1,11,1,None,raw,'test-other')
        self.assertEqual(self.lookup(source=mid).json()['status'],'not_quote')

    def test_candidates_are_scoped_bounded_and_allow_equal_time(self):
        r=self.client.get('/api/messages/10/reference-candidates?account_id=1&group_id=11')
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual([m['id'] for m in r.json()['items']],[1])
        self.assertNotIn('raw_payload',r.text)

    def test_import_backfill_is_idempotent_and_removed_relation_stays_removed(self):
        import json
        from app.database import apply_database_migrations
        payload={'source_message_id':'reply-10','reply_to_source_message_id':'origin-1','content_text':'回复'}
        self.db.execute('UPDATE messages SET raw_payload=? WHERE id=10',(json.dumps(payload),))
        apply_database_migrations(self.db)
        self.assertEqual(self.lookup().json()['match_method'],'source_id')
        apply_database_migrations(self.db)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM message_references').fetchone()[0],1)
        r=self.client.delete('/api/messages/10/reference?account_id=1&group_id=11&confirmed=true')
        self.assertEqual(r.status_code,200)
        apply_database_migrations(self.db)
        self.assertEqual(self.lookup().json()['match_method'],'full_text')

    def test_hard_deleted_manual_target_does_not_bind_a_different_same_text(self):
        self.assertEqual(self.bind().status_code,200)
        self.db.execute('DELETE FROM attachments WHERE message_id=1')
        self.db.execute('DELETE FROM messages WHERE id=1')
        self.insert(2,'完整原文','2026-08-02 10:00:00')
        self.assertEqual(self.lookup().json()['status'],'not_found')

    def test_source_id_duplicates_are_ambiguous_and_never_cross_groups(self):
        raw={'source_message_id':'reply-1','reply_to_source_message_id':'origin-1',
             'sent_at':'2026-08-23 10:00:00','content_text':'回复'}
        mid=insert_message(self.db,1,11,1,None,raw,'hash')
        self.db.execute("UPDATE messages SET source_message_id='origin-1' WHERE id IN (12,21)")
        self.assertEqual(self.lookup(source=mid).json()['target']['id'],1)
        self.insert(2,'不同文本','2026-08-02 10:00:00')
        self.db.execute("UPDATE messages SET source_message_id='origin-1' WHERE id=2")
        data=self.lookup(source=mid).json()
        self.assertEqual(data['status'],'ambiguous')
        self.assertIsNone(data['target'])

    def test_candidates_use_one_read_snapshot_during_concurrent_soft_delete(self):
        import sqlite3
        import tempfile
        from pathlib import Path
        from app.main import app
        from app.database import get_connection
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'isolated.sqlite3'
            reader=sqlite3.connect(path,check_same_thread=False)
            reader.row_factory=sqlite3.Row
            self.db.commit()
            self.db.backup(reader)
            reader.execute('PRAGMA journal_mode=WAL')
            writer=sqlite3.connect(path,check_same_thread=False)
            fired=[]
            def concurrent_delete(statement):
                if statement.strip().startswith('SELECT m.id,') and not fired:
                    fired.append(True)
                    writer.execute('UPDATE messages SET is_deleted=1 WHERE id=1')
                    writer.commit()
            reader.set_trace_callback(concurrent_delete)
            app.dependency_overrides[get_connection]=lambda:reader
            try:
                r=self.client.get('/api/messages/10/reference-candidates?account_id=1&group_id=11')
                self.assertEqual(r.status_code,200,r.text)
                self.assertTrue(fired)
                self.assertTrue(all(item['is_deleted']==0 for item in r.json()['items']))
            finally:
                reader.close()
                writer.close()


if __name__=='__main__':unittest.main()
