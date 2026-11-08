"""Read-only faceting, navigation and export over the same message filter contract."""
import sqlite3
import tempfile
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..database import get_connection
from ..services.link_previews import enrich_items, safe_url
from ..services.quote_context import CONTEXT_RADIUS, message_items, neighboring_ids, scoped_source
from ..services.view_filters import NOISE_SQL, parse_viewer, use_standard_type
from .messages import MessageFilterPayload, build_filters_from_payload, build_where_sql, count_matching_messages


router = APIRouter(prefix='/api/message-view', tags=['messages'])
JOINS = '''FROM messages m JOIN weibo_accounts wa ON wa.id=m.account_id
    JOIN chat_groups cg ON cg.id=m.group_id JOIN chat_users cu ON cu.id=m.user_id'''


class ViewRequest(BaseModel):
    filters: MessageFilterPayload
    deleted_only: bool = False


class MatchRequest(ViewRequest):
    query: str = Field(min_length=1, max_length=300)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=200, ge=1, le=200)


def conditions(request):
    return build_filters_from_payload(request.filters, deleted_only=request.deleted_only)


@router.post('/facets')
def facets(request: ViewRequest, connection: sqlite3.Connection = Depends(get_connection)):
    connection.execute('SAVEPOINT viewer_facets')
    try:
        selection = parse_viewer(request.filters.viewer)
        base = request.model_copy(deep=True)
        if not use_standard_type(base.filters.message_type, base.filters.viewer):
            base.filters.message_type = None
        base.filters.viewer = None
        filters, params = conditions(base)
        where = build_where_sql(filters)
        members = [dict(row) for row in connection.execute(f'''
            SELECT cu.id, cu.display_name, cu.avatar_url, COUNT(*) AS count
            {JOINS} {where} GROUP BY cu.id ORDER BY count DESC, cu.id
        ''', params)]
        for member in members:
            member['avatar_url'] = safe_url(member['avatar_url'])
        hours = [0] * 24
        for row in connection.execute(f"SELECT strftime('%H', m.sent_at), COUNT(*) {JOINS} {where} GROUP BY 1", params):
            if row[0] is not None:
                hours[int(row[0])] = row[1]
        current = request.model_copy(deep=True)
        current.filters.viewer = selection.model_copy(update={'hide_noise': False}).model_dump_json()
        filters, params = conditions(current)
        hidden = count_matching_messages(connection, [*filters, NOISE_SQL], params) if selection.hide_noise else 0
        return {'members': members, 'hours': hours, 'hidden_noise': hidden}
    finally:
        connection.execute('RELEASE SAVEPOINT viewer_facets')


@router.get('/{message_id}/context')
def context(message_id: int = Path(ge=1), account_id: int = Query(ge=1),
            group_id: int = Query(ge=1), deleted_only: bool = False,
            direction: Literal['center', 'older', 'newer'] = 'center',
            connection: sqlite3.Connection = Depends(get_connection)):
    connection.execute('SAVEPOINT viewer_context')
    try:
        target = scoped_source(connection, message_id, account_id, group_id, deleted_only)
        if target is None:
            raise HTTPException(404, '消息不存在或不属于当前账号、群聊及删除状态')
        older = neighboring_ids(connection, target, 'older')
        newer = neighboring_ids(connection, target, 'newer')
        ids = [message_id, *older[:CONTEXT_RADIUS], *newer[:CONTEXT_RADIUS]]
        if direction != 'center':
            ids = (older if direction == 'older' else newer)[:CONTEXT_RADIUS]
        items = message_items(connection, ids)
        return {'target_id': message_id, 'account_id': account_id, 'group_id': group_id,
                'direction': direction,
                'deleted_only': deleted_only, 'items': enrich_items(connection, items),
                'has_older': direction == 'newer' or len(older) > CONTEXT_RADIUS,
                'has_newer': direction == 'older' or len(newer) > CONTEXT_RADIUS}
    finally:
        connection.execute('RELEASE SAVEPOINT viewer_context')


@router.post('/matches')
def matches(request: MatchRequest, connection: sqlite3.Connection = Depends(get_connection)):
    filters, params = conditions(request)
    # instr is literal: %, _ and regex syntax are normal search characters.
    filters.append('instr(lower(m.content_text), lower(?)) > 0')
    params.append(request.query)
    connection.execute('SAVEPOINT viewer_matches')
    try:
        total = count_matching_messages(connection, filters, params)
        items = [dict(row) for row in connection.execute(f'''
            SELECT m.id, m.account_id, m.group_id, m.is_deleted, m.sent_at
            {JOINS} {build_where_sql(filters)} ORDER BY m.sent_at DESC, m.id DESC LIMIT ? OFFSET ?
        ''', [*params, request.limit, request.offset])]
        return {'total': total, 'items': items, 'offset': request.offset}
    finally:
        connection.execute('RELEASE SAVEPOINT viewer_matches')


def export_chunks(output):
    try:
        while chunk := output.read(65536):
            yield chunk
    finally:
        output.close()


@router.post('/export')
def export(request: ViewRequest, connection: sqlite3.Connection = Depends(get_connection)):
    filters, params = conditions(request)
    output = tempfile.SpooledTemporaryFile(max_size=1024 * 1024)
    connection.execute('SAVEPOINT viewer_export')
    try:
        rows = connection.execute(f'''SELECT m.id, wa.display_name AS account_name, cg.name AS group_name,
            cu.display_name AS sender_name, m.sent_at, m.content_text {JOINS}
            {build_where_sql(filters)} ORDER BY m.sent_at DESC, m.id DESC''', params)
        for row in rows:
            text = f"[{row['sent_at']}] {row['account_name']} / {row['group_name']} / {row['sender_name']}\n{row['content_text']}\n"
            for attachment in connection.execute('SELECT attachment_type, title, source_url FROM attachments WHERE message_id=? ORDER BY id', (row['id'],)):
                text += f"[{attachment['attachment_type']}] {attachment['title'] or ''} {attachment['source_url'] or ''}\n"
            output.write((text + '\n').encode('utf-8'))
        output.seek(0)
    except BaseException:
        output.close()
        raise
    finally:
        connection.execute('RELEASE SAVEPOINT viewer_export')
    return StreamingResponse(export_chunks(output), media_type='text/plain; charset=utf-8',
                             headers={'Content-Disposition': 'attachment; filename="weibo-chat-filtered.txt"'})
