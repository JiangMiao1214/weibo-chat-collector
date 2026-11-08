"""Delete only empty local accounts, with a scoped preview and rollback on failure."""
import hashlib
import json
import os
import sqlite3
import stat
import tempfile
from fastapi import HTTPException
from . import browser_login_manager
from .target_management import account_row, write_lock


SCOPED_TABLES = {
    'collection_jobs': '采集任务与断点', 'import_batches': '导入记录',
    'browser_page_captures': '网页快照', 'weibo_verification_reports': '验证记录',
    'deletion_jobs': '删除记录', 'archive_groups': '归档设置', 'ocr_groups': 'OCR 设置',
    'activity_events': '操作日志',
}
ACCOUNT_TABLES = {'archive_https_hosts': '归档主机记录', 'ocr_results': 'OCR 文字结果'}
LABELS = {'messages': '正常消息', 'recycled_messages': '回收站消息', 'attachments': '附件记录',
          'group_members': '群成员记录', 'archive_jobs': '归档记录', 'ocr_jobs': 'OCR 任务',
          **SCOPED_TABLES, **ACCOUNT_TABLES}
SCOPE = '(account_id=? OR group_id IN (SELECT id FROM chat_groups WHERE account_id=?))'
MESSAGE_IDS = f'SELECT id FROM messages WHERE {SCOPE}'


def history_counts(connection, account_id):
    args = (account_id, account_id)
    def count(sql):
        return connection.execute(sql, args).fetchone()[0]
    counts = {key: count(f'SELECT COUNT(*) FROM {key} WHERE {SCOPE}') for key in SCOPED_TABLES}
    counts.update({key: connection.execute(f'SELECT COUNT(*) FROM {key} WHERE account_id=?',
                                         (account_id,)).fetchone()[0] for key in ACCOUNT_TABLES})
    counts['messages'] = count(f'SELECT COUNT(*) FROM messages WHERE {SCOPE} AND is_deleted=0')
    counts['recycled_messages'] = count(f'SELECT COUNT(*) FROM messages WHERE {SCOPE} AND is_deleted<>0')
    counts['attachments'] = count(f'SELECT COUNT(*) FROM attachments WHERE message_id IN ({MESSAGE_IDS})')
    counts['group_members'] = connection.execute('''SELECT COUNT(*) FROM group_members WHERE group_id IN
        (SELECT id FROM chat_groups WHERE account_id=?)''', (account_id,)).fetchone()[0]
    archives = f'SELECT id FROM archive_jobs WHERE message_id IN ({MESSAGE_IDS})'
    counts['archive_jobs'] = count(f'SELECT COUNT(*) FROM archive_jobs WHERE message_id IN ({MESSAGE_IDS})')
    counts['ocr_jobs'] = count(f'SELECT COUNT(*) FROM ocr_jobs WHERE archive_id IN ({archives})')
    return counts


def credential_state(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None, None
    except OSError:
        return None, '无法检查本机凭据，请先检查目录权限'
    if not stat.S_ISREG(info.st_mode):
        return None, '凭据路径不是普通文件，请先处理该路径后重试'
    return [info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns], None


def deletion_preview(connection, account_id, auth_dir):
    # Browser lock always precedes the SQLite write lock, as with other lifecycle actions.
    session = browser_login_manager.get_browser_login_manager().get_active_session()
    write_lock(connection)
    account = dict(account_row(connection, account_id))
    groups = [dict(row) for row in connection.execute('SELECT * FROM chat_groups WHERE account_id=? ORDER BY id', (account_id,))]
    counts = history_counts(connection, account_id)
    blockers = [f'{LABELS[key]} {value} 条，需保留账号' for key, value in counts.items() if value]
    if session and session['account_id'] == account_id:
        blockers.append('请先结束此账号的扫码或群绑定会话')
    credential, error = credential_state(auth_dir / f'account-{account_id}.cookies.json')
    if error:
        blockers.append(error)
    snapshot = json.dumps([account, groups, counts, credential, blockers], sort_keys=True, ensure_ascii=False)
    return {'account': {'id': account_id, 'display_name': account['display_name']},
            'groups': [{'id': row['id'], 'name': row['name']} for row in groups],
            'counts': counts, 'blockers': blockers, 'can_delete': not blockers,
            'credentials_present': credential is not None,
            'preview_token': hashlib.sha256(snapshot.encode()).hexdigest()}


def restore_credential(path, content):
    descriptor, temporary = tempfile.mkstemp(prefix='.restore-account-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def delete_empty_account(connection, account_id, auth_dir, preview_token):
    preview = deletion_preview(connection, account_id, auth_dir)
    if not preview['can_delete']:
        raise HTTPException(409, '此账号不能删除：' + '；'.join(preview['blockers']))
    if preview['preview_token'] != preview_token:
        raise HTTPException(409, '账号、关联范围或凭据已变化，请重新预览并确认')
    cookie = auth_dir / f'account-{account_id}.cookies.json'
    saved = None
    removed = False
    try:
        # Retain bytes only in memory until commit so a database failure can restore credentials.
        if preview['credentials_present']:
            saved = cookie.read_bytes()
        connection.execute('DELETE FROM chat_groups WHERE account_id=?', (account_id,))
        connection.execute('DELETE FROM weibo_accounts WHERE id=?', (account_id,))
        if saved is not None:
            cookie.unlink()
            removed = True
        connection.commit()
    except (OSError, sqlite3.Error) as error:
        connection.rollback()
        if removed:
            restore_credential(cookie, saved)
        raise HTTPException(409, '删除未完成，账号与群已保留；请检查数据库及凭据目录权限后重试') from error
    return {'id': account_id, 'deleted': True, 'deleted_group_count': len(preview['groups']),
            'credentials_removed': preview['credentials_present'], 'delete_attachment_files': False}
