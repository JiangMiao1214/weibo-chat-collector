"""Synthetic imports exercise UID identity before fallback message deduplication."""
import csv
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.database import apply_database_migrations
from app.importer import import_file
from app.collectors.base import CollectionCandidate
from app.services.collection_runner import create_queued_api_job, ingest_api_candidates


def message(uid=None, **patch):
    return {'sender_name': '模拟同名成员', 'source_user_id': uid,
            'sent_at': '2026-10-01 11:00:00', 'message_type': 'text',
            'content_text': '同秒相同正文', **patch}


class ImportUserIdentityTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.db_path = self.root / 'isolated.sqlite'
        with sqlite3.connect(self.db_path) as connection:
            apply_database_migrations(connection)

    def import_messages(self, messages, suffix='json', account='模拟账号', group='模拟群'):
        source = self.root / f'synthetic.{suffix}'
        if suffix == 'json':
            source.write_text(json.dumps({'account': account, 'group': group, 'messages': messages}), encoding='utf-8')
        else:
            with source.open('w', encoding='utf-8', newline='') as output:
                writer = csv.DictWriter(output, fieldnames=['account', 'group', *messages[0]])
                writer.writeheader()
                writer.writerows({'account': account, 'group': group, **item} for item in messages)
        return import_file(self.db_path, source)

    def check_distinct_uids(self, suffix, source_ids=False):
        messages = [message(content_text='无 UID 历史'), message('uid-a'), message('uid-b')]
        if source_ids:
            messages = [{**item, 'source_message_id': f'mid-{index}'} for index, item in enumerate(messages)]
        summary = self.import_messages(messages, suffix)
        self.assertEqual((summary.inserted_count, summary.duplicate_count), (3, 0))
        with sqlite3.connect(self.db_path) as connection:
            rows = connection.execute('SELECT cu.source_user_id FROM messages m JOIN chat_users cu ON cu.id=m.user_id ORDER BY m.id').fetchall()
            self.assertEqual(rows, [(None,), ('uid-a',), ('uid-b',)])
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM group_members').fetchone()[0], 3)
        repeat = self.import_messages(messages, suffix)
        self.assertEqual((repeat.inserted_count, repeat.duplicate_count), (0, 3))

    def test_json_without_source_message_ids(self):
        self.check_distinct_uids('json')

    def test_csv_without_source_message_ids(self):
        self.check_distinct_uids('csv')

    def test_stable_message_ids_still_keep_member_identities(self):
        self.check_distinct_uids('json', source_ids=True)

    def test_known_identity_never_relabels_anonymous_history(self):
        self.import_messages([message(content_text='无 UID 历史')])
        with sqlite3.connect(self.db_path) as connection:
            anonymous_id = connection.execute('SELECT user_id FROM messages').fetchone()[0]
        self.import_messages([message('uid-a'), message('uid-b')])
        with sqlite3.connect(self.db_path) as connection:
            self.assertEqual(connection.execute('SELECT source_user_id FROM chat_users WHERE id=?', (anonymous_id,)).fetchone(), (None,))
            self.assertEqual(connection.execute("SELECT user_id FROM messages WHERE content_text='无 UID 历史'").fetchone()[0], anonymous_id)
            self.assertEqual(connection.execute('SELECT COUNT(DISTINCT user_id) FROM messages').fetchone()[0], 3)

    def test_uid_rename_retains_identity_and_older_nickname_does_not_replace_latest(self):
        self.import_messages([message('uid-a', sender_name='旧昵称')])
        # Same UID/time/content with a changed nickname remains a duplicate.
        duplicate = self.import_messages([message('uid-a', sender_name='新昵称')])
        self.assertEqual((duplicate.inserted_count, duplicate.duplicate_count), (0, 1))
        self.import_messages([message('uid-a', sender_name='更旧昵称', sent_at='2026-09-01 11:00:00')])
        with sqlite3.connect(self.db_path) as connection:
            self.assertEqual(connection.execute('SELECT source_user_id, display_name FROM chat_users').fetchall(), [('uid-a', '新昵称')])
            self.assertEqual(connection.execute('SELECT COUNT(DISTINCT user_id) FROM messages').fetchone()[0], 1)
            self.assertEqual(connection.execute('SELECT display_name_in_group FROM group_members').fetchone()[0], '新昵称')

    def test_missing_and_empty_uid_share_anonymous_identity_but_not_known_uid(self):
        self.import_messages([message('uid-a')])
        for anonymous in (message(''), message(None), {k: v for k, v in message().items() if k != 'source_user_id'}):
            self.import_messages([anonymous])
        with sqlite3.connect(self.db_path) as connection:
            self.assertEqual(connection.execute('SELECT source_user_id FROM chat_users ORDER BY id').fetchall(), [('uid-a',), (None,)])
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM messages').fetchone()[0], 2)

    def test_dedup_remains_account_and_group_scoped(self):
        messages = [message(content_text='无 UID 历史'), message('uid-a', source_message_id='shared-mid'), message('uid-b')]
        self.import_messages(messages)
        # Keep a legacy second group as an isolation fixture, not a new user-facing binding.
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("INSERT INTO chat_groups (account_id, name) VALUES (1, '旧另一群')")
        for account, group in [('模拟账号', '旧另一群'), ('模拟账号乙', '模拟群')]:
            summary = self.import_messages(messages, account=account, group=group)
            self.assertEqual((summary.inserted_count, summary.duplicate_count), (3, 0))
            repeat = self.import_messages(messages, account=account, group=group)
            self.assertEqual((repeat.inserted_count, repeat.duplicate_count), (0, 3))
        with sqlite3.connect(self.db_path) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM messages').fetchone()[0], 9)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM group_members').fetchone()[0], 9)

    def test_mock_api_ingestion_uses_the_same_uid_rules(self):
        self.import_messages([message(content_text='无 UID 历史')])
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            job = create_queued_api_job(connection, account_id=1, group_id=1,
                source_group_id='synthetic-group', timezone_name='Asia/Shanghai', page_size=20,
                range_start=datetime(2026, 10, 1), range_end=datetime(2026, 10, 1, 23, 59, 59))
            candidates = [CollectionCandidate(account_id=1, group_id=1, source_user_id=uid,
                sender_name='模拟同名成员', sent_at=datetime(2026, 10, 1, 11),
                message_type='text', content_text='同秒相同正文') for uid in ('uid-a', 'uid-b')]
            summary = ingest_api_candidates(connection, collection_job_id=job, account_id=1, group_id=1, candidates=candidates)
            self.assertEqual((summary.inserted_count, summary.duplicate_count), (2, 0))
            self.assertEqual(connection.execute('SELECT COUNT(DISTINCT user_id) FROM messages').fetchone()[0], 3)
