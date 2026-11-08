"""Viewer contracts against an isolated DB; no production lifespan/network."""
import json
import unittest

import test_messages_http as fixtures


class MessageViewHttpTests(unittest.TestCase):
    restore_overrides = staticmethod(fixtures.MessagesHttpTests.restore_overrides)

    def setUp(self):
        fixtures.MessagesHttpTests.setUp(self)
        self.db.execute("INSERT INTO chat_users (id, display_name, avatar_url) VALUES (2, '用户', 'https://fixture.invalid/avatar.png')")
        self.db.executemany("""INSERT INTO messages
            (id, account_id, group_id, user_id, sent_at, message_type, content_text)
            VALUES (?, 1, 11, ?, ?, ?, ?)""", [
                (6, 2, '2026-08-23 11:00:00', 'text', '早安'),
                (7, 2, '2026-08-23 12:00:00', 'text', '搜索目标 https://weibo.com/123/Abc'),
                (8, 1, '2026-08-23 12:01:00', 'text', '分享视频：搜索目标'),
            ])
        self.db.execute("INSERT INTO attachments (message_id, attachment_type, source_url) VALUES (8, 'video', 'https://fixture.invalid/video')")
        self.db.commit()

    def listing(self, **viewer):
        response = self.client.get('/api/messages', params={
            'account_id': 1, 'group_id': 11, 'viewer': json.dumps(viewer), 'limit': 1})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def view(self, endpoint, viewer=None, **extra):
        response = self.client.post('/api/message-view/' + endpoint, json={
            'filters': {'account_id': 1, 'group_id': 11, 'viewer': json.dumps(viewer or {})}, **extra})
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def test_members_hours_union_and_full_facets(self):
        self.assertEqual(self.listing(members=[2], hours=[11, 12])['total'], 2)
        self.assertEqual(self.listing(members=[1, 2], hours=[12])['total'], 2)
        data = self.view('facets', {'members': [2], 'hours': [12]}).json()
        self.assertEqual([(m['id'], m['count']) for m in data['members']], [(1, 2), (2, 2)])
        self.assertEqual(data['members'][1]['avatar_url'], 'https://fixture.invalid/avatar.png')
        self.assertEqual(len(data['hours']), 24)
        self.assertEqual(data['hours'][12], 2)

    def test_media_overlap_and_noise_are_read_only(self):
        self.assertEqual(self.listing(media='photos')['total'], 1)
        self.assertEqual(self.listing(media='video')['items'][0]['id'], 8)
        self.assertEqual(self.listing(media='links')['total'], 2)
        self.assertEqual(self.listing(hide_noise=True)['total'], 3)
        self.assertEqual(self.view('facets', {'hide_noise': True}).json()['hidden_noise'], 1)
        self.assertEqual(self.listing()['total'], 4)
        self.assertEqual(self.db.execute('SELECT is_deleted FROM messages WHERE id=6').fetchone()[0], 0)

    def test_video_placeholder_is_not_a_link_without_video_url(self):
        self.db.execute("UPDATE messages SET content_text='分享视频操作说明' WHERE id=7")
        self.assertEqual(self.listing(media='video')['total'], 1)
        self.db.execute("UPDATE messages SET content_text='Video' WHERE id=7")
        self.assertEqual(self.listing(media='video')['total'], 2)
        self.assertEqual(self.listing(media='links')['total'], 1)

    def test_invalid_viewer_rejected_in_reads_and_deletion(self):
        for viewer in ({'hours': [24]}, {'members': [-1]}, {'media': 'bad'}, {'unknown': True}):
            with self.subTest(viewer=viewer):
                encoded = json.dumps(viewer)
                self.assertEqual(self.client.get('/api/messages', params={'viewer': encoded}).status_code, 422)
                self.assertEqual(self.client.post('/api/messages/soft-delete', json={
                    'filters': {'viewer': encoded}, 'confirm': True}).status_code, 422)

    def test_extended_filters_apply_to_delete_restore_and_export(self):
        filters = {'account_id': 1, 'group_id': 11, 'viewer': json.dumps({'members': [2], 'hours': [12]})}
        export = self.view('export', {'members': [2], 'hours': [12]})
        self.assertIn('搜索目标 https://weibo.com/123/Abc', export.text)
        self.assertNotIn('早安', export.text)
        self.assertNotIn('分享视频', export.text)
        self.assertIn('attachment', export.headers['content-disposition'])
        for action, field, extra in [('delete-preview', 'preview_count', {}),
                                     ('soft-delete', 'deleted_count', {'confirm': True}),
                                     ('restore', 'restored_count', {'confirm': True})]:
            response = self.client.post('/api/messages/' + action, json={'filters': filters, **extra})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()[field], 1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM messages WHERE is_deleted=0').fetchone()[0], 6)

    def test_general_context_is_scoped_and_can_recenter_unloaded_targets(self):
        url = '/api/message-view/7/context'
        params = {'account_id': 1, 'group_id': 11}
        response = self.client.get(url, params=params)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual([m['id'] for m in data['items']], [8, 7, 6, 1])
        self.assertEqual(data['target_id'], 7)
        self.assertNotIn('raw_payload', str(data))
        for wrong in ({'account_id': 2, 'group_id': 11}, {**params, 'deleted_only': True}):
            self.assertEqual(self.client.get(url, params=wrong).status_code, 404)

    def test_search_matches_are_all_filtered_rows_literal_and_not_a_list_filter(self):
        result = self.view('matches', query='搜索目标').json()
        self.assertEqual([item['id'] for item in result['items']], [8, 7])
        self.assertEqual(self.view('matches', {'members': [2]}, query='搜索目标').json()['items'][0]['id'], 7)
        self.assertEqual(self.view('matches', query='%').json()['items'], [])
        self.assertEqual(self.listing()['total'], 4)

    def test_rich_preview_uses_only_whitelisted_stored_data(self):
        raw = {'cookie': 'FAKE_SECRET', 'url_objects': [{'url_ori': 'https://weibo.com/123/Abc',
            'object': {'status': {'user': {'screen_name': '原作者'}, 'text': '<b>原文</b>',
                'reposts_count': 3, 'comments_count': 4, 'attitudes_count': 5}}}]}
        self.db.execute('UPDATE messages SET raw_payload=? WHERE id=7', (json.dumps(raw),))
        data = self.listing(members=[2], hours=[12])
        preview = data['items'][0]['link_previews'][0]
        self.assertEqual((preview['author'], preview['text']), ('原作者', '原文'))
        self.assertEqual((preview['reposts'], preview['comments'], preview['likes']), (3, 4, 5))
        self.assertNotIn('FAKE_SECRET', str(data))
        self.db.execute("UPDATE messages SET raw_payload='invalid json' WHERE id=7")
        self.assertEqual(self.listing(members=[2], hours=[12])['items'][0]['link_previews'][0]['text'], None)

    def test_export_and_search_cover_more_than_one_page_without_deleted_or_other_accounts(self):
        self.db.executemany('''INSERT INTO messages
            (id, account_id, group_id, user_id, sent_at, message_type, content_text)
            VALUES (?, 1, 11, 2, '2026-08-23 13:00:00', 'text', ?)''',
            [(100 + i, f'批量匹配-{i:03d}') for i in range(220)])
        export = self.view('export', {'members': [2], 'hours': [13]})
        self.assertEqual(export.text.count('批量匹配-'), 220)
        self.assertIn('批量匹配-000', export.text)
        self.assertIn('批量匹配-219', export.text)
        first = self.view('matches', query='批量匹配', limit=200).json()
        second = self.view('matches', query='批量匹配', limit=200, offset=200).json()
        ids = [m['id'] for m in first['items'] + second['items']]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(first['total'], 220)
        self.assertEqual(ids, list(range(319, 99, -1)))

    def test_deleted_view_facets_export_context_and_hard_delete_keep_scope(self):
        facets = self.view('facets', deleted_only=True).json()
        self.assertEqual(sum(facets['hours']), 1)
        exported = self.view('export', deleted_only=True).text
        self.assertIn('甲删除附件', exported)
        self.assertNotIn('乙删除附件', exported)
        response = self.client.get('/api/message-view/4/context?account_id=1&group_id=11&deleted_only=true')
        self.assertEqual([m['id'] for m in response.json()['items']], [4])
        filters = {'account_id': 1, 'group_id': 11, 'viewer': json.dumps({'members': [2]})}
        result = self.client.post('/api/messages/hard-delete', json={'filters': filters, 'confirm': True})
        self.assertEqual(result.json()['deleted_count'], 0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM messages WHERE is_deleted=1').fetchone()[0], 2)

    def test_context_is_bounded_stable_and_can_recenter(self):
        self.db.executemany('''INSERT INTO messages
            (id, account_id, group_id, user_id, sent_at, message_type, content_text)
            VALUES (?, 1, 11, 1, '2026-08-23 14:00:00', 'text', '相邻记录')''', [(i,) for i in range(100, 130)])
        result = self.client.get('/api/message-view/115/context?account_id=1&group_id=11').json()
        self.assertEqual([m['id'] for m in result['items']], list(range(120, 109, -1)))
        self.assertTrue(result['has_older'])
        self.assertTrue(result['has_newer'])
        next_window = self.client.get('/api/message-view/110/context?account_id=1&group_id=11').json()
        self.assertIn(105, [m['id'] for m in next_window['items']])

    def test_directional_context_pages_exclude_anchor_and_keep_scope(self):
        self.db.executemany('''INSERT INTO messages
            (id, account_id, group_id, user_id, sent_at, message_type, content_text)
            VALUES (?, 1, 11, 1, '2026-08-23 14:00:00', 'text', '翻页')''', [(i,) for i in range(100, 120)])
        older = self.client.get('/api/message-view/110/context?account_id=1&group_id=11&direction=older').json()
        self.assertEqual([m['id'] for m in older['items']], [109, 108, 107, 106, 105])
        newer = self.client.get('/api/message-view/109/context?account_id=1&group_id=11&direction=newer').json()
        self.assertEqual([m['id'] for m in newer['items']], [114, 113, 112, 111, 110])
        self.assertEqual(self.client.get('/api/message-view/110/context?account_id=2&group_id=11&direction=older').status_code, 404)
        self.assertEqual(self.client.get('/api/message-view/110/context?account_id=1&group_id=11&direction=bad').status_code, 422)

    def test_unified_media_filter_and_single_day_apply_together(self):
        result = self.client.get('/api/messages', params={'account_id': 1, 'group_id': 11,
            'message_type': 'link', 'viewer': json.dumps({'media': 'links'}),
            'date_from': '2026-08-23 00:00:00', 'date_to': '2026-08-23 23:59:59'}).json()
        self.assertEqual(result['total'], 2)

    def test_media_alias_does_not_narrow_base_facets_and_conflicts_are_rejected(self):
        filters = {'account_id': 1, 'group_id': 11, 'message_type': 'link',
                   'viewer': json.dumps({'media': 'links'})}
        response = self.client.post('/api/message-view/facets', json={'filters': filters})
        self.assertEqual(sum(response.json()['hours']), 4)
        self.assertEqual(len(response.json()['members']), 2)
        preview = self.client.post('/api/messages/delete-preview', json={'filters': filters})
        self.assertEqual(preview.json()['preview_count'], 2)
        filters['message_type'] = 'image'
        for endpoint in ['/api/message-view/facets', '/api/messages/delete-preview', '/api/messages/soft-delete']:
            self.assertEqual(self.client.post(endpoint, json={'filters': filters, 'confirm': True}).status_code, 422)
        self.assertEqual(self.listing()['total'], 4)

    def test_facets_respect_base_filters_and_safe_preview_urls(self):
        response = self.client.post('/api/message-view/facets', json={'filters': {'account_id': 2, 'group_id': 11}})
        self.assertEqual(response.json()['members'], [])
        self.assertEqual(sum(response.json()['hours']), 0)
        self.db.execute("UPDATE chat_users SET avatar_url='javascript:alert(1)' WHERE id=2")
        self.assertIsNone(self.view('facets').json()['members'][1]['avatar_url'])
        raw = {'url_objects': [{'url_ori': 'javascript:alert(1)', 'object': {'status': {'text': 'evil'}}},
                               {'url_ori': 'https://user:password@fixture.invalid', 'object': {}}]}
        self.db.execute('UPDATE messages SET raw_payload=? WHERE id=7', (json.dumps(raw),))
        cards = self.listing(members=[2], hours=[12])['items'][0]['link_previews']
        self.assertEqual([card['url'] for card in cards], ['https://weibo.com/123/Abc'])


    def test_join_notices_are_hidden_reversibly_but_substantive_messages_remain(self):
        notices = ['模拟成员甲加入了群', '模拟成员乙 加入了群', '模拟成员丙加入了群聊',
                   '  模拟成员丁 加入了群聊。  ']
        normal = ['模拟成员甲加入了群，大家欢迎他', '模拟成员乙加入了群聊以后分享了资料',
                  '今天讨论的内容\n模拟成员丙加入了群', '早安，今天讨论资料', '加入了群']
        self.db.executemany("""INSERT INTO messages
            (id, account_id, group_id, user_id, sent_at, message_type, content_text)
            VALUES (?, 1, 11, 2, '2026-08-23 13:00:00', 'text', ?)""",
            [(100 + i, text) for i, text in enumerate(notices + normal)])
        self.db.commit()
        before = list(self.db.iterdump())
        data = self.listing(hide_noise=True)
        self.assertEqual(data['total'], 3 + len(normal))
        self.assertEqual(self.view('facets', {'hide_noise': True}).json()['hidden_noise'], 1 + len(notices))
        exported = self.view('export', {'hide_noise': True}).text
        for notice in notices:
            self.assertNotIn('\n' + notice + '\n\n', exported)
        for text in normal:
            self.assertIn('\n' + text + '\n\n', exported)
        self.assertEqual(self.view('matches', {'hide_noise': True}, query='加入了群').json()['total'], 4)
        filters = {'account_id': 1, 'group_id': 11, 'viewer': json.dumps({'hide_noise': True})}
        preview = self.client.post('/api/messages/delete-preview', json={'filters': filters})
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json()['preview_count'], data['total'])
        self.assertEqual(self.listing()['total'], 4 + len(notices) + len(normal))
        self.assertEqual(self.view('facets').json()['hidden_noise'], 0)
        self.assertEqual(list(self.db.iterdump()), before)

    def test_join_noise_statistics_remain_account_group_and_deleted_scoped(self):
        self.db.executemany("""INSERT INTO messages
            (id, account_id, group_id, user_id, sent_at, message_type, content_text, is_deleted)
            VALUES (?, ?, ?, 1, '2026-08-23 13:00:00', 'text', '模拟成员加入了群', ?)""",
            [(100, 1, 11, 0), (101, 1, 12, 0), (102, 2, 21, 0), (103, 1, 11, 1)])
        self.db.commit()
        self.assertEqual(self.view('facets', {'hide_noise': True}).json()['hidden_noise'], 2)
        self.assertEqual(self.view('facets', {'hide_noise': True}, deleted_only=True).json()['hidden_noise'], 1)
        self.assertEqual(self.listing(hide_noise=True)['total'], 3)
        self.assertEqual(self.listing(hide_noise=True)['attachment_summary'], self.listing()['attachment_summary'])
        self.assertNotIn('模拟成员加入了群', self.view('export', {'hide_noise': True}, deleted_only=True).text)

if __name__ == '__main__':
    unittest.main()
