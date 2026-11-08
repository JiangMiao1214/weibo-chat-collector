import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field

from ..database import get_connection
from ..settings import Settings, get_settings
from ..services.archive_queue import control, status, skip_link
from ..services.archive_views import serve_archive
from ..services.archive_repair import retry_job, inspect_files
from ..services.archive_policy import approve_host
from ..services.archive_thumbnails import thumbnail
from ..services.archive_manual import manual_snapshot

router = APIRouter(prefix='/api/archives', tags=['archives'])


class ArchiveScope(BaseModel):
    account_id: int = Field(gt=0)
    group_id: int = Field(gt=0)


class ArchiveControl(ArchiveScope):
    action: Literal['start', 'pause', 'resume']


@router.post('/control')
def archive_control(request: ArchiveControl, connection: sqlite3.Connection = Depends(get_connection)):
    return control(connection, request.account_id, request.group_id, request.action)


@router.get('/status')
def archive_status(account_id: int = Query(gt=0), group_id: int = Query(gt=0),
                   connection: sqlite3.Connection = Depends(get_connection)):
    return status(connection, account_id, group_id)


@router.post('/{job_id}/skip')
def archive_skip(request: ArchiveScope, job_id: int = Path(gt=0), connection: sqlite3.Connection = Depends(get_connection)):
    return skip_link(connection, job_id, request.account_id, request.group_id)


@router.get('/{job_id}/content')
def archive_content(job_id: int = Path(gt=0), account_id: int = Query(gt=0), group_id: int = Query(gt=0),
                    deleted_only: bool = False, connection: sqlite3.Connection = Depends(get_connection),
                    settings: Settings = Depends(get_settings)):
    row = connection.execute('''SELECT j.*,m.account_id,m.group_id FROM archive_jobs j
        JOIN messages m ON m.id=j.message_id WHERE j.id=? AND m.account_id=? AND m.group_id=? AND m.is_deleted=?''',
        (job_id, account_id, group_id, int(deleted_only))).fetchone()
    if not row:
        raise HTTPException(404, '此范围内没有该归档')
    return serve_archive(connection, row, settings.attachments_dir, deleted_only)


@router.post('/{job_id}/retry')
def archive_retry(request: ArchiveScope, job_id: int = Path(gt=0), connection: sqlite3.Connection = Depends(get_connection)):
    return retry_job(connection, job_id, request.account_id, request.group_id)


@router.post('/repair')
def archive_repair(request: ArchiveScope, connection: sqlite3.Connection = Depends(get_connection),
                   settings: Settings = Depends(get_settings)):
    return inspect_files(connection, settings.attachments_dir, request.account_id, request.group_id, repair=True)


@router.get('/integrity')
def archive_integrity(account_id: int = Query(gt=0), group_id: int = Query(gt=0),
                      connection: sqlite3.Connection = Depends(get_connection), settings: Settings = Depends(get_settings)):
    return inspect_files(connection, settings.attachments_dir, account_id, group_id)


@router.post('/{job_id}/trust-host')
def archive_trust_host(request: ArchiveScope, job_id: int = Path(gt=0), connection: sqlite3.Connection = Depends(get_connection)):
    approve_host(connection, job_id, request.account_id, request.group_id)
    return status(connection, request.account_id, request.group_id)


@router.get('/{job_id}/thumbnail')
def archive_thumbnail(job_id: int = Path(gt=0), account_id: int = Query(gt=0), group_id: int = Query(gt=0),
                      deleted_only: bool = False, connection: sqlite3.Connection = Depends(get_connection),
                      settings: Settings = Depends(get_settings)):
    row = connection.execute('''SELECT j.*,m.account_id FROM archive_jobs j JOIN messages m ON m.id=j.message_id
        WHERE j.id=? AND m.account_id=? AND m.group_id=? AND m.is_deleted=?''',
        (job_id, account_id, group_id, int(deleted_only))).fetchone()
    if not row:
        raise HTTPException(404, '此范围内没有该归档')
    return thumbnail(row, settings.attachments_dir)


class ManualSnapshot(ArchiveScope):
    text: str = Field(min_length=40, max_length=2 * 1024**2)


@router.post('/{job_id}/snapshot')
def archive_manual_snapshot(request: ManualSnapshot, job_id: int = Path(gt=0),
                            connection: sqlite3.Connection = Depends(get_connection), settings: Settings = Depends(get_settings)):
    return manual_snapshot(connection, settings, job_id, request.account_id, request.group_id, request.text)
