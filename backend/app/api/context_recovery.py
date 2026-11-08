"""Explicit, bounded missing-reference collection; never starts network work here."""
from datetime import datetime, timedelta
from typing import Literal
import sqlite3
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from ..database import get_connection
from ..services.quote_context import scoped_source, find_quote_context
from ..services.target_management import require_enabled, write_lock
from ..services.collection_limits import require_account_cooldown_clear
from ..services.collection_runner import create_queued_api_job
from ..settings import get_settings

router = APIRouter(prefix='/api/messages', tags=['messages'])


class RecoveryRequest(BaseModel):
    account_id: int = Field(gt=0)
    group_id: int = Field(gt=0)
    range_start: datetime
    range_end: datetime
    confirmed: Literal[True]


def validate_range(request, source):
    start, end = request.range_start, request.range_end
    if start.tzinfo or end.tzinfo:
        raise HTTPException(422, '请使用采集配置时区的本地时间，不带时区偏移')
    if not timedelta(0) < end - start <= timedelta(days=7):
        raise HTTPException(422, '补采集范围必须大于零且不超过 7 天')
    if end > datetime.fromisoformat(source['sent_at']):
        raise HTTPException(422, '补采集结束时间不能晚于引用消息')


@router.post('/{message_id}/recover-context', status_code=202)
def recover(message_id: int, request: RecoveryRequest,
            connection: sqlite3.Connection = Depends(get_connection)):
    write_lock(connection)
    source = scoped_source(connection, message_id, request.account_id, request.group_id, False)
    if source is None:
        raise HTTPException(404, '引用消息不在当前账号和群的未删除消息中')
    validate_range(request, source)
    if find_quote_context(connection, source)['status'] != 'not_found':
        raise HTTPException(409, '本地已有匹配、存在歧义或没有引用，请先核对本地原文')
    require_enabled(connection, request.account_id, request.group_id)
    require_account_cooldown_clear(connection,request.account_id)
    group = connection.execute('SELECT source_group_id FROM chat_groups WHERE id=?', (request.group_id,)).fetchone()
    if not group['source_group_id']:
        raise HTTPException(409, '请先绑定此群的微博群 ID')
    times = [value.strftime('%Y-%m-%d %H:%M:%S') for value in (request.range_start, request.range_end)]
    existing = connection.execute('''SELECT id FROM collection_jobs WHERE account_id=? AND group_id=?
        AND range_start=? AND range_end=? AND status IN ('queued','awaiting_confirmation','running','stopped')''',
        (request.account_id, request.group_id, *times)).fetchone()
    if existing:
        raise HTTPException(409, f'已有未完成任务 #{existing[0]}，请在采集监控查看或续传')
    settings = get_settings()
    job = create_queued_api_job(connection, account_id=request.account_id, group_id=request.group_id,
        source_group_id=group[0], range_start=request.range_start, range_end=request.range_end,
        timezone_name=settings.weibo_api_timezone, page_size=settings.weibo_api_page_size)
    connection.execute('UPDATE collection_jobs SET recovery_max_pages=20 WHERE id=?', (job,))
    return {'id': job, 'status': 'queued', 'max_pages': 20,
            'message': '已排队；进入采集监控后须再次确认启动。每次最多 20 页，未覆盖范围会停止。'}
