"""Whitelisted, stored-only link presentation. Never fetch a URL or expose raw payload."""
from .archive_metadata import enrich_archives
import json
import re
from html import unescape
from .web_urls import safe_url




def plain(value):
    return unescape(re.sub(r'<[^>]*>', '', value)) if isinstance(value, str) else None


def object_value(value):
    return value if isinstance(value, dict) else {}


def count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def from_object(entry):
    entry = object_value(entry)
    info = object_value(entry.get('object'))
    status = object_value(info.get('status'))
    url = safe_url(entry.get('url_ori') or entry.get('url') or info.get('url_long') or info.get('url_short'))
    if not url:
        return None
    return {'url': url, 'title': plain(info.get('title')),
            'author': plain(object_value(status.get('user')).get('screen_name')),
            'text': plain(status.get('text') or info.get('description')),
            'reposts': count(status.get('reposts_count')), 'comments': count(status.get('comments_count')),
            'likes': count(status.get('attitudes_count'))}


def previews(raw_payload, content, attachments):
    try:
        raw = object_value(json.loads(raw_payload or '{}'))
    except (ValueError, TypeError):
        raw = {}
    objects = raw.get('url_objects')
    result = {}
    for entry in objects if isinstance(objects, list) else []:
        card = from_object(entry)
        if card:
            result[card['url']] = card
    candidates = [{'source_url': url.rstrip('。，！？；：、）】」'), 'title': None, 'description': None}
                  for url in re.findall(r'https?://[^\s<>"「」]+', content or '')]
    candidates.extend(a for a in attachments if a['attachment_type'] == 'link')
    for item in candidates:
        url = safe_url(item['source_url'])
        if url and url not in result:
            result[url] = {'url': url, 'title': plain(item['title']), 'text': plain(item['description']),
                           'author': None, 'reposts': None, 'comments': None, 'likes': None}
    return list(result.values())


def enrich_items(connection, items):
    if not items:
        return enrich_archives(connection, items)
    ids = [item['id'] for item in items]
    marks = ','.join('?' for _ in ids)
    raw = {row['id']: row['raw_payload'] for row in connection.execute(
        f'SELECT id, raw_payload FROM messages WHERE id IN ({marks})', ids)}
    attachments = {}
    for row in connection.execute(f'SELECT * FROM attachments WHERE message_id IN ({marks}) ORDER BY id', ids):
        attachments.setdefault(row['message_id'], []).append(dict(row))
    for item in items:
        item['link_previews'] = previews(raw[item['id']], item['content_text'], attachments.get(item['id'], []))
    return enrich_archives(connection, items)
