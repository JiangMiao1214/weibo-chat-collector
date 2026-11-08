"""Permanent deletion fixes its target set before removing filter dependencies."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import test_messages_http as fixtures


class HardDeleteSnapshotTests(unittest.TestCase):
    setUp = fixtures.MessagesHttpTests.setUp
    restore_overrides = staticmethod(fixtures.MessagesHttpTests.restore_overrides)
    post = fixtures.MessagesHttpTests.post

    def seed_indexes(self):
        self.db.execute("INSERT INTO search_indexes (message_id, indexed_text) SELECT id, content_text FROM messages")
        self.db.commit()

    def assert_delete(self, extra, attachment_type='file'):
        self.seed_indexes()
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / 'synthetic-original.txt'
            original.write_text('synthetic original', encoding='utf-8')
            self.db.execute("UPDATE messages SET message_type='text' WHERE id=4")
            self.db.execute("""UPDATE attachments SET attachment_type=?, file_name='unique-filename.txt',
                source_url='https://example.invalid/unique-resource', local_path=? WHERE message_id=4""",
                (attachment_type, str(original)))
            self.db.commit()
            filters = {'account_id': 1, 'group_id': 11, **extra}
            preview = self.post('hard-delete-preview', filters)
            self.assertEqual((preview['preview_count'], preview['attachment_record_count']), (1, 1))
            result = self.post('hard-delete', filters, confirm=True)
            self.assertEqual((result['preview_count'], result['deleted_count'], result['attachment_record_count']), (1, 1, 1))
            self.assertFalse(result['delete_attachment_files'])
            self.assertEqual(original.read_text(encoding='utf-8'), 'synthetic original')
        self.assertEqual([r[0] for r in self.db.execute('SELECT id FROM messages ORDER BY id')], [1, 2, 3, 5])
        self.assertEqual([r[0] for r in self.db.execute('SELECT message_id FROM search_indexes ORDER BY message_id')], [1, 2, 3, 5])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM attachments').fetchone()[0], 5)

    def test_has_attachment(self):
        self.assert_delete({'has_attachment': True})

    def test_attachment_title(self):
        self.assert_delete({'keyword': '甲删除附件'})

    def test_attachment_filename(self):
        self.assert_delete({'keyword': 'unique-filename'})

    def test_attachment_url(self):
        self.assert_delete({'keyword': 'unique-resource'})

    def test_attachment_only_photos(self):
        self.assert_delete({'viewer': json.dumps({'media': 'photos'})}, 'image')

    def test_attachment_only_video(self):
        self.assert_delete({'viewer': json.dumps({'media': 'video'})}, 'video')

    def test_attachment_only_links(self):
        self.assert_delete({'viewer': json.dumps({'media': 'links'})}, 'link')

    def test_account_group_control(self):
        self.assert_delete({})

    def test_failure_at_each_delete_step_rolls_back_every_record(self):
        self.seed_indexes()
        for table in ('attachments', 'search_indexes', 'messages'):
            with self.subTest(table=table):
                self.db.execute(f"""CREATE TEMP TRIGGER fail_delete BEFORE DELETE ON {table}
                    BEGIN SELECT RAISE(ABORT, 'synthetic deletion failure'); END""")
                self.db.commit()
                with self.assertRaisesRegex(sqlite3.IntegrityError, 'synthetic deletion failure'):
                    self.post('hard-delete', {'account_id': 1}, confirm=True)
                for name, expected in [('messages', 5), ('attachments', 6), ('search_indexes', 5)]:
                    self.assertEqual(self.db.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0], expected)
                self.db.execute('DROP TRIGGER fail_delete')
                self.db.commit()
        # A new request after rollback can still delete the complete target.
        self.assertEqual(self.post('hard-delete', {'account_id': 1}, confirm=True)['deleted_count'], 1)

    def test_many_targets_and_empty_scope_do_not_depend_on_sql_parameter_limit(self):
        self.db.executemany("""INSERT INTO messages (account_id, group_id, user_id, sent_at, is_deleted)
            VALUES (1, 11, 1, ?, 1)""", [('2026-10-01 10:00:00',)] * 1500)
        self.db.commit()
        result = self.post('hard-delete', {'account_id': 1, 'group_id': 11}, confirm=True)
        self.assertEqual((result['preview_count'], result['deleted_count']), (1501, 1501))
        empty = self.post('hard-delete', {'account_id': 2, 'group_id': 11}, confirm=True)
        self.assertEqual((empty['preview_count'], empty['deleted_count'], empty['attachment_record_count']), (0, 0, 0))
        self.assertEqual([r[0] for r in self.db.execute('SELECT id FROM messages ORDER BY id')], [1, 2, 3, 5])
