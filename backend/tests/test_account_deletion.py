"""Deletion previews and execution use fixture databases and temporary fake credentials."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import test_messages_http as fixtures
from app.main import app
from app.settings import get_settings


class AccountDeletionTests(unittest.TestCase):
    restore_overrides = staticmethod(fixtures.MessagesHttpTests.restore_overrides)

    def setUp(self):
        fixtures.MessagesHttpTests.setUp(self)
        temp=TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.auth=Path(temp.name)
        app.dependency_overrides[get_settings]=lambda:SimpleNamespace(weibo_api_auth_dir=self.auth)
        self.manager=patch('app.services.browser_login_manager.get_browser_login_manager').start().return_value
        self.addCleanup(patch.stopall)
        self.manager.get_active_session.return_value=None
        self.db.execute("INSERT INTO weibo_accounts(id,display_name) VALUES(3,'可删除测试账号')")
        self.db.execute("INSERT INTO chat_groups(id,account_id,name) VALUES(31,3,'空群')")
        self.db.commit()
        for aid in (2,3): (self.auth/f'account-{aid}.cookies.json').write_text('fake-cookie',encoding='utf-8')

    def preview(self,aid=3):
        result=self.client.get(f'/api/management/accounts/{aid}/delete-preview')
        self.assertEqual(result.status_code,200,result.text)
        return result.json()

    def delete(self,preview,aid=3,confirm=True):
        return self.client.request('DELETE',f'/api/management/accounts/{aid}',json={
            'confirmed':confirm,'preview_token':preview['preview_token']})

    def test_empty_account_and_groups_deleted_only_after_exact_confirmation(self):
        preview=self.preview()
        self.assertTrue(preview['can_delete'])
        self.assertEqual([g['id'] for g in preview['groups']],[31])
        self.assertTrue(preview['credentials_present'])
        self.assertNotIn('fake-cookie',str(preview))
        self.assertEqual(self.client.delete('/api/management/accounts/3').status_code,422)
        self.assertEqual(self.delete(preview,confirm=False).status_code,422)
        self.assertIsNotNone(self.db.execute('SELECT id FROM weibo_accounts WHERE id=3').fetchone())
        result=self.delete(preview)
        self.assertEqual(result.status_code,200,result.text)
        self.assertIsNone(self.db.execute('SELECT id FROM weibo_accounts WHERE id=3').fetchone())
        self.assertIsNone(self.db.execute('SELECT id FROM chat_groups WHERE id=31').fetchone())
        self.assertFalse((self.auth/'account-3.cookies.json').exists())
        self.assertEqual((self.auth/'account-2.cookies.json').read_text(),'fake-cookie')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],5)
        self.assertEqual(list(self.db.execute('PRAGMA foreign_key_check')),[])

    def test_account_with_no_groups_or_credentials_can_be_deleted(self):
        self.db.execute('DELETE FROM chat_groups WHERE id=31'); self.db.commit()
        (self.auth/'account-3.cookies.json').unlink()
        result=self.delete(self.preview())
        self.assertEqual(result.status_code,200,result.text)

    def test_live_and_recycled_messages_are_both_blocking_and_originals_untouched(self):
        original=self.auth/'shared-original'; original.write_bytes(b'fixture-image')
        self.db.execute('UPDATE attachments SET local_path=?',(str(original),)); self.db.commit()
        preview=self.preview(1)
        self.assertFalse(preview['can_delete'])
        self.assertEqual(preview['counts']['messages'],2)
        self.assertEqual(preview['counts']['recycled_messages'],1)
        self.assertEqual(preview['counts']['attachments'],4)
        self.assertEqual(self.delete(preview,1).status_code,409)
        self.assertEqual(original.read_bytes(),b'fixture-image')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],5)

    def test_every_history_category_blocks_deletion_even_without_messages(self):
        statements=[
            "INSERT INTO collection_jobs(account_id,group_id,range_start,range_end,status) VALUES(3,31,'a','b','stopped')",
            "INSERT INTO import_batches(account_id,group_id,source_type) VALUES(3,31,'fixture')",
            "INSERT INTO browser_page_captures(account_id,group_id,range_start,range_end,captured_at,blocks_json) VALUES(3,31,'a','b','now','[]')",
            "INSERT INTO weibo_verification_reports(account_id,group_id) VALUES(3,31)",
            "INSERT INTO deletion_jobs(account_id,group_id,filter_json) VALUES(NULL,31,'{}')",
            "INSERT INTO group_members(group_id,user_id) VALUES(31,1)",
            "INSERT INTO archive_groups(account_id,group_id) VALUES(3,31)",
            "INSERT INTO archive_https_hosts(account_id,host) VALUES(3,'fixture.invalid')",
            "INSERT INTO ocr_groups(account_id,group_id) VALUES(3,31)",
            "INSERT INTO ocr_results(account_id,content_hash,engine,text) VALUES(3,'hash','fake','fixture')",
            "INSERT INTO activity_events(account_id,group_id,category,action,result) VALUES(3,31,'fixture','start','done')",
        ]
        for statement in statements:
            with self.subTest(statement=statement):
                # Each independent category must block; reset fixture only, never production data.
                self.db.execute(statement); self.db.commit()
                preview=self.preview()
                self.assertFalse(preview['can_delete'])
                self.assertEqual(self.delete(preview).status_code,409)
                self.assertTrue((self.auth/'account-3.cookies.json').exists())
                table=statement.split('(')[0].split()[-1]
                self.db.execute(f'DELETE FROM {table} WHERE '+('group_id=31' if table=='group_members' else 'account_id=3 OR group_id=31' if table=='deletion_jobs' else 'account_id=3'))
                self.db.execute('DELETE FROM activity_events WHERE account_id=3'); self.db.commit()

    def test_changed_scope_credentials_or_identity_requires_new_preview(self):
        for change in ('group','name','cookie'):
            with self.subTest(change=change):
                preview=self.preview()
                if change=='group': self.db.execute("UPDATE chat_groups SET name='更名群' WHERE id=31")
                elif change=='name': self.db.execute("UPDATE weibo_accounts SET display_name='更名账号' WHERE id=3")
                else: (self.auth/'account-3.cookies.json').write_text('new-fake-cookie')
                self.db.commit()
                self.assertEqual(self.delete(preview).status_code,409)
                self.assertIsNotNone(self.db.execute('SELECT id FROM weibo_accounts WHERE id=3').fetchone())
        self.assertEqual(self.delete(self.preview(),aid=2).status_code,409)

    def test_scan_or_new_running_collection_blocks_and_preserves_account(self):
        preview=self.preview()
        self.manager.get_active_session.return_value={'account_id':3}
        self.assertFalse(self.preview()['can_delete'])
        self.assertEqual(self.delete(preview).status_code,409)
        self.manager.get_active_session.return_value=None
        self.db.execute("INSERT INTO collection_jobs(account_id,group_id,range_start,range_end,status) VALUES(3,31,'a','b','running')")
        self.db.commit()
        self.assertEqual(self.delete(preview).status_code,409)
        self.assertTrue((self.auth/'account-3.cookies.json').exists())

    def test_credential_failure_rolls_back_database_and_does_not_follow_symlinks(self):
        preview=self.preview()
        with patch.object(Path,'unlink',side_effect=PermissionError('fixture-denied')):
            result=self.delete(preview)
        self.assertEqual(result.status_code,409,result.text)
        self.assertIsNotNone(self.db.execute('SELECT id FROM weibo_accounts WHERE id=3').fetchone())
        self.assertIsNotNone(self.db.execute('SELECT id FROM chat_groups WHERE id=31').fetchone())
        cookie=self.auth/'account-3.cookies.json'; cookie.unlink()
        cookie.symlink_to(self.auth/'account-2.cookies.json')
        self.assertFalse(self.preview()['can_delete'])
        self.assertEqual((self.auth/'account-2.cookies.json').read_text(),'fake-cookie')

    def test_database_commit_failure_restores_credentials_and_all_rows(self):
        import sqlite3
        from app.services.account_deletion import delete_empty_account
        from fastapi import HTTPException
        preview=self.preview()
        class FailedCommit:
            def __getattr__(proxy,name): return getattr(self.db,name)
            def commit(proxy): raise sqlite3.OperationalError('fixture commit failure')
        with self.assertRaises(HTTPException) as raised:
            delete_empty_account(FailedCommit(),3,self.auth,preview['preview_token'])
        self.assertEqual(raised.exception.status_code,409)
        self.assertEqual((self.auth/'account-3.cookies.json').read_text(),'fake-cookie')
        self.assertIsNotNone(self.db.execute('SELECT id FROM weibo_accounts WHERE id=3').fetchone())
        self.assertIsNotNone(self.db.execute('SELECT id FROM chat_groups WHERE id=31').fetchone())

    def test_inflight_archive_and_ocr_remain_untouched(self):
        self.db.execute("INSERT INTO archive_jobs(id,message_id,source_url,kind,status) VALUES(100,1,'https://fixture.invalid/a','image','downloading')")
        self.db.execute("INSERT INTO ocr_jobs(archive_id,content_hash,status) VALUES(100,'fake','processing')")
        self.db.commit()
        preview=self.preview(1)
        self.assertEqual(preview['counts']['archive_jobs'],1)
        self.assertEqual(preview['counts']['ocr_jobs'],1)
        self.assertEqual(self.delete(preview,1).status_code,409)
        self.assertEqual(self.db.execute('SELECT status FROM archive_jobs WHERE id=100').fetchone()[0],'downloading')
        self.assertEqual(self.db.execute('SELECT status FROM ocr_jobs WHERE archive_id=100').fetchone()[0],'processing')

    def test_cookie_upload_takes_write_lock_before_saving_and_checks_account(self):
        from app.api.weibo_api import save_account_cookies,SaveAccountCookiesRequest
        from fastapi import HTTPException
        payload=SaveAccountCookiesRequest(cookies=[{'name':'SUB','value':'fake','domain':'.weibo.com'}])
        def save(*args):
            self.assertTrue(self.db.in_transaction,'upload must serialize with account deletion before writing cookies')
            return {'authenticated':True}
        with patch('app.api.weibo_api.CookieProfileStore') as store, patch('app.api.weibo_api.get_settings',return_value=SimpleNamespace(weibo_api_auth_dir=self.auth)):
            store.return_value.save.side_effect=save
            save_account_cookies(3,payload,self.db)
            self.db.rollback()
            self.assertEqual(self.delete(self.preview()).status_code,200)
            with self.assertRaises(HTTPException): save_account_cookies(3,payload,self.db)
            self.assertEqual(store.return_value.save.call_count,1)
