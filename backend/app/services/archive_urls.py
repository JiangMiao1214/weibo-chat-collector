"""Normalize known legacy URLs before choosing an archive adapter or transport."""
from urllib.parse import parse_qs, urlsplit

from .archive_storage import ArchiveError
from .archive_policy import TRUSTED_HOSTS

# Exact additions correspond to supported redirect, expression, cover and article origins.
FAKE_IP_HTTPS_HOSTS = {'weibo.com', 'www.weibo.com', 'm.weibo.cn', 'upload.api.weibo.com',
                       't.cn', 'weibo.cn', 'face.t.sinajs.cn', 'i2.hdslb.com', 'gaoshanwen.com'}


def compatible_https_host(host):
    return host in TRUSTED_HOSTS.get() or host in FAKE_IP_HTTPS_HOSTS or host.endswith(('.sinaimg.cn', '.sinaimg.com'))


def source_parts(url):
    try:
        parsed = urlsplit(url)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username
                or parsed.password or parsed.port not in (None, 80 if parsed.scheme == 'http' else 443)):
            raise ValueError()
        return parsed
    except (ValueError, UnicodeError):
        raise ArchiveError('不支持此地址：仅允许公网 HTTP(S) 标准端口') from None


def normalize_source(url):
    for _ in range(5):
        parsed = source_parts(url)
        if parsed.hostname == 'weibo.cn' and parsed.path.rstrip('/') == '/sinaurl':
            targets = parse_qs(parsed.query).get('u', [])
            if len(targets) != 1:
                raise ArchiveError('微博外链缺少唯一目标地址，无法归档')
            url = targets[0]
            continue
        if parsed.scheme == 'http' and compatible_https_host(parsed.hostname):
            parsed = parsed._replace(scheme='https', netloc=parsed.hostname)
        return parsed.geturl()
    raise ArchiveError('微博外链嵌套次数超过上限')
