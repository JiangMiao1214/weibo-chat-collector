"""Image access uses an isolated DB, fake Cookie profiles and mocked HTTP only."""
import io
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError

import test_messages_http as fixtures
from app.settings import get_settings
from app.main import app


class AttachmentImageTests(TestCase):
    setUp = fixtures.MessagesHttpTests.setUp
    restore_overrides = staticmethod(fixtures.MessagesHttpTests.restore_overrides)

    def prepare(self, url='https://upload.api.weibo.com/2/mss/msget?fid=fake'):
        self.db.execute('UPDATE attachments SET source_url=? WHERE id=1', (url,))
        self.db.commit()
        app.dependency_overrides[get_settings] = lambda: SimpleNamespace(weibo_api_auth_dir='/tmp/unused-fake-profiles')
        profile = patch('app.collectors.weibo_api.CookieProfileStore._load', return_value=[
            {'name': 'SUB', 'value': 'fake-account-1', 'domain': '.weibo.com', 'path': '/'},
            {'name': 'SECRET', 'value': 'wrong-path', 'domain': '.weibo.com', 'path': '/private'}])
        self.cookies = profile.start()
        self.addCleanup(profile.stop)
        transport = patch('urllib.request.build_opener')
        self.opener = transport.start().return_value
        self.addCleanup(transport.stop)
        image = MagicMock()
        image.headers = {'Content-Type': 'image/png'}
        image.read.return_value = b'\x89PNG\r\n\x1a\nfixture'
        self.opener.open.return_value.__enter__.return_value = image
        return image

    def get_image(self, **params):
        return self.client.get('/api/attachments/1/image', params={
            'account_id': 1, 'group_id': 11, 'deleted_only': False, **params})

    def test_image_request_uses_own_scoped_cookies_and_weibo_referer(self):
        self.prepare()
        response = self.get_image()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['content-type'], 'image/png')
        self.assertEqual(response.headers['cache-control'], 'no-store')
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.get_header('Referer'), 'https://weibo.com/')
        self.assertEqual(request.get_header('Cookie'), 'SUB=fake-account-1')
        self.cookies.assert_called_once_with(1)

    def test_scope_and_deleted_state_rejected_before_network_or_cookie_read(self):
        self.prepare()
        for params in ({'account_id': 2}, {'group_id': 12}, {'deleted_only': True}):
            self.assertEqual(self.get_image(**params).status_code, 404)
        self.cookies.assert_not_called()
        self.opener.open.assert_not_called()
        self.db.execute('UPDATE messages SET is_deleted=1 WHERE id=1')
        self.db.commit()
        self.assertEqual(self.get_image().status_code, 404)
        self.assertEqual(self.get_image(deleted_only=True).status_code, 200)

    def test_cdn_request_never_receives_account_cookie(self):
        self.prepare('https://wx1.sinaimg.cn/large/fake.jpg')
        self.assertEqual(self.get_image().status_code, 200)
        self.assertIsNone(self.opener.open.call_args.args[0].get_header('Cookie'))
        self.cookies.assert_not_called()

    def test_arbitrary_hosts_and_non_image_attachment_are_rejected(self):
        self.prepare()
        for url in ('http://127.0.0.1/image', 'https://evil.invalid/x',
                    'https://wx1.sinaimg.cn.evil.invalid/x', 'https://user:secret@wx1.sinaimg.cn/x',
                    'https://upload.api.weibo.com/other', 'https://wx1.sinaimg.cn:8443/x'):
            self.db.execute('UPDATE attachments SET source_url=? WHERE id=1', (url,))
            self.db.commit()
            self.assertEqual(self.get_image().status_code, 400, url)
        self.opener.open.assert_not_called()
        self.db.execute("UPDATE attachments SET attachment_type='link' WHERE id=1")
        self.db.commit()
        self.assertEqual(self.get_image().status_code, 404)

    def test_403_and_non_images_have_clear_errors_without_upstream_secrets(self):
        image = self.prepare()
        self.opener.open.side_effect = HTTPError('secret-url', 403, 'secret-cookie', {}, io.BytesIO(b'secret'))
        response = self.get_image()
        self.assertEqual(response.status_code, 403)
        self.assertIn('登录', response.json()['detail'])
        self.assertNotIn('secret', response.text)
        self.opener.open.side_effect = None
        image.headers = {'Content-Type': 'text/html'}
        self.assertEqual(self.get_image().status_code, 502)

    def test_redirect_handler_never_forwards_requests_automatically(self):
        self.prepare()
        self.assertEqual(self.get_image().status_code, 200)
        import urllib.request
        handler = urllib.request.build_opener.call_args.args[-1]
        self.assertIsNone(handler.redirect_request(None, None, 302, '', {}, 'https://evil.invalid/'))

    def test_redirect_to_cdn_drops_cookies_and_rejects_untrusted_destination(self):
        self.prepare()
        image_response = self.opener.open.return_value
        self.opener.open.side_effect = [HTTPError('fake', 302, '',
            {'Location': 'https://wx1.sinaimg.cn/large/fake.jpg'}, io.BytesIO()), image_response]
        self.assertEqual(self.get_image().status_code, 200)
        redirected = self.opener.open.call_args.args[0]
        self.assertIsNone(redirected.get_header('Cookie'))
        self.cookies.assert_called_once_with(1)
        self.opener.open.reset_mock()
        self.opener.open.side_effect = HTTPError('fake', 302, '',
            {'Location': 'https://127.0.0.1/private'}, io.BytesIO())
        self.assertEqual(self.get_image().status_code, 400)
        self.assertEqual(self.opener.open.call_count, 1)

    def test_missing_cookie_timeout_and_oversized_image_are_bounded(self):
        from app.collectors.weibo_api import CookieProfileError
        image = self.prepare()
        self.cookies.side_effect = CookieProfileError('secret-path')
        response = self.get_image()
        self.assertEqual(response.status_code, 401)
        self.assertNotIn('secret-path', response.text)
        self.opener.open.assert_not_called()
        self.cookies.side_effect = None
        self.opener.open.side_effect = TimeoutError('secret-url')
        self.assertEqual(self.get_image().status_code, 502)
        self.opener.open.side_effect = None
        image.read.return_value = b'x' * (20 * 1024 * 1024 + 1)
        self.assertEqual(self.get_image().status_code, 413)
        image.read.assert_called_once_with(20 * 1024 * 1024 + 1)
