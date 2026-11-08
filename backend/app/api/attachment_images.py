import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response

from ..services.archive_views import serve_archive
from ..database import get_connection
from ..services.attachment_images import fetch_attachment_image
from ..settings import Settings, get_settings

router = APIRouter(prefix='/api/attachments', tags=['attachments'])


@router.get('/{attachment_id}/image')
def attachment_image(
    attachment_id: int = Path(gt=0),
    account_id: int = Query(gt=0),
    group_id: int = Query(gt=0),
    deleted_only: bool = False,
    connection: sqlite3.Connection = Depends(get_connection),
    settings: Settings = Depends(get_settings),
):
    row = connection.execute('''
        SELECT a.source_url, m.account_id FROM attachments a
        JOIN messages m ON m.id = a.message_id
        WHERE a.id = ? AND a.attachment_type = 'image'
          AND m.account_id = ? AND m.group_id = ? AND m.is_deleted = ?
    ''', (attachment_id, account_id, group_id, int(deleted_only))).fetchone()
    if row is None:
        raise HTTPException(404, '此范围内没有该图片附件')
    archived = connection.execute('''
        SELECT j.*,m.account_id,m.group_id FROM archive_jobs j
        JOIN messages m ON m.id=j.message_id JOIN attachments a ON a.message_id=m.id
        WHERE a.id=? AND j.source_url=a.source_url AND j.kind='image' AND j.status='stored'
    ''', (attachment_id,)).fetchone()
    if archived:
        return serve_archive(connection, archived, settings.attachments_dir, deleted_only)
    content, mime = fetch_attachment_image(row['source_url'] or '', row['account_id'], settings.weibo_api_auth_dir)
    return Response(content, media_type=mime, headers={
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
        'Content-Security-Policy': "default-src 'none'; sandbox", 'Referrer-Policy': 'no-referrer',
    })
