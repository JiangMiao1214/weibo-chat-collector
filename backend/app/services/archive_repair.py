"""Explicit, scoped repair and retry; never resumes paused accounts implicitly."""
from fastapi import HTTPException
from .archive_queue import scope, status
from .archive_reuse import verified_file


def retry_job(connection, job_id, account_id, group_id):
    scope(connection, account_id, group_id)
    job = connection.execute('''SELECT j.* FROM archive_jobs j JOIN messages m ON m.id=j.message_id
        WHERE j.id=? AND m.account_id=? AND m.group_id=? AND m.is_deleted=0''', (job_id, account_id, group_id)).fetchone()
    if not job:
        raise HTTPException(404, '此范围内没有该归档')
    if job['status'] not in ('failed', 'blocked', 'skipped'):
        raise HTTPException(409, '仅能重试失败、等待处理或已跳过项目')
    requeue(connection, job)
    return status(connection, account_id, group_id)


def requeue(connection, job):
    connection.execute("UPDATE archive_jobs SET status='queued',attempts=0,retry_at=0,error=NULL,local_path=NULL WHERE id=?", (job['id'],))
    connection.execute('DELETE FROM archive_failures WHERE job_id=?', (job['id'],))
    connection.execute('''UPDATE attachments SET download_status='pending',local_path=NULL,downloaded_at=NULL
        WHERE message_id=? AND source_url=? AND attachment_type=?''', (job['message_id'], job['source_url'], job['kind']))


def inspect_files(connection, root, account_id, group_id, repair=False):
    scope(connection, account_id, group_id)
    rows = connection.execute('''SELECT j.*,m.account_id FROM archive_jobs j JOIN messages m ON m.id=j.message_id
        WHERE m.account_id=? AND m.group_id=? AND m.is_deleted=0 AND j.status='stored' ''', (account_id, group_id)).fetchall()
    invalid = [row for row in rows if not verified_file(root, row)]
    if repair:
        connection.execute('UPDATE archive_runtime SET next_at=next_at WHERE id=1')
        current = [connection.execute("SELECT j.*,m.account_id FROM archive_jobs j JOIN messages m ON m.id=j.message_id WHERE j.id=? AND j.status='stored' AND m.is_deleted=0", (row['id'],)).fetchone() for row in invalid]
        invalid = [row for row in current if row and not verified_file(root, row)]
        for row in invalid:
            requeue(connection, row)
    return {**status(connection, account_id, group_id), 'checked': len(rows), 'invalid': len(invalid),
            'repaired': len(invalid) if repair else 0}
