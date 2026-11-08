"""Independent join-notice filter; isolated DB, synthetic messages, no workers."""
import json
import unittest

import test_messages_http as fixtures


class JoinNoticeFilterTests(unittest.TestCase):
    restore_overrides = staticmethod(fixtures.MessagesHttpTests.restore_overrides)

    def setUp(self):
        fixtures.MessagesHttpTests.setUp(self)
        self.notices = ['模拟甲 加入了群', '模拟乙加入了群聊', ' 模拟丙 加入了群！ ']
        self.normal = ['这是正常讨论', '早安', '模拟甲加入了群，欢迎',
                       '「模拟乙加入了群」 - - - 回复', '内容\n模拟丙加入了群']
        rows = [(100+i, 1, 11, text, 0) for i, text in enumerate(self.notices+self.normal)]
        rows += [(200, 1, 12, '其他群的讨论', 0), (201, 2, 21, '其他账号的讨论', 0),
                 (300, 1, 11, '模拟删除成员加入了群', 1), (301, 1, 11, '回收站正常讨论', 1)]
        self.db.executemany("""INSERT INTO messages
            (id,account_id,group_id,user_id,sent_at,message_type,content_text,is_deleted)
            VALUES (?,?,?,1,'2026-08-23 13:00:00','text',?,?)""", rows)
        self.db.executemany("INSERT INTO attachments(message_id,attachment_type) VALUES (?,?)",
                            [(100, 'image'), (103, 'file')])
        self.db.commit()

    def filters(self, **changes):
        return {'account_id': 1, 'group_id': 11,
                'viewer': json.dumps({'hours': [13], 'hide_join_notices': True, **changes})}

    def listing(self, **params):
        response = self.client.get('/api/messages', params={**self.filters(), **params})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_independent_toggle_preserves_same_senders_greetings_and_discussion(self):
        before = list(self.db.iterdump())
        first = self.listing(limit=2)
        second = self.listing(limit=100, **first['next_cursor'])
        self.assertEqual({item['id'] for item in first['items']+second['items']}, set(range(103, 108)))
        self.assertEqual(first['total'], 5)
        self.assertEqual(first['attachment_summary'], {'message_count': 1, 'total_count': 1, 'by_type': {'file': 1}})
        self.assertEqual(second['attachment_summary'], first['attachment_summary'])
        self.assertEqual(self.listing(**self.filters(hide_join_notices=False))['total'], 8)
        self.assertEqual(self.listing(**self.filters(hide_noise=True))['total'], 4)
        self.assertEqual(list(self.db.iterdump()), before)

    def test_search_export_and_destructive_scope_share_the_filter(self):
        request = {'filters': self.filters()}
        exported = self.client.post('/api/message-view/export', json=request)
        self.assertEqual(exported.status_code, 200, exported.text)
        for notice in self.notices:
            self.assertNotIn('\n'+notice+'\n', exported.text)
        for normal in self.normal:
            self.assertIn('\n'+normal+'\n', exported.text)
        self.assertIn('[file]', exported.text)
        self.assertNotIn('[image]', exported.text)
        result = self.client.post('/api/message-view/matches', json={**request, 'query': '加入了群'})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['total'], 3)
        for action, field, extra in [('delete-preview', 'preview_count', {}),
                                     ('soft-delete', 'deleted_count', {'confirm': True}),
                                     ('restore', 'restored_count', {'confirm': True})]:
            # Restore also includes the pre-existing normal deleted message #301.
            response = self.client.post('/api/messages/'+action, json={**request, **extra})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()[field], 6 if action == 'restore' else 5)
        self.assertEqual([r[0] for r in self.db.execute('SELECT is_deleted FROM messages WHERE id BETWEEN 100 AND 102')], [0, 0, 0])
        response = self.client.post('/api/messages/hard-delete', json={**request, 'confirm': True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['deleted_count'], 0)
        self.assertIsNotNone(self.db.execute('SELECT id FROM messages WHERE id=300').fetchone())

    def test_group_account_recycle_bin_and_noise_statistics_stay_scoped(self):
        self.assertEqual(self.listing(account_id=2, group_id=11)['total'], 0)
        self.assertEqual([m['id'] for m in self.listing(group_id=12)['items']], [200])
        self.assertEqual([m['id'] for m in self.listing(account_id=2, group_id=21)['items']], [201])
        self.assertEqual([m['id'] for m in self.listing(include_deleted=True, deleted_only=True)['items']], [301])
        for noise, expected in [(False, 0), (True, 1)]:
            result = self.client.post('/api/message-view/facets', json={'filters': self.filters(hide_noise=noise)})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['hidden_noise'], expected)
        # Context deliberately ignores content filters and still includes notices.
        response = self.client.get('/api/message-view/103/context', params={'account_id': 1, 'group_id': 11})
        self.assertIn(102, [m['id'] for m in response.json()['items']])

    def test_invalid_toggle_is_rejected_without_modifying_messages(self):
        before = list(self.db.iterdump())
        bad = self.filters(hide_join_notices=['invalid'])
        self.assertEqual(self.client.get('/api/messages', params=bad).status_code, 422)
        self.assertEqual(self.client.post('/api/messages/soft-delete',
                                         json={'filters': bad, 'confirm': True}).status_code, 422)
        self.assertEqual(list(self.db.iterdump()), before)
