"""Persistent explicit references. Never interpret shared-post IDs as chat reply IDs."""
import json
import re
from fastapi import HTTPException


def normalized_target(message):
    value = message.get('reply_to_source_message_id')
    if value is None or value == '':
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', str(value)):
        raise ValueError('reply_to_source_message_id must be an exact source message ID')
    if str(value) == str(message.get('source_message_id')):
        raise ValueError('A message cannot reference itself')
    return str(value)


def save_import_reference(connection, message_id, message):
    target = normalized_target(message)
    if target and not connection.execute('SELECT 1 FROM message_reference_dismissals WHERE message_id=?', (message_id,)).fetchone():
        connection.execute('''INSERT OR IGNORE INTO message_references(message_id,target_source_id,method)
            VALUES(?,?,'source_id')''', (message_id, target))


def backfill_import_references(connection):
    # Only the documented normalized import field is recognized, not arbitrary remote IDs.
    rows = connection.execute('''SELECT id,raw_payload FROM messages WHERE raw_payload LIKE '%reply_to_source_message_id%'
        AND id NOT IN (SELECT message_id FROM message_references)''').fetchall()
    for row in rows:
        try:
            payload = json.loads(row[1])
            if isinstance(payload, dict) and 'source_message_id' in payload and 'content_text' in payload:
                save_import_reference(connection, row[0], payload)
        except (ValueError, TypeError):
            continue


def reference_candidates(connection, source):
    reference = connection.execute('SELECT * FROM message_references WHERE message_id=?', (source['id'],)).fetchone()
    if reference is None:
        return None
    field, value = ('id', reference['target_message_id']) if reference['method'] == 'manual' else ('source_message_id', reference['target_source_id'])
    rows = connection.execute(f'''SELECT id,account_id,group_id,is_deleted,sent_at FROM messages
        WHERE account_id=? AND group_id=? AND is_deleted=? AND id<>? AND {field}=?
        AND julianday(sent_at)<=julianday(?) LIMIT 2''', (source['account_id'], source['group_id'], source['is_deleted'],
        source['id'], value, source['sent_at'])).fetchall()
    return reference['method'], rows


def bind_manual(connection, source, target_id):
    target = connection.execute('''SELECT id,source_message_id,sent_at FROM messages
        WHERE id=? AND account_id=? AND group_id=? AND is_deleted=?''',
        (target_id, source['account_id'], source['group_id'], source['is_deleted'])).fetchone()
    if target is None:
        raise HTTPException(404, '目标消息不存在或不属于当前账号、群聊及删除状态')
    if target_id == source['id'] or not connection.execute('SELECT julianday(?)<=julianday(?)',
            (target['sent_at'], source['sent_at'])).fetchone()[0]:
        raise HTTPException(409, '不能关联自身或时间晚于回复的消息')
    connection.execute('DELETE FROM message_reference_dismissals WHERE message_id=?', (source['id'],))
    connection.execute('''INSERT INTO message_references(message_id,target_message_id,target_source_id,method)
        VALUES(?,?,?,'manual') ON CONFLICT(message_id) DO UPDATE SET
        target_message_id=excluded.target_message_id,target_source_id=excluded.target_source_id,
        method='manual',updated_at=CURRENT_TIMESTAMP''', (source['id'], target_id, target['source_message_id']))


def enrich_references(connection, items):
    if not items:
        return items
    marks = ','.join('?' for _ in items)
    methods = dict(connection.execute(f'SELECT message_id,method FROM message_references WHERE message_id IN ({marks})',
                                      [item['id'] for item in items]))
    for item in items:
        item['reference_method'] = methods.get(item['id'])
    return items
