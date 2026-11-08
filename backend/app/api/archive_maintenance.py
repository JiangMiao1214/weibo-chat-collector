"""Explicit global maintenance, kept separate from per-group archive controls."""
import shutil
import sqlite3
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from ..database import get_connection
from ..settings import Settings, get_settings
from ..services import archive_maintenance as service

router = APIRouter(prefix='/api/archive-maintenance', tags=['archives'])


class CleanupRequest(BaseModel):
    token: str | None = None


def checked(action, *args, **kwargs):
    try:
        return action(*args, **kwargs)
    except (ValueError, OSError, sqlite3.DatabaseError) as error:
        message = str(error) if isinstance(error, ValueError) else '归档维护失败，请检查磁盘空间和备份格式'
        raise HTTPException(409, message) from None


@router.post('/backup')
def backup(connection: sqlite3.Connection = Depends(get_connection), settings: Settings = Depends(get_settings)):
    output = settings.database_path.parent / 'backups' / f'archive-{uuid4().hex}.zip'
    return checked(service.backup, connection, settings.attachments_dir, output)


@router.post('/cleanup')
def cleanup(request: CleanupRequest, connection: sqlite3.Connection = Depends(get_connection),
            settings: Settings = Depends(get_settings)):
    return checked(service.cleanup, connection, settings.attachments_dir, confirm=request.token)


@router.post('/restore')
async def restore(request: Request, settings: Settings = Depends(get_settings)):
    root = settings.database_path.parent / 'restored'
    root.mkdir(parents=True, exist_ok=True)
    identifier = uuid4().hex
    upload = root / ('.upload-' + identifier)
    total = 0
    try:
        with upload.open('xb') as output:
            async for chunk in request.stream():
                total += len(chunk)
                if total > 100 * 1024**3 or shutil.disk_usage(root).free < len(chunk) + 256 * 1024**2:
                    raise HTTPException(413, '备份过大或磁盘空间不足')
                output.write(chunk)
        return await run_in_threadpool(checked, service.restore, upload, root / identifier)
    finally:
        upload.unlink(missing_ok=True)
