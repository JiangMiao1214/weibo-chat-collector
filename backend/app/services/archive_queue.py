"""Persistent opt-in group archives. Discovery runs only on committed messages."""
import time

from fastapi import HTTPException

from .target_management import require_enabled, write_lock
from .web_urls import safe_url
from .link_previews import previews
from .archive_reuse import unique_batch, index_resources

KINDS = {'image', 'video', 'file', 'audio', 'link'}


def scope(connection, account_id, group_id):
    if not connection.execute('SELECT 1 FROM chat_groups WHERE id=? AND account_id=?',
                              (group_id, account_id)).fetchone():
        raise HTTPException(404, '账号与群聊不匹配')


def status(connection, account_id, group_id):
    scope(connection, account_id, group_id)
    group = connection.execute('SELECT * FROM archive_groups WHERE group_id=?', (group_id,)).fetchone()
    counts = dict(connection.execute('''SELECT j.status, COUNT(*) FROM archive_jobs j
        JOIN messages m ON m.id=j.message_id WHERE m.account_id=? AND m.group_id=? AND m.is_deleted=0
        GROUP BY j.status''', (account_id, group_id)))
    errors = [dict(row) for row in connection.execute('''SELECT j.id, j.kind, j.status, j.error, j.source_url, (SELECT failure_url FROM archive_failures f WHERE f.job_id=j.id) AS failure_url, m.id AS message_id
        FROM archive_jobs j JOIN messages m ON m.id=j.message_id
        WHERE m.account_id=? AND m.group_id=? AND m.is_deleted=0 AND j.error IS NOT NULL
        ORDER BY j.id DESC LIMIT 20''', (account_id, group_id))]
    active_jobs = [dict(row) for row in connection.execute('''SELECT j.id,j.kind,j.source_url,m.id AS message_id
        FROM archive_jobs j JOIN messages m ON m.id=j.message_id
        WHERE m.account_id=? AND m.group_id=? AND m.is_deleted=0 AND j.status='downloading' ORDER BY j.id LIMIT 3''',
        (account_id, group_id))]
    for item in errors + active_jobs:
        item['source_url'] = safe_url(item['source_url'])
        if 'failure_url' in item:
            item['failure_url'] = safe_url(item['failure_url'])
    return {'account_id': account_id, 'group_id': group_id, 'enabled': bool(group), 'paused': bool(group and group['paused']),
            'reason': group['reason'] if group else None, 'counts': counts, 'errors': errors, 'active_jobs': active_jobs}


def control(connection, account_id, group_id, action):
    write_lock(connection)
    scope(connection, account_id, group_id)
    if action in ('start', 'resume'):
        require_enabled(connection, account_id, group_id)
    if action == 'start':
        connection.execute('''INSERT INTO archive_groups(account_id, group_id) VALUES (?,?)
            ON CONFLICT(group_id) DO UPDATE SET paused=0, reason=NULL, scan_id=0''', (account_id, group_id))
    elif action == 'pause':
        connection.execute("UPDATE archive_groups SET paused=1, reason='已手动暂停' WHERE group_id=?", (group_id,))
    elif action == 'resume':
        connection.execute('UPDATE archive_groups SET paused=0, reason=NULL, scan_id=0 WHERE group_id=?', (group_id,))
    if action in ('start', 'resume'):
        connection.execute('''UPDATE archive_jobs SET status='queued', attempts=0, retry_at=0, error=NULL
            WHERE status IN ('failed','blocked') AND message_id IN
            (SELECT id FROM messages WHERE account_id=? AND group_id=? AND is_deleted=0)''', (account_id, group_id))
    return status(connection, account_id, group_id)


def skip_link(connection, job_id, account_id, group_id):
    scope(connection, account_id, group_id)
    row = connection.execute('''SELECT j.status,j.kind FROM archive_jobs j JOIN messages m ON m.id=j.message_id
        WHERE j.id=? AND m.account_id=? AND m.group_id=? AND m.is_deleted=0''', (job_id, account_id, group_id)).fetchone()
    if not row:
        raise HTTPException(404, '此范围内没有该归档')
    if row['kind'] != 'link' or row['status'] not in ('failed', 'blocked', 'skipped'):
        raise HTTPException(409, '仅能跳过失败或等待处理的链接')
    connection.execute("""UPDATE archive_jobs SET status='skipped',retry_at=0,
        error='已跳过：用户确认此链接不可访问，未保存本地' WHERE id=? AND status IN ('failed','blocked')""", (job_id,))
    return status(connection, account_id, group_id)


