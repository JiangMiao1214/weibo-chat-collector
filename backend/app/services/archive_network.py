"""Bounded public-URL transport with pinned DNS and per-hop Cookie scoping."""
from contextlib import contextmanager
import http.client
import ipaddress
import socket
import ssl
import time
from urllib.parse import urljoin

from ..collectors.weibo_api import CookieProfileStore, CookieProfileError
from .archive_storage import ArchiveError
from .web_urls import safe_url
from .archive_urls import compatible_https_host, normalize_source, source_parts

FAKE_IP_RANGE = ipaddress.ip_network('198.18.0.0/15')


def validate_addresses(parsed, host, addresses):
    if not addresses:
        raise ArchiveError('DNS 未返回可用地址，请检查网络设置', retryable=True)
    non_public = [ipaddress.ip_address(address) for address in addresses if not ipaddress.ip_address(address).is_global]
    if not non_public:
        return
    if not all(address in FAKE_IP_RANGE for address in non_public):
        raise ArchiveError('域名解析到非公网地址，已拒绝连接；请检查 DNS 或代理设置')
    if parsed.scheme != 'https' or not compatible_https_host(host):
        raise ArchiveError('DNS 返回代理 Fake-IP；此地址不在 HTTPS 兼容范围，请将该域名设为真实 DNS 解析后重试')
    # Only supported HTTPS origins may use the local proxy mapping. PinnedHTTPS still
    # verifies the original hostname/certificate before sending any request or Cookie.


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address):
        super().__init__(host, timeout=30)
        self.address = address

    def connect(self):
        raw = socket.create_connection((self.address, 443), timeout=self.timeout)
        try:
            self.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def destination(url):
    try:
        parsed = source_parts(url)
        host = parsed.hostname.encode('idna').decode('ascii')
        addresses = {item[4][0] for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}
        validate_addresses(parsed, host, addresses)
    except (ValueError, UnicodeError):
        raise ArchiveError('不支持此地址：仅允许公网 HTTP(S) 标准端口') from None
    return parsed, host, sorted(addresses)[0]


def request_headers(parsed, account_id, auth_dir):
    headers = {'User-Agent': 'Mozilla/5.0', 'Accept-Encoding': 'identity', 'Referer': 'https://weibo.com/'}
    if parsed.scheme == 'https' and parsed.hostname in ('weibo.com', 'www.weibo.com', 'm.weibo.cn', 'upload.api.weibo.com'):
        store = CookieProfileStore(auth_dir)
        try:
            cookies = store._cookies_for_request(store._load(account_id), host=parsed.hostname, request_path=parsed.path or '/')
        except CookieProfileError:
            raise ArchiveError('微博登录失效，请重新登录后恢复归档', blocked=True) from None
        headers['Cookie'] = '; '.join(f"{c['name']}={c['value']}" for c in cookies)
    return headers


@contextmanager
def open_source(url, account_id, auth_dir, check=lambda: None, extra_headers=None):
    connection = None
    try:
        for _ in range(5):
            check()
            url = normalize_source(url)
            parsed, host, address = destination(url)
            connection = PinnedHTTPS(host, address) if parsed.scheme == 'https' else http.client.HTTPConnection(address, timeout=30)
            headers = request_headers(parsed, account_id, auth_dir)
            headers['Host'] = host
            if extra_headers:
                headers.update({key: value for key, value in extra_headers.items() if key in ('Range', 'If-Range')})
            path = parsed.path or '/'
            if parsed.query:
                path += '?' + parsed.query
            connection.request('GET', path, headers=headers)
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.headers.get('Location')
                connection.close()
                if not location:
                    raise ArchiveError('源站跳转地址缺失')
                url = urljoin(url, location)
                continue
            if response.status == 416 and extra_headers:
                error = ArchiveError('续传范围失效，将重新下载', retryable=True)
                error.restart_download = True
                raise error
            if response.status in (401, 403, 429):
                raise ArchiveError('源站拒绝访问或限流，请检查登录与权限后手动恢复', blocked=True)
            if response.status not in ((200, 206) if extra_headers else (200,)):
                raise ArchiveError(f'源站返回 HTTP {response.status}', retryable=response.status >= 500)
            yield response, url
            return
        raise ArchiveError('源站跳转次数超过上限')
    except ArchiveError as error:
        if not getattr(error, 'failure_url', None):
            error.failure_url = safe_url(url)
        raise
    except (OSError, http.client.HTTPException):
        raise ArchiveError('网络读取失败，可稍后重试', retryable=True) from None
    finally:
        if connection:
            connection.close()


def response_chunks(response, limit, check=lambda: None):
    start, total = time.monotonic(), 0
    while True:
        check()
        chunk = response.read(256 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit or time.monotonic() - start > 1800:
            raise ArchiveError('文件超过大小或单次下载时间上限')
        yield chunk
    expected = response.headers.get('Content-Length')
    if expected and total != int(expected):
        raise ArchiveError('文件接收不完整，尚未保存', retryable=True)
