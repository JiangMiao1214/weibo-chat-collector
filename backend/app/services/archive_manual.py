"""User supplied readable text for sites requiring a browser session; no remote execution."""
from fastapi import HTTPException
from .archive_download import save_snapshot
from .archive_publish import publish
from .archive_queue import status


def manual_snapshot(connection, settings, job_id, account_id, group_id, text):
    # Take the same write lock as workers before testing and changing state.
    connection.execute('UPDATE archive_runtime SET next_at=next_at WHERE id=1')
    row = connection.execute('''SELECT j.*,m.account_id,m.group_id FROM archive_jobs j JOIN messages m ON m.id=j.message_id
        WHERE j.id=? AND m.account_id=? AND m.group_id=? AND m.is_deleted=0''', (job_id, account_id, group_id)).fetchone()
    if not row:
        raise HTTPException(404, '此范围内没有该归档')
    if row['kind'] != 'link' or row['status'] not in ('failed', 'blocked', 'skipped'):
        raise HTTPException(409, '仅能为未保存的失败或已跳过链接导入正文')
    if len(text.strip()) < 40 or len(text.encode('utf-8')) > 2 * 1024**2:
        raise HTTPException(422, '正文至少 40 字且不超过 2 MiB')
    snapshot = {'title': '手动正文快照（不含关联媒体）', 'text': text.strip(), 'source_url': row['source_url'], 'resources': []}
    result, resources = save_snapshot(snapshot, dict(row), settings, lambda: None)
    publish(connection, row, result, resources)
    return status(connection, account_id, group_id)
