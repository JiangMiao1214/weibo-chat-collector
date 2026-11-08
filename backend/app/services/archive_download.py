"""Download originals and article snapshots without re-encoding image bytes."""
import json
import shutil
from urllib.parse import urlsplit

from .archive_network import open_source, response_chunks
from .archive_pages import weibo_page_snapshot, page_snapshot
from .archive_storage import ArchiveError, save_stream
from .archive_urls import normalize_source
from .archive_resume import resume_state, range_headers, receive_binary
from .archive_hls import HLS_MIMES, save_hls
from .archive_locks import job_lock

SNAPSHOT_MIME = 'application/vnd.weibo-archive+json'
LIMITS = {'image': 10 * 1024**2, 'link': 2 * 1024**2, 'video': 20 * 1024**2,
          'file': 20 * 1024**2, 'audio': 10 * 1024**2}


def download_limit(kind, mime):
    # Links may resolve to media/files; known media MIME also constrains generic files.
    declared = LIMITS['file'] if kind == 'link' and mime != 'text/html' else LIMITS[kind]
    media_kind = mime.split('/')[0]
    actual = LIMITS['link'] if mime == 'text/html' else LIMITS.get(media_kind, LIMITS['file'])
    return min(declared, actual)


def save_snapshot(snapshot, job, settings, check):
    resources = {item['url']: item for item in snapshot['resources']}
    if len(resources) > 100:
        raise ArchiveError('正文关联资源超过 100 个，未标记完整归档')
    snapshot['resources'] = list(resources.values())
    result = save_stream(settings.attachments_dir, job['account_id'],
                         [json.dumps(snapshot, ensure_ascii=False).encode('utf-8')], SNAPSHOT_MIME, check)
    return result, snapshot['resources']


def image_signature(mime, first):
    signatures = {'image/jpeg': b'\xff\xd8\xff', 'image/png': b'\x89PNG\r\n\x1a\n', 'image/gif': b'GIF8'}
    if mime in signatures:
        return first.startswith(signatures[mime])
    if mime == 'image/webp':
        return first[:4] == b'RIFF' and first[8:12] == b'WEBP'
    return mime == 'image/avif' and first[4:8] == b'ftyp' and b'avif' in first[8:32]


def validate_media(mime, kind, first):
    if kind == 'image' and mime not in ('image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/avif'):
        raise ArchiveError('图片源未返回图片，可能是失效地址或登录页')
    if kind == 'image' and not image_signature(mime, first):
        raise ArchiveError('图片文件头无效，未标记保存成功')
    if kind == 'video' and mime not in ('video/mp4', 'video/webm', 'video/quicktime', 'application/octet-stream'):
        raise ArchiveError('未获得视频原文件；播放页或分段流暂不支持')
    if mime in ('text/html', 'application/json') or first.lstrip().lower().startswith((b'<!doctype html', b'<html', b'{"error')):
        raise ArchiveError('源站返回页面或错误信息，未保存为附件')
    if mime in ('application/vnd.apple.mpegurl', 'application/x-mpegurl') or first.startswith(b'#EXTM3U'):
        raise ArchiveError('分段视频流暂不支持完整离线归档')


def download_content(job, settings, check):
    url = normalize_source(job['source_url'])
    snapshot = weibo_page_snapshot(url, job['account_id'], settings.weibo_api_auth_dir, check) if job['kind'] == 'link' else None
    if snapshot is not None:
        return save_snapshot(snapshot, job, settings, check)
    state = resume_state(job, settings.attachments_dir) if job['kind'] != 'link' else None
    with open_source(url, job['account_id'], settings.weibo_api_auth_dir, check, **range_headers(state)) as (response, final_url):
        snapshot = weibo_page_snapshot(final_url, job['account_id'], settings.weibo_api_auth_dir, check) if job['kind'] == 'link' else None
        if snapshot is not None:
            return save_snapshot(snapshot, job, settings, check)
        mime = response.headers.get('Content-Type', 'application/octet-stream').split(';')[0].strip().lower()
        if (mime in HLS_MIMES or urlsplit(final_url).path.lower().endswith('.m3u8')) and job['kind'] in ('video', 'link', 'file'):
            if state:
                shutil.rmtree(state['directory'], ignore_errors=True)
            body = b''.join(response_chunks(response, LIMITS['link'], check))
            return save_hls(body, final_url, job, settings, check, open_source, LIMITS['video'])
        chunks = response_chunks(response, download_limit(job['kind'], mime), check)
        if job['kind'] == 'link' and mime == 'text/html':
            return save_snapshot(page_snapshot(b''.join(chunks), final_url), job, settings, check)
        result = receive_binary(response, final_url, state, job, settings.attachments_dir, mime,
                                download_limit(job['kind'], mime), check, validate_media)
        return result, []


def download(job, settings, check=lambda: None):
    with job_lock(settings.attachments_dir, job):
        try:
            return download_content(job, settings, check)
        except ArchiveError as error:
            if getattr(error, 'restart_download', False) and job.get('id'):
                state = resume_state(job, settings.attachments_dir)
                shutil.rmtree(state['directory'], ignore_errors=True)
            raise
