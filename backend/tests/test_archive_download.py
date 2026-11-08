"""Transport/parser/storage regressions using fake responses and temp files only."""
from contextlib import contextmanager
import io
import json
from types import SimpleNamespace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from urllib.parse import urlsplit

import test_archives_http as fixtures
from app.services.archive_storage import ArchiveError, save_stream
from app.services.archive_download import download, SNAPSHOT_MIME
from app.services.archive_network import destination, open_source, request_headers
from app.services.archive_pages import weibo_snapshot

PNG = b'\x89PNG\r\n\x1a\n' + b'fixture-image'


class ArchiveDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = SimpleNamespace(attachments_dir=Path(self.temp.name), weibo_api_auth_dir=Path(self.temp.name) / 'fake-auth')

    @contextmanager
    def source(self, body, mime, length=None):
        response = io.BytesIO(body)
        response.headers = {'Content-Type': mime, 'Content-Length': str(len(body) if length is None else length)}
        with patch('app.services.archive_download.open_source') as opener:
            opener.return_value.__enter__.return_value = (response, 'https://fixture.invalid/object')
            yield response

    def job(self, kind):
        return {'kind': kind, 'account_id': 1, 'source_url': 'https://fixture.invalid/object'}

    def test_media_originals_preserve_bytes_and_reject_login_pages_truncation_and_fake_images(self):
        for kind, mime, body in [('image', 'image/png', PNG), ('file', 'application/pdf', b'%PDF-1.7\nfixture'),
                                 ('video', 'video/mp4', b'\0\0\0\x18ftypisomfixture')]:
            with self.subTest(kind=kind), self.source(body, mime):
                result, resources = download(self.job(kind), self.settings)
                self.assertEqual((self.settings.attachments_dir / result['local_path']).read_bytes(), body)
                self.assertEqual(resources, [])
        for mime, body, length in [('text/html', b'<html>login</html>', None),
                                    ('image/png', PNG, 999), ('image/png', b'not an image', None)]:
            with self.subTest(mime=mime, length=length), self.source(body, mime, length):
                with self.assertRaises(ArchiveError):
                    download(self.job('image'), self.settings)
        self.assertEqual(list(self.settings.attachments_dir.rglob('.part-*')), [])

    def test_article_text_and_media_are_saved_without_scripts_or_live_html(self):
        body = ('<h1>模拟标题</h1><p>' + '模拟正文用于离线读取。' * 8 + '</p>'
                '<script>secretScript()</script><img src="photo.png"><video src="video.mp4"></video>'
                '<a href="file.pdf">附件</a>').encode()
        with self.source(body, 'text/html'):
            result, resources = download(self.job('link'), self.settings)
        self.assertEqual(result['mime_type'], SNAPSHOT_MIME)
        snapshot = json.loads((self.settings.attachments_dir / result['local_path']).read_text())
        self.assertIn('模拟正文', snapshot['text'])
        self.assertNotIn('secretScript', snapshot['text'])
        self.assertEqual({r['kind'] for r in resources}, {'image', 'video', 'file'})

    def test_media_size_limits_accept_boundary_and_reject_one_extra_byte(self):
        cases = [('image', 'image/png', PNG, 10), ('audio', 'audio/mpeg', b'ID3', 10),
                 ('video', 'video/mp4', b'\0\0\0\x18ftypisom', 20), ('file', 'application/pdf', b'%PDF-', 20),
                 ('link', 'image/png', PNG, 10), ('link', 'audio/mpeg', b'ID3', 10),
                 ('link', 'video/mp4', b'\0\0\0\x18ftypisom', 20), ('link', 'application/pdf', b'%PDF-', 20),
                 ('file', 'image/png', PNG, 10), ('file', 'audio/mpeg', b'ID3', 10)]
        for kind, mime, prefix, mib in cases:
            body = prefix + b'x' * (mib * 1024**2 - len(prefix))
            with self.subTest(kind=kind, mime=mime, boundary='exact'), self.source(body, mime):
                result, _ = download(self.job(kind), self.settings)
                self.assertEqual(result['file_size'], len(body))
            before = {p for p in self.settings.attachments_dir.rglob('*') if p.is_file()}
            with self.subTest(kind=kind, mime=mime, boundary='exceeded'), self.source(body + b'x', mime):
                with self.assertRaisesRegex(ArchiveError, '超过大小'):
                    download(self.job(kind), self.settings)
                self.assertEqual({p for p in self.settings.attachments_dir.rglob('*') if p.is_file()}, before)

    def test_html_size_limit_is_two_mib_including_without_content_length(self):
        body = b'<p>' + b'x' * (2 * 1024**2 - 7) + b'</p>'
        with self.source(body, 'text/html'):
            result, _ = download(self.job('link'), self.settings)
            self.assertEqual(result['mime_type'], SNAPSHOT_MIME)
        before = {p for p in self.settings.attachments_dir.rglob('*') if p.is_file()}
        for has_length in (True, False):
            with self.subTest(has_length=has_length), self.source(body + b'x', 'text/html') as response:
                if not has_length:
                    response.headers.pop('Content-Length')
                with self.assertRaisesRegex(ArchiveError, '超过大小'):
                    download(self.job('link'), self.settings)
                self.assertEqual({p for p in self.settings.attachments_dir.rglob('*') if p.is_file()}, before)

    def test_weibo_full_text_original_media_and_long_retweet_are_saved(self):
        base = {'id': 123, 'isLongText': True, 'user': {'screen_name': '模拟作者'},
                'pic_infos': {'p': {'largest': {'url': 'https://fixture.invalid/large.png'}}},
                'page_info': {'media_info': {'stream_url_hd': 'https://fixture.invalid/hd.mp4'}},
                'retweeted_status': {'id': 456, 'isLongText': True}}
        with patch('app.services.archive_pages.get_json', side_effect=[base,
                {'data': {'longTextContent': '完整微博正文'}}, {'data': {'longTextContent': '完整转发正文'}}]):
            snapshot = weibo_snapshot('https://weibo.com/123/Abc', 1, self.settings.weibo_api_auth_dir, lambda: None)
        self.assertIn('完整微博正文', snapshot['text'])
        self.assertIn('完整转发正文', snapshot['text'])
        self.assertEqual(len(snapshot['resources']), 2)

    def test_profile_link_saves_labeled_profile_snapshot_instead_of_dynamic_html_shell(self):
        job = {**self.job('link'), 'source_url': 'https://weibo.com/u/123'}
        payload = {'ok': 1, 'data': {'user': {'id': 123, 'screen_name': '模拟用户',
                   'description': '<b>模拟简介</b>', 'location': '模拟地区', 'followers_count': 12,
                   'avatar_hd': 'https://tvax1.sinaimg.cn/fixture.png'}}}
        with patch('app.services.archive_pages.get_json', return_value=payload) as api, \
             patch('app.services.archive_download.open_source', side_effect=AssertionError('no dynamic HTML')):
            result, resources = download(job, self.settings)
        snapshot = json.loads((self.settings.attachments_dir / result['local_path']).read_text())
        self.assertIn('个人主页资料快照', snapshot['title'])
        self.assertIn('不含该用户全部微博', snapshot['text'])
        self.assertIn('模拟简介', snapshot['text'])
        self.assertNotIn('<b>', snapshot['text'])
        self.assertEqual(resources, [{'url': 'https://tvax1.sinaimg.cn/fixture.png', 'kind': 'image'}])
        api.assert_called_once()
        self.assertEqual(api.call_args.args[:3], ('https://weibo.com/ajax/profile/info?uid=123', 1, self.settings.weibo_api_auth_dir))

    def test_profile_invalid_or_wrong_user_response_is_not_saved(self):
        job = {**self.job('link'), 'source_url': 'https://weibo.com/u/123'}
        for payload in ({'ok': 0}, {'data': {'user': {'id': 456, 'screen_name': '错误用户'}}},
                        {'data': {'user': {}}}, {'data': []}):
            with self.subTest(payload=payload), patch('app.services.archive_pages.get_json', return_value=payload), \
                 patch('app.services.archive_download.open_source', side_effect=AssertionError('no dynamic HTML')):
                with self.assertRaisesRegex(ArchiveError, '个人主页'):
                    download(job, self.settings)
        self.assertEqual(list(self.settings.attachments_dir.rglob('*')), [])

    def test_wrapped_http_weibo_permalink_uses_article_api_with_own_account(self):
        job = {**self.job('link'), 'account_id': 2,
               'source_url': 'https://weibo.cn/sinaurl?u=http%3A%2F%2Fweibo.com%2F123%2FAbc'}
        with patch('app.services.archive_pages.get_json', return_value={'id': 456, 'text_raw': '模拟微博正文'}) as api, \
             patch('app.services.archive_download.open_source', side_effect=AssertionError('no redirect landing page')):
            result, _ = download(job, self.settings)
        self.assertEqual(api.call_args.args[:2], ('https://weibo.com/ajax/statuses/show?id=Abc', 2))
        self.assertIn('模拟微博正文', (self.settings.attachments_dir / result['local_path']).read_text())

    def test_explicit_resource_denial_is_distinct_from_login_rate_limit_and_unknown_responses(self):
        for payload, skipped in [({'ok': 0, 'msg': '暂无查看权限'}, True),
                                 ({'error': '此微博已被删除'}, True),
                                 ({'message': '微博不存在'}, True),
                                 ({'error_code': 21301, 'msg': '暂无查看权限'}, False),
                                 ({'code': 429, 'msg': '暂无查看权限'}, False),
                                 ({'msg': '请先登录，暂无查看权限'}, False),
                                 ({'msg': '访问频次过高'}, False), ({'ok': 0}, False)]:
            with self.subTest(payload=payload), patch('app.services.archive_pages.get_json', return_value=payload):
                with self.assertRaises(ArchiveError) as failed:
                    weibo_snapshot('https://weibo.com/123/Abc', 1, self.settings.weibo_api_auth_dir, lambda: None)
                self.assertEqual(type(failed.exception).__name__, 'ArchiveSkipped' if skipped else 'ArchiveError')
                self.assertEqual(failed.exception.blocked, not skipped)
        self.assertEqual(list(self.settings.attachments_dir.rglob('*')), [])

    def test_expression_and_cover_download_through_fake_ip_preserve_original_bytes(self):
        for host in ('face.t.sinajs.cn', 'i2.hdslb.com'):
            job = {**self.job('image'), 'source_url': f'https://{host}/fixture.png'}
            response = io.BytesIO(PNG)
            response.status = 200
            response.headers = {'Content-Type': 'image/png', 'Content-Length': str(len(PNG))}
            connection = MagicMock()
            connection.getresponse.return_value = response
            with self.subTest(host=host), patch('socket.getaddrinfo', return_value=[(0, 0, 0, '', ('198.18.0.60', 443))]), \
                 patch('app.services.archive_network.PinnedHTTPS', return_value=connection), \
                 patch('app.services.archive_network.CookieProfileStore._load') as cookies:
                result, resources = download(job, self.settings)
                self.assertEqual((self.settings.attachments_dir / result['local_path']).read_bytes(), PNG)
                self.assertEqual(resources, [])
                cookies.assert_not_called()

    def test_public_dns_only_and_cookies_are_scoped_to_weibo_https(self):
        for ip in ('127.0.0.1', '10.0.0.1', '169.254.169.254', '::1'):
            with patch('socket.getaddrinfo', return_value=[(0, 0, 0, '', (ip, 443))]):
                with self.assertRaises(ArchiveError):
                    destination('https://fixture.invalid/file')
        with patch('app.services.archive_network.CookieProfileStore._load', return_value=[
                {'name': 'SUB', 'value': 'FAKE', 'domain': '.weibo.com', 'path': '/'},
                {'name': 'OTHER', 'value': 'FAKE', 'domain': '.weibo.com', 'path': '/private'}]) as cookies:
            own = request_headers(urlsplit('https://weibo.com/ajax/statuses/show'), 7, self.settings.weibo_api_auth_dir)
            self.assertEqual(own['Cookie'], 'SUB=FAKE')
            cookies.assert_called_once_with(7)
            for url in ('https://wx1.sinaimg.cn/x', 'https://weibo.com.evil.invalid/x', 'http://weibo.com/x'):
                self.assertNotIn('Cookie', request_headers(urlsplit(url), 7, self.settings.weibo_api_auth_dir))

    def test_redirect_to_cdn_removes_cookie_and_network_failure_is_sanitized(self):
        def resolved(url):
            parsed = urlsplit(url)
            return parsed, parsed.hostname, '93.184.216.34'
        redirect = SimpleNamespace(status=302, headers={'Location': 'https://wx1.sinaimg.cn/x'})
        response = SimpleNamespace(status=200, headers={})
        connections = [MagicMock(), MagicMock()]
        connections[0].getresponse.return_value = redirect
        connections[1].getresponse.return_value = response
        with patch('app.services.archive_network.destination', side_effect=resolved), \
             patch('app.services.archive_network.PinnedHTTPS', side_effect=connections), \
             patch('app.services.archive_network.CookieProfileStore._load', return_value=[
                 {'name': 'SUB', 'value': 'FAKE', 'domain': '.weibo.com', 'path': '/'}]):
            with open_source('https://weibo.com/photo', 1, self.settings.weibo_api_auth_dir) as (result, url):
                self.assertEqual(url, 'https://wx1.sinaimg.cn/x')
            self.assertIn('Cookie', connections[0].request.call_args.kwargs['headers'])
            self.assertNotIn('Cookie', connections[1].request.call_args.kwargs['headers'])
        with patch('app.services.archive_network.destination', side_effect=OSError('SECRET')):
            with self.assertRaisesRegex(ArchiveError, '网络读取失败'):
                with open_source('https://fixture.invalid/x', 1, self.settings.weibo_api_auth_dir):
                    self.fail('network failure must not yield')

    def test_disk_full_and_midstream_failure_leave_no_partial_file(self):
        with patch('shutil.disk_usage', return_value=SimpleNamespace(free=1)):
            with self.assertRaises(ArchiveError) as failed:
                save_stream(self.settings.attachments_dir, 1, [PNG], 'image/png')
        self.assertTrue(failed.exception.blocked)
        self.assertEqual(list(self.settings.attachments_dir.rglob('.part-*')), [])

    def test_interrupted_media_resumes_with_range_and_preserves_exact_bytes(self):
        body = PNG + b'x' * 300000
        job = {**self.job('image'), 'id': 77}
        calls = []
        class Broken(io.BytesIO):
            def read(self, size=-1):
                if self.tell():
                    raise ArchiveError('temporary offline', retryable=True)
                return super().read(16)
        @contextmanager
        def network(url, account, auth, check, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                response = Broken(body)
                response.status = 200
                response.headers = {'Content-Type': 'image/png', 'Content-Length': str(len(body)), 'ETag': '"fixture-v1"'}
            else:
                response = io.BytesIO(body[16:])
                response.status = 206
                response.headers = {'Content-Type': 'image/png', 'Content-Length': str(len(body)-16),
                                    'ETag': '"fixture-v1"', 'Content-Range': f'bytes 16-{len(body)-1}/{len(body)}'}
            yield response, url
        with patch('app.services.archive_download.open_source', side_effect=network):
            with self.assertRaises(ArchiveError):
                download(job, self.settings)
            result, _ = download(job, self.settings)
        self.assertEqual(calls[1].get('extra_headers', {}).get('Range'), 'bytes=16-')
        self.assertEqual((self.settings.attachments_dir / result['local_path']).read_bytes(), body)
        self.assertEqual([p for p in self.settings.attachments_dir.rglob('.resume-*') if p.name != '.resume-locks'], [])

    def test_unencrypted_vod_hls_saves_complete_transport_stream_with_total_limit(self):
        playlist = b'#EXTM3U\n#EXT-X-TARGETDURATION:5\n#EXTINF:5,\none.ts\n#EXTINF:5,\ntwo.ts\n#EXT-X-ENDLIST\n'
        segment = b'\x47' + b'x' * 187
        @contextmanager
        def network(url, *_args, **_kwargs):
            data = segment if url.endswith('.ts') else playlist
            response = io.BytesIO(data)
            response.headers = {'Content-Type': 'video/mp2t' if url.endswith('.ts') else 'application/vnd.apple.mpegurl', 'Content-Length': str(len(data))}
            yield response, url
        with patch('app.services.archive_download.open_source', side_effect=network):
            result, resources = download(self.job('video'), self.settings)
        self.assertEqual((self.settings.attachments_dir / result['local_path']).read_bytes(), segment * 2)
        self.assertEqual(result['mime_type'], 'video/mp2t')
        self.assertEqual(resources, [])
        with patch('app.services.archive_download.open_source', side_effect=network), \
             patch.dict('app.services.archive_download.LIMITS', {'video': 200}):
            with self.assertRaisesRegex(ArchiveError, '超过大小'):
                download(self.job('video'), self.settings)

    def test_hls_encryption_and_live_streams_never_mark_saved(self):
        for playlist in (b'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key"\n#EXTINF:5,\na.ts\n#EXT-X-ENDLIST',
                         b'#EXTM3U\n#EXTINF:5,\na.ts\n'):
            with self.source(playlist, 'application/vnd.apple.mpegurl'):
                with self.assertRaises(ArchiveError):
                    download(self.job('video'), self.settings)
        self.assertEqual(list(self.settings.attachments_dir.rglob('*')), [])

    def test_resume_ignored_by_server_restarts_instead_of_appending(self):
        from app.services.archive_resume import resume_state
        job = {**self.job('image'), 'id': 99}
        state = resume_state(job, self.settings.attachments_dir)
        state['data'].write_bytes(PNG + b'old')
        (state['directory'] / 'meta.json').write_text(json.dumps({'url': job['source_url'], 'validator': '"old"', 'final_url': job['source_url']}))
        with self.source(PNG + b'new', 'image/png'):
            result, _ = download(job, self.settings)
        self.assertEqual((self.settings.attachments_dir / result['local_path']).read_bytes(), PNG + b'new')

    def test_changed_resume_validator_discards_partial_and_never_publishes(self):
        from app.services.archive_resume import resume_state
        job = {**self.job('image'), 'id': 99}
        state = resume_state(job, self.settings.attachments_dir)
        state['data'].write_bytes(PNG)
        (state['directory'] / 'meta.json').write_text(json.dumps({'url': job['source_url'], 'validator': '"old"', 'final_url': job['source_url']}))
        with self.source(b'changed', 'image/png') as response:
            response.status = 206
            response.headers.update({'ETag': '"new"', 'Content-Range': f'bytes {len(PNG)}-{len(PNG)+6}/{len(PNG)+7}'})
            with self.assertRaisesRegex(ArchiveError, '变化'):
                download(job, self.settings)
        self.assertFalse(state['directory'].exists())
        self.assertEqual([p for p in self.settings.attachments_dir.rglob('*') if p.is_file() and '.resume-locks' not in p.parts], [])

    def test_two_workers_cannot_write_same_resumable_file_at_once(self):
        import threading
        job = {**self.job('image'), 'id': 105}
        started, release = threading.Event(), threading.Event()
        errors = []
        @contextmanager
        def network(url, *_args, **_kwargs):
            started.set()
            release.wait(2)
            response = io.BytesIO(PNG)
            response.headers = {'Content-Type': 'image/png', 'Content-Length': str(len(PNG))}
            yield response, url
        def run():
            try:
                download(job, self.settings)
            except Exception as error:
                errors.append(error)
        with patch('app.services.archive_download.open_source', side_effect=network):
            thread = threading.Thread(target=run)
            thread.start()
            self.assertTrue(started.wait(2))
            try:
                with self.assertRaisesRegex(ArchiveError, '占用'):
                    download(job, self.settings)
            finally:
                release.set()
                thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
