"""One account/one group at every write entry; isolated data and fake sessions only."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import patch
import test_messages_http as fixtures
from app.database import apply_database_migrations, get_connection
from app.importer import import_file
from app.main import app


class GroupLimitTests(unittest.TestCase):
    restore_overrides = staticmethod(fixtures.MessagesHttpTests.restore_overrides)

    def setUp(self):
        fixtures.MessagesHttpTests.setUp(self)

    def new_account(self):
        return self.client.post('/api/management/accounts', json={'display_name': '待配置账号'}).json()['id']

    def test_first_group_allowed_second_rejected_even_when_disabled(self):
        aid = self.new_account()
        first = self.client.post('/api/management/groups', json={'account_id': aid, 'name': '首群', 'is_active': False})
        self.assertEqual(first.status_code, 201, first.text)
        second = self.client.post('/api/management/groups', json={'account_id': aid, 'name': '另一个群'})
        self.assertEqual(second.status_code, 409, second.text)
        self.assertIn('一个群聊', second.text)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM chat_groups WHERE account_id=?', (aid,)).fetchone()[0], 1)

    def test_advanced_target_cannot_add_second_group_and_rolls_back_account_edit(self):
        result = self.client.post('/api/weibo-api/targets', json={'account_id': 2, 'account_name': '不应保存改名',
            'group_name': '新增群', 'source_group_id': '999'})
        self.assertEqual(result.status_code, 409, result.text)
        self.assertEqual(self.db.execute('SELECT display_name FROM weibo_accounts WHERE id=2').fetchone()[0], '账号乙')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM chat_groups WHERE account_id=2').fetchone()[0], 1)

    def test_different_accounts_can_bind_same_remote_group(self):
        for _ in range(2):
            aid = self.new_account()
            result = self.client.post('/api/management/groups', json={'account_id': aid, 'name': '相同远端群', 'source_group_id': '123'})
            self.assertEqual(result.status_code, 201, result.text)

    def test_legacy_groups_preserved_but_new_binding_rejected_in_all_edit_entries(self):
        self.db.execute("UPDATE chat_groups SET source_group_id='123' WHERE id=11")
        self.db.commit()
        form={'account_id':1,'name':'保留原群','source_group_id':'123'}
        self.assertEqual(self.client.patch('/api/management/groups/11',json=form).status_code,200)
        form.update(source_group_id='456')
        self.assertEqual(self.client.patch('/api/management/groups/12',json=form).status_code,409)
        result=self.client.post('/api/weibo-api/targets',json={'account_id':1,'account_name':'账号甲',
            'group_id':12,'group_name':'另一群','source_group_id':'456'})
        self.assertEqual(result.status_code,409,result.text)
        from test_browser_login_api import ImmediateConfirmationManager
        from app.services.browser_login_manager import BrowserLoginSession
        from datetime import datetime,timezone,timedelta
        now=datetime.now(timezone.utc)
        session=BrowserLoginSession(session_id='fake',account_id=1,group_id=12,status='group_candidate_found',
            created_at=now,expires_at=now+timedelta(minutes=10),cookie_ready=True,candidate_source_group_id='456')
        with patch('app.api.browser_login.get_browser_login_manager',return_value=ImmediateConfirmationManager(session)):
            result=self.client.post('/api/weibo-api/browser-sessions/fake/confirm-group',json={'candidate_source_group_id':'456'})
        self.assertEqual(result.status_code,409,result.text)
        apply_database_migrations(self.db)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],5)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM chat_groups WHERE account_id=1').fetchone()[0],2)
        self.assertIsNone(self.db.execute('SELECT source_group_id FROM chat_groups WHERE id=12').fetchone()[0])

    def test_import_cannot_create_second_group_or_partial_batch(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)/'fixture.sqlite3'
            with sqlite3.connect(path) as db:
                apply_database_migrations(db)
                db.execute("INSERT INTO weibo_accounts(id,display_name) VALUES(1,'导入账号')")
                db.execute("INSERT INTO chat_groups(account_id,name,is_active) VALUES(1,'已有群',0)")
            source = Path(directory)/'input.json'
            source.write_text(json.dumps({'account':'导入账号','group':'第二群','messages':[]}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '一个群聊'):
                import_file(path, source)
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM chat_groups').fetchone()[0],1)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM import_batches').fetchone()[0],0)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM collection_jobs').fetchone()[0],0)
            source.write_text(json.dumps({'account':'导入账号','group':'已有群','messages':[]}), encoding='utf-8')
            self.assertEqual(import_file(path, source).inserted_count,0)

    def test_simultaneous_advanced_and_management_creation_only_one_wins(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'concurrent.sqlite3'
            with sqlite3.connect(path) as db:
                apply_database_migrations(db)
                db.execute("INSERT INTO weibo_accounts(id,display_name) VALUES(1,'并发账号')")
            barrier=Barrier(2)
            def connection():
                db=sqlite3.connect(path,check_same_thread=False,timeout=5)
                db.row_factory=sqlite3.Row
                db.execute('PRAGMA foreign_keys=ON')
                try:
                    barrier.wait(timeout=5)
                    with db: yield db
                finally: db.close()
            app.dependency_overrides[get_connection]=connection
            def create(index):
                if index:
                    return self.client.post('/api/weibo-api/targets',json={'account_id':1,'account_name':'并发账号',
                        'group_name':'备用群','source_group_id':'123'}).status_code
                return self.client.post('/api/management/groups',json={'account_id':1,'name':'管理群'}).status_code
            with ThreadPoolExecutor(max_workers=2) as pool:
                codes=list(pool.map(create,[0,1]))
            self.assertEqual(codes.count(409),1,codes)
            self.assertEqual(sum(code in (200,201) for code in codes),1,codes)
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM chat_groups').fetchone()[0],1)
