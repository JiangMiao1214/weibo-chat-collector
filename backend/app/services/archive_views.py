"""Only locally stored bytes are served by archive routes; all reads stay scoped."""
from html import escape
import json
from urllib.parse import urlencode, urlsplit

from fastapi import HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from .archive_download import SNAPSHOT_MIME
from .archive_storage import ArchiveError, stored_path


def content_url(job_id, account_id, group_id, deleted_only=False):
    return f'/api/archives/{job_id}/content?' + urlencode({'account_id': account_id, 'group_id': group_id,
                                                         'deleted_only': str(deleted_only).lower()})


def snapshot_html(connection, job, path, deleted_only):
    snapshot = json.loads(path.read_text(encoding='utf-8'))
    chunks = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">',
              '<title>本地正文归档</title><h1>' + escape(snapshot['title']) + '</h1>',
              '<p>本地正文快照 · 保存时间：' + escape(job['stored_at'] or '') + '</p>',
              '<pre style="white-space:pre-wrap;overflow-wrap:anywhere">' + escape(snapshot['text']) + '</pre>']
    for resource in snapshot['resources']:
        child = connection.execute('''SELECT * FROM archive_jobs WHERE message_id=? AND source_url=? AND kind=?''',
                                   (job['message_id'], resource['url'], resource['kind'])).fetchone()
        if child and child['status'] == 'stored':
            url = escape(content_url(child['id'], job['account_id'], job['group_id'], deleted_only), quote=True)
            if child['kind'] == 'image':
                chunks.append(f'<p><img src="{url}" style="max-width:100%" alt="已归档图片"></p>')
            elif child['kind'] in ('video', 'audio'):
                tag = child['kind']
                chunks.append(f'<{tag} controls src="{url}" style="max-width:100%"></{tag}>')
            else:
                chunks.append(f'<p><a href="{url}">打开本地文件</a></p>')
        else:
            chunks.append('<p>关联媒体尚未保存：' + escape(child['error'] or child['status'] if child else '未加入队列') + '</p>')
    return '\n'.join(chunks) + '</html>'


def serve_archive(connection, job, root, deleted_only=False):
    if job['status'] != 'stored' or not job['local_path']:
        raise HTTPException(409, '此内容尚未保存到本地')
    try:
        path = stored_path(root, job['local_path'], job['account_id'])
    except ArchiveError as error:
        raise HTTPException(404, str(error)) from None
    headers = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer'}
    mime = job['mime_type'] or 'application/octet-stream'
    if mime == SNAPSHOT_MIME:
        headers['Content-Security-Policy'] = "default-src 'none'; img-src 'self'; media-src 'self'; style-src 'unsafe-inline'; frame-ancestors 'none'"
        return HTMLResponse(snapshot_html(connection, job, path, deleted_only), headers=headers)
    inline = mime.startswith(('image/', 'video/', 'audio/')) and mime != 'image/svg+xml'
    filename = urlsplit(job['source_url']).path.rsplit('/', 1)[-1] or f"archive-{job['id']}"
    if not inline:
        headers['Content-Security-Policy'] = "default-src 'none'; sandbox"
    return FileResponse(path, media_type=mime, filename=filename,
                        content_disposition_type='inline' if inline else 'attachment', headers=headers)
