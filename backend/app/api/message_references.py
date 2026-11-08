"""Explicit confirmation is required to persist a manual quote choice."""
import sqlite3
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field
from ..database import get_connection
from ..services.quote_context import find_quote_context, scoped_source, quoted_text, text_key, message_items
from ..services.message_references import bind_manual
from ..services.target_management import write_lock

router = APIRouter(prefix='/api/messages', tags=['references'])


class ReferenceChoice(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account_id: int = Field(ge=1)
    group_id: int = Field(ge=1)
    deleted_only: bool = False
    target_message_id: int = Field(ge=1)
    confirmed: Literal[True]


def require_source(connection, message_id, account_id, group_id, deleted_only):
    source = scoped_source(connection, message_id, account_id, group_id, deleted_only)
    if source is None:
        raise HTTPException(404, '消息不存在或不属于当前范围')
    return source


@router.put('/{message_id}/reference')
def save_reference(form: ReferenceChoice, message_id: int = Path(ge=1), connection: sqlite3.Connection = Depends(get_connection)):
    write_lock(connection)
    source = require_source(connection, message_id, form.account_id, form.group_id, form.deleted_only)
    bind_manual(connection, source, form.target_message_id)
    return find_quote_context(connection, source)


@router.delete('/{message_id}/reference')
def remove_reference(message_id: int = Path(ge=1), account_id: int = Query(ge=1), group_id: int = Query(ge=1),
                     confirmed: bool = Query(), deleted_only: bool = False, connection: sqlite3.Connection = Depends(get_connection)):
    if not confirmed:
        raise HTTPException(422, '需要明确确认解除关联')
    write_lock(connection)
    source = require_source(connection, message_id, account_id, group_id, deleted_only)
    connection.execute('DELETE FROM message_references WHERE message_id=?', (message_id,))
    connection.execute('INSERT OR IGNORE INTO message_reference_dismissals(message_id) VALUES(?)', (message_id,))
    return find_quote_context(connection, source)


@router.get('/{message_id}/reference-candidates')
def candidates(message_id: int = Path(ge=1), account_id: int = Query(ge=1), group_id: int = Query(ge=1),
               deleted_only: bool = False, q: str = Query(default='', max_length=200),
               offset: int = Query(default=0, ge=0), connection: sqlite3.Connection = Depends(get_connection)):
    connection.execute('SAVEPOINT reference_candidates_read')
    try:
        source = require_source(connection, message_id, account_id, group_id, deleted_only)
        connection.create_function('quote_text_key', 1, text_key, deterministic=True)
        text = quoted_text(source['content_text'] or '') or ''
        condition, value = ('instr(content_text,?)>0', q) if q else ('quote_text_key(content_text)=?', text)
        rows = connection.execute(f'''SELECT id FROM messages WHERE account_id=? AND group_id=? AND is_deleted=?
            AND id<>? AND julianday(sent_at)<=julianday(?) AND {condition}
            ORDER BY sent_at DESC,id DESC LIMIT 21 OFFSET ?''',
            (account_id, group_id, int(deleted_only), message_id, source['sent_at'], value, offset)).fetchall()
        return {'items': message_items(connection, [row[0] for row in rows[:20]]) if rows else [],
                'has_more': len(rows)>20, 'offset': offset}
    finally:
        connection.execute('RELEASE SAVEPOINT reference_candidates_read')
