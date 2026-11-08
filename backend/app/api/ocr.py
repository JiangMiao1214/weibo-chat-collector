"""Explicit local OCR controls and a separate, scoped image-text search index."""
from datetime import date
import sqlite3
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException,Query
from pydantic import BaseModel,Field
from ..database import get_connection
from ..settings import Settings,get_settings
from ..services import ocr_queue
from ..services.ocr_engine import engine_status,prepare_engine,OcrError
from ..services.insight_queries import scope_conditions
from ..services.archive_views import content_url

router=APIRouter(prefix='/api/ocr',tags=['ocr'])


@router.get('/status')
def status(account_id:int=Query(gt=0),group_id:int=Query(gt=0),
           connection:sqlite3.Connection=Depends(get_connection),settings:Settings=Depends(get_settings)):
    return {**ocr_queue.status(connection,account_id,group_id),**engine_status(settings)}


class Control(BaseModel):
    account_id:int=Field(gt=0)
    group_id:int=Field(gt=0)
    action:Literal['start','pause','resume','retry']


@router.post('/control')
def control(request:Control,connection:sqlite3.Connection=Depends(get_connection),settings:Settings=Depends(get_settings)):
    ocr_queue.scope(connection,request.account_id,request.group_id)
    if request.action in ('start','resume'):
        try:
            prepare_engine(settings)
        except OcrError as error:
            raise HTTPException(409,'本地 OCR 引擎不可用，请检查安装：'+str(error)) from None
    ocr_queue.control(connection,request.account_id,request.group_id,request.action)
    return ocr_queue.status(connection,request.account_id,request.group_id)


@router.get('/search')
def search(q:str=Query(min_length=1,max_length=200),account_id:int|None=Query(None,gt=0),
           group_id:int|None=Query(None,gt=0),date_from:date|None=None,date_to:date|None=None,
           deleted_only:bool=False,limit:int=Query(30,ge=1,le=100),offset:int=Query(0,ge=0),
           connection:sqlite3.Connection=Depends(get_connection)):
    if not q.strip():
        raise HTTPException(422,'请输入图片文字关键词')
    where,params=scope_conditions(connection,account_id,group_id,date_from,date_to,deleted_only)
    where+=" AND o.status='done' AND a.status='stored' AND o.content_hash=a.content_hash AND r.account_id=m.account_id"
    if len(q)>=3:
        where+=' AND r.id IN (SELECT rowid FROM ocr_text_index WHERE ocr_text_index MATCH ?)'
        params.append('"'+q.replace('"','""')+'"')
    where+=' AND instr(lower(r.text),lower(?))>0';params.append(q)
    joins='''FROM ocr_jobs o JOIN archive_jobs a ON a.id=o.archive_id JOIN messages m ON m.id=a.message_id
        JOIN ocr_results r ON r.id=o.result_id'''
    connection.execute('SAVEPOINT ocr_search')
    try:
        total=connection.execute(f'SELECT COUNT(*) {joins} WHERE {where}',params).fetchone()[0]
        rows=connection.execute(f'''SELECT m.id AS message_id,m.account_id,m.group_id,m.sent_at,m.is_deleted,
            a.id AS archive_id,substr(r.text,max(1,instr(lower(r.text),lower(?))-60),500) AS text,r.engine
            {joins} WHERE {where} ORDER BY m.sent_at DESC,m.id DESC,a.id LIMIT ? OFFSET ?''',[q,*params,limit,offset])
        items=[dict(row) for row in rows]
        for item in items:
            item['image_url']=content_url(item['archive_id'],item['account_id'],item['group_id'],deleted_only)
        return {'items':items,'total':total,'offset':offset,'has_more':offset+len(items)<total}
    finally:
        connection.execute('RELEASE SAVEPOINT ocr_search')
