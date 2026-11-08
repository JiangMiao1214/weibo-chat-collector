"""Extract inert offline article snapshots; remote scripts/HTML are never replayed."""
from html.parser import HTMLParser
import json
import re
from urllib.parse import urljoin, urlsplit, quote

from .archive_storage import ArchiveError
from .archive_outcomes import check_resource_denial
from .archive_network import open_source, response_chunks


class ArticleParser(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.parts, self.resources = base, [], []
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript', 'form'):
            self.ignored += 1
        if self.ignored:
            return
        attrs = dict(attrs)
        if tag in ('p', 'div', 'br', 'h1', 'h2', 'li'):
            self.parts.append('\n')
        if tag in ('img', 'video', 'source'):
            url = attrs.get('src') or attrs.get('data-src')
            if url:
                self.resources.append({'url': urljoin(self.base, url), 'kind': 'image' if tag == 'img' else 'video'})
        if tag == 'a' and re.search(r'\.(pdf|zip|docx?|xlsx?|pptx?)(?:\?|$)', attrs.get('href', ''), re.I):
            self.resources.append({'url': urljoin(self.base, attrs['href']), 'kind': 'file'})

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript', 'form') and self.ignored:
            self.ignored -= 1

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)


def plain_html(text):
    parser = ArticleParser('https://weibo.com/')
    parser.feed(text or '')
    return ''.join(parser.parts).strip()


def weibo_id(url):
    parsed = urlsplit(url)
    if parsed.hostname in ('weibo.com', 'www.weibo.com'):
        match = re.fullmatch(r'/\d+/([A-Za-z0-9]+)', parsed.path.rstrip('/'))
    elif parsed.hostname == 'm.weibo.cn':
        match = re.fullmatch(r'/(?:detail|status)/([A-Za-z0-9]+)', parsed.path.rstrip('/'))
    else:
        match = None
    return match[1] if match else None


def get_json(url, account_id, auth_dir, check):
    with open_source(url, account_id, auth_dir, check) as (response, _):
        data = b''.join(response_chunks(response, 8 * 1024 * 1024, check))
    try:
        return json.loads(data)
    except (ValueError, UnicodeError):
        raise ArchiveError('微博未返回有效原文，请检查登录后重试', blocked=True) from None


def status_resources(status):
    resources = []
    for item in (status.get('pic_infos') or {}).values():
        image = item.get('largest') or item.get('original') or item.get('large') or {}
        if image.get('url'):
            resources.append({'url': image['url'], 'kind': 'image'})
    for item in status.get('pics') or []:
        url = (item.get('large') or {}).get('url') or item.get('url')
        if url:
            resources.append({'url': url, 'kind': 'image'})
    info = (status.get('page_info') or {}).get('media_info') or {}
    url = info.get('stream_url_hd') or info.get('stream_url')
    if url:
        resources.append({'url': url, 'kind': 'video'})
    return resources


def status_text(status, account_id, auth_dir, check):
    text = status.get('text_raw') or plain_html(status.get('text'))
    if status.get('isLongText'):
        longtext = get_json('https://weibo.com/ajax/statuses/longtext?id=' + str(status['id']), account_id, auth_dir, check)
        text = (longtext.get('data') or {}).get('longTextContent')
        if not text:
            raise ArchiveError('微博长文未完整返回，未标记保存成功')
        text = plain_html(text)
    return text


def weibo_snapshot(url, account_id, auth_dir, check):
    identifier = weibo_id(url)
    status = get_json('https://weibo.com/ajax/statuses/show?id=' + quote(identifier), account_id, auth_dir, check)
    if not isinstance(status, dict) or not status.get('id'):
        check_resource_denial(status)
        raise ArchiveError('微博原文不可访问或已删除，请检查登录与权限', blocked=True)
    text = status_text(status, account_id, auth_dir, check)
    resources = status_resources(status)
    retweet = status.get('retweeted_status') or {}
    if retweet:
        text += '\n\n转发原文：\n' + status_text(retweet, account_id, auth_dir, check)
        resources.extend(status_resources(retweet))
    return {'title': (status.get('user') or {}).get('screen_name') or '微博原文',
            'text': text, 'source_url': url, 'resources': resources}


def profile_id(url):
    parsed = urlsplit(url)
    match = re.fullmatch(r'/u/(\d+)/?', parsed.path)
    return match[1] if parsed.hostname in ('weibo.com', 'www.weibo.com') and match else None


def profile_snapshot(url, account_id, auth_dir, check):
    uid = profile_id(url)
    payload = get_json('https://weibo.com/ajax/profile/info?uid=' + uid, account_id, auth_dir, check)
    data = payload.get('data') if isinstance(payload, dict) else None
    user = data.get('user') if isinstance(data, dict) else None
    if (not isinstance(user, dict) or str(user.get('idstr') or user.get('id')) != uid
            or not isinstance(user.get('screen_name'), str) or not user['screen_name'].strip()):
        raise ArchiveError('微博个人主页未返回匹配的用户资料，可能已失效或需要登录')
    name = plain_html(user['screen_name'])
    lines = ['个人主页资料快照（不含该用户全部微博）', '昵称：' + name, '用户 ID：' + uid]
    for key, label in (('description', '简介'), ('location', '地区'), ('verified_reason', '认证说明')):
        if isinstance(user.get(key), str) and user[key].strip():
            lines.append(label + '：' + plain_html(user[key]))
    avatar = user.get('avatar_hd') or user.get('avatar_large') or user.get('profile_image_url')
    resources = [{'url': avatar, 'kind': 'image'}] if isinstance(avatar, str) and avatar.startswith(('http://', 'https://')) else []
    return {'title': name + ' · 个人主页资料快照', 'text': '\n'.join(lines), 'source_url': url, 'resources': resources}


def weibo_page_snapshot(url, account_id, auth_dir, check):
    if weibo_id(url):
        return weibo_snapshot(url, account_id, auth_dir, check)
    if profile_id(url):
        return profile_snapshot(url, account_id, auth_dir, check)
    return None


def page_snapshot(body, url):
    parser = ArticleParser(url)
    parser.feed(body.decode('utf-8', errors='replace'))
    text = ''.join(parser.parts).strip()
    if not text or len(text) < 40 or any(word in text[:2000] for word in ('passport.weibo', '登录 - 微博', '安全验证', '访问频次过高')):
        raise ArchiveError('网页未返回可归档正文，可能需要登录或动态加载')
    if urlsplit(url).hostname in ('weibo.com', 'www.weibo.com', 'm.weibo.cn'):
        raise ArchiveError('未识别微博原文地址，不能把页面外壳当作原文')
    return {'title': '网页正文快照', 'text': text, 'source_url': url, 'resources': parser.resources}
