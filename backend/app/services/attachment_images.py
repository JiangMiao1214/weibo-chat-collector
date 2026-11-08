"""Bounded, on-demand access to saved Weibo image URLs; never cache credentials."""
import re
import urllib.request
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urljoin

from fastapi import HTTPException

from ..collectors.weibo_api import CookieProfileStore, CookieProfileError

MAX_IMAGE_BYTES = 20 * 1024 * 1024
IMAGE_TYPES = {'image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/avif'}


class NoImageRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def image_source(url: str):
    try:
        parsed = urlsplit(url)
        valid_host = (re.fullmatch(r'(?:wx|ww|ws|tvax|tva)\d+\.sinaimg\.(?:cn|com)', parsed.hostname or '')
                      or (parsed.hostname == 'upload.api.weibo.com' and parsed.path == '/2/mss/msget'))
        if (parsed.scheme != 'https' or not valid_host or parsed.username or parsed.password
                or parsed.port not in (None, 443)):
            raise ValueError('unsupported source')
    except ValueError:
        raise HTTPException(400, '此图片地址暂不支持安全读取') from None
    return parsed


def image_headers(parsed, account_id, auth_dir):
    headers = {'Referer': 'https://weibo.com/', 'User-Agent': 'Mozilla/5.0', 'Accept': 'image/*'}
    if parsed.hostname == 'upload.api.weibo.com':
        store = CookieProfileStore(auth_dir)
        try:
            cookies = store._cookies_for_request(store._load(account_id), host=parsed.hostname,
                                                request_path=parsed.path)
        except CookieProfileError:
            raise HTTPException(401, '请重新登录此图片所属账号后再试') from None
        headers['Cookie'] = '; '.join(f"{cookie['name']}={cookie['value']}" for cookie in cookies)
    return headers


def read_image(response):
    mime = response.headers.get('Content-Type', '').split(';')[0].strip().lower()
    if mime not in IMAGE_TYPES:
        raise HTTPException(502, '图片源未返回可显示的图片，地址可能已失效')
    content = response.read(MAX_IMAGE_BYTES + 1)
    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(413, '图片超过 20 MB 读取上限')
    return content, mime


def fetch_attachment_image(url, account_id, auth_dir):
    opener = urllib.request.build_opener(NoImageRedirect())
    for _ in range(4):
        parsed = image_source(url)
        request = urllib.request.Request(url, headers=image_headers(parsed, account_id, auth_dir))
        try:
            with opener.open(request, timeout=15) as response:
                return read_image(response)
        except HTTPError as error:
            location = error.headers.get('Location')
            error.close()
            if error.code in (301, 302, 303, 307, 308) and location:
                url = urljoin(url, location)
                continue  # Revalidate destination and rebuild scoped headers on every hop.
            if error.code in (401, 403):
                raise HTTPException(403, '微博拒绝读取图片，请重新登录所属账号；原地址也可能已失效') from None
            raise HTTPException(502, '图片源暂时不可用或地址已失效') from None
        except (URLError, TimeoutError, OSError):
            raise HTTPException(502, '读取图片超时或网络不可用，请稍后重试') from None
    raise HTTPException(502, '图片源跳转次数过多')
