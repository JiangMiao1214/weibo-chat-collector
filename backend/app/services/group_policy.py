"""Shared write-serialized group limit for HTTP configuration and offline import."""


def require_group_slot(connection, account_id, group_id=None):
    if not connection.in_transaction:
        connection.execute('BEGIN IMMEDIATE')
    if connection.execute('''SELECT 1 FROM chat_groups
        WHERE account_id=? AND (? IS NULL OR id<>?) LIMIT 1''',
        (account_id, group_id, group_id)).fetchone():
        raise ValueError('一个账号只能对应一个群聊（包含已停用群聊）；请使用已有群聊，已有多群账号需先整理')
