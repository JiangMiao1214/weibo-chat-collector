"""Scoped dashboard and bounded, sanitized diagnostic timeline."""
import json
import sqlite3
from datetime import date
from typing import Literal
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from ..database import get_connection
from ..settings import get_settings, Settings
from ..services.insight_queries import scope_conditions, dashboard
from ..services.activity_logs import log_rows

router=APIRouter(prefix='/api/insights',tags=['insights'])


@router.get('/dashboard')
def get_dashboard(account_id:int|None=Query(None,gt=0),group_id:int|None=Query(None,gt=0),
                  date_from:date|None=None,date_to:date|None=None,deleted_only:bool=False,
                  period:Literal['day','week','month']='day',
                  connection:sqlite3.Connection=Depends(get_connection),settings:Settings=Depends(get_settings)):
    connection.execute('SAVEPOINT dashboard')
    try:
        where,params=scope_conditions(connection,account_id,group_id,date_from,date_to,deleted_only)
        return dashboard(connection,where,params,period,settings.attachments_dir)
    finally:
        connection.execute('RELEASE SAVEPOINT dashboard')


class LogFilters:
    def __init__(self,account_id:int|None=Query(None,gt=0),group_id:int|None=Query(None,gt=0),
                 date_from:date|None=None,date_to:date|None=None,
                 message_date_from:date|None=None,message_date_to:date|None=None,
                 category:Literal['','collection','archive','ocr']='',task_id:int|None=Query(None,gt=0),
                 action:str=Query('',max_length=80),result:str=Query('',max_length=80),error_code:str=Query('',max_length=80)):
        self.scope=(account_id,group_id,date_from,date_to)
        self.message_dates=(message_date_from,message_date_to)
        self.values={'category':category,'task_id':task_id,'action':action,'result':result,'error_code':error_code}

    def conditions(self,connection):
        where,params=scope_conditions(connection,*self.scope,deleted_only=None,alias='e',time_column='occurred_at')
        for field,value in self.values.items():
            if value:
                where+=f' AND e.{field}=?';params.append(value)
        if any(self.message_dates):
            message_where,message_params=scope_conditions(connection,date_from=self.message_dates[0],date_to=self.message_dates[1],deleted_only=None)
            where+=f' AND EXISTS(SELECT 1 FROM messages m WHERE m.id=e.message_id AND {message_where})'
            params.extend(message_params)
        return where,params


@router.get('/logs')
def logs(filters:LogFilters=Depends(),limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),
         connection:sqlite3.Connection=Depends(get_connection)):
    return log_rows(connection,*filters.conditions(connection),limit,offset)


@router.get('/logs/export')
def export_logs(filters:LogFilters=Depends(),connection:sqlite3.Connection=Depends(get_connection)):
    data=log_rows(connection,*filters.conditions(connection),10000,0)
    return Response(json.dumps(data,ensure_ascii=False,indent=2),media_type='application/json',
                    headers={'Content-Disposition':'attachment; filename="diagnostic-events.json"','Cache-Control':'no-store'})


@router.get('/events/{event_id}/resource')
def event_resource(event_id:int,account_id:int=Query(gt=0),group_id:int=Query(gt=0),deleted_only:bool=False,
                   connection:sqlite3.Connection=Depends(get_connection)):
    from fastapi import HTTPException
    from ..services.web_urls import safe_url
    row=connection.execute('''SELECT a.id,a.kind,a.status,a.source_url,f.failure_url
        FROM activity_events e JOIN archive_jobs a ON a.id=e.task_id AND e.category='archive'
        JOIN messages m ON m.id=a.message_id LEFT JOIN archive_failures f ON f.job_id=a.id
        WHERE e.id=? AND e.account_id=? AND e.group_id=? AND m.account_id=e.account_id
        AND m.group_id=e.group_id AND m.is_deleted=?''',(event_id,account_id,group_id,int(deleted_only))).fetchone()
    if row is None:
        raise HTTPException(404,'资源已不在当前账号、群聊或删除状态中')
    item=dict(row)
    item['source_url']=safe_url(item['source_url']);item['failure_url']=safe_url(item['failure_url'])
    return item
