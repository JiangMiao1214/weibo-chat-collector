"""Fake-IP compatibility with simulated DNS, sockets and TLS only."""
import ssl
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import test_archives_http as fixtures
from app.services.archive_network import destination, open_source, PinnedHTTPS
from app.services.archive_storage import ArchiveError


def dns(*addresses):
    return [(0, 0, 0, '', (address, 443)) for address in addresses]


class ArchiveNetworkTests(unittest.TestCase):
    def test_observed_wrapper_expression_cover_and_article_hosts_accept_fake_ip(self):
        for host in ('weibo.cn', 'face.t.sinajs.cn', 'i2.hdslb.com', 'gaoshanwen.com'):
            with self.subTest(host=host), patch('socket.getaddrinfo', return_value=dns('198.18.0.60')):
                self.assertEqual(destination(f'https://{host}/fixture')[1:], (host, '198.18.0.60'))
        for host in ('weibo.cn.evil.invalid', 'evilgaoshanwen.com', 'evilhdslb.com', 'sinajs.cn.evil.invalid'):
            with self.subTest(host=host), patch('socket.getaddrinfo', return_value=dns('198.18.0.60')):
                with self.assertRaises(ArchiveError):
                    destination(f'https://{host}/fixture')

    def test_old_http_short_link_upgrades_before_network_and_strips_cookies_on_external_redirect(self):
        first, second = MagicMock(), MagicMock()
        first.getresponse.return_value = SimpleNamespace(status=302, headers={'Location': 'https://gaoshanwen.com/fixture'})
        second.getresponse.return_value = SimpleNamespace(status=200, headers={})
        with patch('socket.getaddrinfo', return_value=dns('198.18.0.60')), \
             patch('app.services.archive_network.PinnedHTTPS', side_effect=[first, second]) as tls, \
             patch('app.services.archive_network.http.client.HTTPConnection') as cleartext, \
             patch('app.services.archive_network.CookieProfileStore._load') as cookies:
            with open_source('http://t.cn:80/fixture', 2, 'fake-auth') as (_, url):
                self.assertEqual(url, 'https://gaoshanwen.com/fixture')
            self.assertEqual([call.args[0] for call in tls.call_args_list], ['t.cn', 'gaoshanwen.com'])
            cleartext.assert_not_called()
            cookies.assert_not_called()
            self.assertNotIn('Cookie', second.request.call_args.kwargs['headers'])

    def test_sinaurl_unwraps_target_and_checks_private_target_before_sending_request(self):
        connection = MagicMock()
        connection.getresponse.return_value = SimpleNamespace(status=200, headers={})
        with patch('socket.getaddrinfo', return_value=dns('198.18.0.60')), \
             patch('app.services.archive_network.PinnedHTTPS', return_value=connection) as tls, \
             patch('app.services.archive_network.CookieProfileStore._load') as cookies:
            with open_source('https://weibo.cn/sinaurl?u=https%3A%2F%2Fgaoshanwen.com%2Ffixture%3Ftheme%3Dlight', 2, 'fake-auth') as (_, url):
                self.assertEqual(url, 'https://gaoshanwen.com/fixture?theme=light')
            tls.assert_called_once_with('gaoshanwen.com', '198.18.0.60')
            cookies.assert_not_called()
        with patch('socket.getaddrinfo', return_value=dns('127.0.0.1')), \
             patch('app.services.archive_network.PinnedHTTPS') as tls:
            with self.assertRaises(ArchiveError):
                with open_source('https://weibo.cn/sinaurl?u=https%3A%2F%2F127.0.0.1%2Fprivate', 2, 'fake-auth'):
                    self.fail('private target must not be requested')
            tls.assert_not_called()

    def test_bad_wrapper_and_nested_target_cannot_bypass_url_checks(self):
        from urllib.parse import quote
        targets = ['file:///private/fixture', 'https://FAKE:SECRET@weibo.com/123/Abc',
                   'https://weibo.com:8443/123/Abc']
        nested = 'https://weibo.com/123/Abc'
        for _ in range(6):
            nested = 'https://weibo.cn/sinaurl?u=' + quote(nested, safe='')
        urls = ['https://weibo.cn/sinaurl', 'https://weibo.cn/sinaurl?u=https://weibo.com&u=https://t.cn', nested]
        urls += ['https://weibo.cn/sinaurl?u=' + quote(target, safe='') for target in targets]
        with patch('app.services.archive_network.PinnedHTTPS') as tls, patch('socket.getaddrinfo') as resolve:
            for url in urls:
                with self.subTest(url=url), self.assertRaises(ArchiveError):
                    with open_source(url, 1, 'fake-auth'):
                        self.fail('invalid wrapper must not connect')
            tls.assert_not_called()
            resolve.assert_not_called()

    def test_official_https_hosts_accept_proxy_fake_ip_without_changing_hostname(self):
        for host in ('weibo.com', 'www.weibo.com', 'm.weibo.cn', 'upload.api.weibo.com',
                     'tvax1.sinaimg.cn', 'h5.sinaimg.cn', 'wx2.sinaimg.com', 't.cn'):
            for address in ('198.18.0.59', '198.19.255.254'):
                with self.subTest(host=host, address=address), patch('socket.getaddrinfo', return_value=dns(address)):
                    parsed, actual_host, actual_address = destination(f'https://{host}/fixture')
                    self.assertEqual((parsed.scheme, actual_host, actual_address), ('https', host, address))

    def test_fake_ip_exception_does_not_allow_http_literals_lookalikes_or_private_dns(self):
        for url in ('http://weibo.com/fixture', 'https://198.18.0.59/fixture',
                    'https://weibo.com.evil.invalid/fixture', 'https://evilsinaimg.cn/fixture',
                    'https://fixture.invalid/fixture', 'https://weibo.com:8443/fixture',
                    'https://FAKE:SECRET@weibo.com/fixture'):
            with self.subTest(url=url), patch('socket.getaddrinfo', return_value=dns('198.18.0.59')):
                with self.assertRaises(ArchiveError):
                    destination(url)
        for addresses in (('127.0.0.1',), ('10.0.0.1',), ('169.254.169.254',), ('::1',),
                          ('198.18.0.59', '192.168.1.1'), ('198.20.0.1', '10.0.0.1')):
            with self.subTest(addresses=addresses), patch('socket.getaddrinfo', return_value=dns(*addresses)):
                with self.assertRaises(ArchiveError):
                    destination('https://weibo.com/fixture')

    def test_dns_rejection_explains_fake_ip_separately_from_invalid_port(self):
        with patch('socket.getaddrinfo', return_value=dns('198.18.0.59')):
            with self.assertRaisesRegex(ArchiveError, 'Fake-IP'):
                destination('https://fixture.invalid/fixture')
        with patch('socket.getaddrinfo', return_value=dns('10.0.0.1')):
            with self.assertRaisesRegex(ArchiveError, '非公网'):
                destination('https://weibo.com/fixture')
        with self.assertRaisesRegex(ArchiveError, '标准端口'):
            destination('https://weibo.com:8443/fixture')

    def test_fake_ip_transport_preserves_host_and_rechecks_redirect_before_connecting(self):
        connection = MagicMock()
        connection.getresponse.return_value = SimpleNamespace(status=302, headers={'Location': 'https://127.0.0.1/private'})
        with patch('socket.getaddrinfo', side_effect=[dns('198.18.0.59'), dns('127.0.0.1')]), \
             patch('app.services.archive_network.PinnedHTTPS', return_value=connection) as transport, \
             patch('app.services.archive_network.CookieProfileStore._load') as cookies:
            with self.assertRaisesRegex(ArchiveError, '非公网'):
                with open_source('https://tvax1.sinaimg.cn/fixture', 1, 'unused-fake-auth'):
                    self.fail('must not follow a private redirect')
            transport.assert_called_once_with('tvax1.sinaimg.cn', '198.18.0.59')
            headers = connection.request.call_args.kwargs['headers']
            self.assertEqual(headers['Host'], 'tvax1.sinaimg.cn')
            self.assertNotIn('Cookie', headers)
            cookies.assert_not_called()

    def test_pinned_tls_checks_original_hostname_and_closes_socket_on_invalid_certificate(self):
        raw = MagicMock()
        context = ssl.create_default_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        with patch('socket.create_connection', return_value=raw) as connect, \
             patch('ssl.create_default_context', return_value=context), \
             patch.object(context, 'wrap_socket', side_effect=ssl.SSLCertVerificationError('fake certificate')) as wrap:
            with self.assertRaises(ssl.SSLCertVerificationError):
                PinnedHTTPS('tvax1.sinaimg.cn', '198.18.0.59').connect()
            connect.assert_called_once_with(('198.18.0.59', 443), timeout=30)
            wrap.assert_called_once_with(raw, server_hostname='tvax1.sinaimg.cn')
            raw.close.assert_called_once()

    def test_explicit_proxy_trust_is_exact_https_and_still_rejects_private_dns(self):
        from app.services.archive_policy import trust_hosts
        with patch('socket.getaddrinfo', return_value=dns('198.18.0.3')):
            with trust_hosts(['news.fixture.invalid']):
                self.assertEqual(destination('https://news.fixture.invalid/article')[1], 'news.fixture.invalid')
                for url in ('http://news.fixture.invalid/article', 'https://other.fixture.invalid/article', 'https://news.fixture.invalid.evil/article'):
                    with self.assertRaises(ArchiveError):
                        destination(url)
                with patch('socket.getaddrinfo', return_value=dns('198.18.0.3', '127.0.0.1')):
                    with self.assertRaises(ArchiveError):
                        destination('https://news.fixture.invalid/article')
            with self.assertRaises(ArchiveError):
                destination('https://news.fixture.invalid/article')