def enqueue(connection, message_id, url, kind, parent_id=None):
    url = safe_url(url)
    if url and kind in KINDS:
        connection.execute('''INSERT OR IGNORE INTO archive_jobs(message_id,source_url,kind,parent_id)
            VALUES (?,?,?,?)''', (message_id, url, kind, parent_id))
        if parent_id:
            child = connection.execute('SELECT id FROM archive_jobs WHERE message_id=? AND source_url=? AND kind=?',
                                       (message_id, url, kind)).fetchone()
            if child['id'] != parent_id:
                connection.execute('INSERT OR IGNORE INTO archive_resources(parent_id, job_id) VALUES (?,?)',
                                   (parent_id, child['id']))


def discover(connection):
    for group in connection.execute('SELECT g.* FROM archive_groups g JOIN weibo_accounts a ON a.id=g.account_id JOIN chat_groups c ON c.id=g.group_id WHERE g.paused=0 AND a.is_active=1 AND c.is_active=1') .fetchall():
        rows = connection.execute('''SELECT id, content_text, raw_payload, is_deleted FROM messages
            WHERE account_id=? AND group_id=? AND id>? ORDER BY id LIMIT 100''',
            (group['account_id'], group['group_id'], group['scan_id'])).fetchall()
        for row in rows:
            if row['is_deleted']:
                continue
            attachments = connection.execute('SELECT * FROM attachments WHERE message_id=?', (row['id'],)).fetchall()
            known = {a['source_url'] for a in attachments}
            for attachment in attachments:
                enqueue(connection, row['id'], attachment['source_url'], attachment['attachment_type'])
            for card in previews(row['raw_payload'], row['content_text'], attachments):
                if card['url'] not in known:
                    enqueue(connection, row['id'], card['url'], 'link')
        if rows:
            connection.execute('UPDATE archive_groups SET scan_id=? WHERE group_id=?', (rows[-1]['id'], group['group_id']))


def claim(connection, owner, now=None):
    jobs = claim_batch(connection, owner, now=now, limit=1)
    return jobs[0] if jobs else None


def claim_batch(connection, owner, now=None, limit=3):
    now = time.time() if now is None else now
    # Also serialize discovery and recovery across multiple backend processes.
    result = connection.execute('''UPDATE archive_runtime SET owner=?, expires_at=? WHERE id=1
        AND (owner=? OR expires_at<?) AND next_at<=?''', (owner, now + 90, owner, now, now))
    if not result.rowcount:
        return []
    connection.execute("UPDATE archive_jobs SET status='queued' WHERE status='downloading'")
    discover(connection)
    index_resources(connection)
    if connection.execute("SELECT 1 FROM collection_jobs WHERE status IN ('running','awaiting_confirmation') LIMIT 1").fetchone():
        return []
    rows = connection.execute('''SELECT j.*, m.account_id,m.group_id FROM archive_jobs j
        JOIN messages m ON m.id=j.message_id JOIN archive_groups g ON g.group_id=m.group_id
        WHERE g.paused=0 AND m.is_deleted=0 AND j.status='queued'
        AND EXISTS(SELECT 1 FROM weibo_accounts a JOIN chat_groups c ON c.account_id=a.id
            WHERE a.id=m.account_id AND c.id=m.group_id AND a.is_active=1 AND c.is_active=1) AND j.retry_at<=?
        ORDER BY j.id''', (now,))
    rows = unique_batch(rows, max(1, min(limit, 3)))
    for row in rows:
        connection.execute("UPDATE archive_jobs SET status='downloading', attempts=attempts+1, error=NULL WHERE id=?", (row['id'],))
    return [dict(row) for row in rows]
