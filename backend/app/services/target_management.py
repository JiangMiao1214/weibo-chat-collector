"""Local target lifecycle; no network, historical identity changes or implicit resume."""
from dataclasses import asdict
from fastapi import HTTPException
from ..collectors.weibo_api import CookieProfileStore
from . import browser_login_manager
from .group_policy import require_group_slot


def check_group_slot(connection, account_id, group_id=None):
    try:
        require_group_slot(connection, account_id, group_id)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


def write_lock(connection):
    if not connection.in_transaction:
        connection.execute('BEGIN IMMEDIATE')


def account_row(connection, account_id):
    row = connection.execute('SELECT * FROM weibo_accounts WHERE id=?', (account_id,)).fetchone()
    if row is None:
        raise HTTPException(404, '账号不存在')
    return row


def require_enabled(connection, account_id, group_id):
    row = connection.execute('''SELECT a.is_active AS account_active,g.is_active AS group_active
        FROM weibo_accounts a JOIN chat_groups g ON g.account_id=a.id WHERE a.id=? AND g.id=?''',
        (account_id, group_id)).fetchone()
    if not row:
        raise HTTPException(404, '账号与群聊不匹配')
    if not row['account_active'] or not row['group_active']:
        raise HTTPException(409, '账号或群聊已停用，请先在管理页启用')


def require_quiet(connection, account_id, group_id=None):
    session = browser_login_manager.get_browser_login_manager().get_active_session()
    if session and session['account_id'] == account_id:
        raise HTTPException(409, '请先结束此账号的扫码或群绑定会话')
    write_lock(connection)
    running = connection.execute('''SELECT 1 FROM collection_jobs WHERE account_id=?
        AND (? IS NULL OR group_id=?) AND status='running' LIMIT 1''',
        (account_id, group_id, group_id)).fetchone()
    if running:
        raise HTTPException(409, '请先在采集监控中安全停止运行中的任务，再执行此操作')


def pause_target(connection, account_id, group_id=None):
    connection.execute('''UPDATE collection_jobs SET status='stopped',stop_code='target_disabled',
        stop_reason='账号或群聊已停用',finished_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
        WHERE account_id=? AND (? IS NULL OR group_id=?) AND status IN ('queued','awaiting_confirmation')''',
        (account_id, group_id, group_id))
    connection.execute("""UPDATE archive_groups SET paused=1,reason='账号或群聊管理操作后暂停，请手动恢复'
        WHERE account_id=? AND (? IS NULL OR group_id=?)""", (account_id, group_id, group_id))

    connection.execute('UPDATE ocr_groups SET paused=1 WHERE account_id=? AND (? IS NULL OR group_id=?)',
                       (account_id,group_id,group_id))


def group_has_history(connection, group_id):
    for table in ('messages', 'collection_jobs', 'browser_page_captures', 'import_batches', 'weibo_verification_reports'):
        if connection.execute(f'SELECT 1 FROM {table} WHERE group_id=? LIMIT 1', (group_id,)).fetchone():
            return True
    return False


def ensure_group_identity_can_change(connection, *, group_id, current_source_group_id, requested_source_group_id):
    if requested_source_group_id and requested_source_group_id != current_source_group_id:
        row = connection.execute('SELECT account_id FROM chat_groups WHERE id=?', (group_id,)).fetchone()
        check_group_slot(connection, row[0], group_id)
    if current_source_group_id and current_source_group_id != requested_source_group_id and group_has_history(connection, group_id):
        raise HTTPException(409, '此群已有历史记录，不能更换或清除微博群 ID；请保留原绑定')


def check_binding(connection, account_id, source_group_id, group_id=None):
    if source_group_id and connection.execute('''SELECT 1 FROM chat_groups
        WHERE account_id=? AND source_group_id=? AND (? IS NULL OR id<>?)''',
        (account_id, source_group_id, group_id, group_id)).fetchone():
        raise HTTPException(409, '此微博群 ID 已绑定到该账号的另一个群聊')


def group_summary(connection, row):
    item = dict(row)
    aid, gid = item['account_id'], item['id']
    item.update(dict(connection.execute('''SELECT COUNT(*) AS message_count,MIN(sent_at) AS first_message_at,
        MAX(sent_at) AS last_message_at FROM messages WHERE account_id=? AND group_id=? AND is_deleted=0''', (aid, gid)).fetchone()))
    item['attachment_count'] = connection.execute('''SELECT COUNT(*) FROM attachments a JOIN messages m ON m.id=a.message_id
        WHERE m.account_id=? AND m.group_id=? AND m.is_deleted=0''', (aid, gid)).fetchone()[0]
    archive = connection.execute('SELECT paused,reason FROM archive_groups WHERE account_id=? AND group_id=?', (aid, gid)).fetchone()
    item['archive'] = dict(archive) if archive else None
    item['archive_counts'] = dict(connection.execute('''SELECT j.status,COUNT(*) FROM archive_jobs j JOIN messages m ON m.id=j.message_id
        WHERE m.account_id=? AND m.group_id=? AND m.is_deleted=0 GROUP BY j.status''', (aid, gid)))
    latest = connection.execute('''SELECT id,status,created_at FROM collection_jobs
        WHERE account_id=? AND group_id=? ORDER BY id DESC LIMIT 1''', (aid, gid)).fetchone()
    item['latest_collection'] = dict(latest) if latest else None
    return item


def overview(connection, auth_dir):
    groups = [group_summary(connection, row) for row in connection.execute('''SELECT g.*,a.display_name AS account_name
        FROM chat_groups g JOIN weibo_accounts a ON a.id=g.account_id ORDER BY g.account_id,g.id''').fetchall()]
    store = CookieProfileStore(auth_dir)
    accounts = []
    for row in connection.execute('SELECT id,display_name,notes,is_active FROM weibo_accounts ORDER BY id').fetchall():
        item = dict(row)
        owned = [g for g in groups if g['account_id'] == row['id']]
        item.update(group_count=len(owned), message_count=sum(g['message_count'] for g in owned),
                    cookie_profile=asdict(store.status(row['id'])))
        accounts.append(item)
    return {'accounts': accounts, 'groups': groups}
