"""Explicit local account/group management with write-serialized lifecycle changes."""
import sqlite3
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from ..database import get_connection
from ..settings import get_settings
from ..services.target_management import (account_row, check_binding, check_group_slot, ensure_group_identity_can_change,
    overview, pause_target, require_quiet, write_lock)

from ..services.target_lifecycle import lifecycle_guard
from ..services.account_deletion import deletion_preview, delete_empty_account

router = APIRouter(prefix='/api/management', tags=['management'], dependencies=[Depends(lifecycle_guard)])


class AccountForm(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    display_name: str = Field(min_length=1, max_length=100)
    notes: str = Field(default='', max_length=2000)
    is_active: bool = True


class DeleteAccountForm(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirmed: bool
    preview_token: str = Field(min_length=64, max_length=64)


class GroupForm(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    account_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default='', max_length=2000)
    source_group_id: str | None = Field(default=None, max_length=64)
    is_active: bool = True

    @field_validator('source_group_id')
    @classmethod
    def numeric_id(cls, value):
        if value and (not value.isascii() or not value.isdigit()):
            raise ValueError('微博群 ID 必须是数字')
        return value or None


@router.get('')
def get_overview(connection: sqlite3.Connection = Depends(get_connection), settings=Depends(get_settings)):
    return overview(connection, settings.weibo_api_auth_dir)


@router.post('/accounts', status_code=201)
def create_account(form: AccountForm, connection: sqlite3.Connection = Depends(get_connection)):
    write_lock(connection)
    cursor = connection.execute('INSERT INTO weibo_accounts(display_name,notes,is_active) VALUES(?,?,?)',
        (form.display_name, form.notes, int(form.is_active)))
    return {'id': cursor.lastrowid}


@router.get('/accounts/{account_id}/delete-preview')
def preview_account_deletion(account_id: int = Path(ge=1),
                             connection: sqlite3.Connection = Depends(get_connection), settings=Depends(get_settings)):
    return deletion_preview(connection, account_id, settings.weibo_api_auth_dir)


@router.delete('/accounts/{account_id}')
def delete_account(form: DeleteAccountForm, account_id: int = Path(ge=1),
                   connection: sqlite3.Connection = Depends(get_connection), settings=Depends(get_settings)):
    if not form.confirmed:
        raise HTTPException(422, '需要明确确认删除账号')
    return delete_empty_account(connection, account_id, settings.weibo_api_auth_dir, form.preview_token)


@router.patch('/accounts/{account_id}')
def edit_account(form: AccountForm, account_id: int = Path(ge=1), connection: sqlite3.Connection = Depends(get_connection)):
    old = account_row(connection, account_id)
    if bool(old['is_active']) != form.is_active:
        require_quiet(connection, account_id)
        if not form.is_active:
            pause_target(connection, account_id)
    write_lock(connection)
    connection.execute('''UPDATE weibo_accounts SET display_name=?,notes=?,is_active=?,updated_at=CURRENT_TIMESTAMP WHERE id=?''',
        (form.display_name, form.notes, int(form.is_active), account_id))
    return {'id': account_id}


@router.delete('/accounts/{account_id}/credentials')
def clear_credentials(account_id: int = Path(ge=1), confirmed: bool = Query(),
                      connection: sqlite3.Connection = Depends(get_connection), settings=Depends(get_settings)):
    if not confirmed:
        raise HTTPException(422, '需要明确确认清除凭据')
    account_row(connection, account_id)
    require_quiet(connection, account_id)
    if connection.execute('''SELECT 1 FROM archive_jobs j JOIN messages m ON m.id=j.message_id
        WHERE m.account_id=? AND j.status='downloading' LIMIT 1''', (account_id,)).fetchone():
        raise HTTPException(409, '请先暂停归档并等待当前下载退出，再清除凭据')
    pause_target(connection, account_id)
    try:
        (settings.weibo_api_auth_dir / f'account-{account_id}.cookies.json').unlink(missing_ok=True)
    except OSError as error:
        raise HTTPException(409, '无法清除本机凭据文件，请检查目录权限') from error
    connection.execute('UPDATE weibo_accounts SET login_profile_name=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?', (account_id,))
    return {'id': account_id, 'credentials_removed': True}


@router.post('/groups', status_code=201)
def create_group(form: GroupForm, connection: sqlite3.Connection = Depends(get_connection)):
    write_lock(connection)
    account_row(connection, form.account_id)
    check_group_slot(connection, form.account_id)
    check_binding(connection, form.account_id, form.source_group_id)
    cursor = connection.execute('''INSERT INTO chat_groups(account_id,name,description,source_group_id,is_active)
        VALUES(?,?,?,?,?)''', (form.account_id, form.name, form.description, form.source_group_id, int(form.is_active)))
    return {'id': cursor.lastrowid, 'source_group_id': form.source_group_id}


@router.patch('/groups/{group_id}')
def edit_group(form: GroupForm, group_id: int = Path(ge=1), connection: sqlite3.Connection = Depends(get_connection)):
    old = connection.execute('SELECT * FROM chat_groups WHERE id=?', (group_id,)).fetchone()
    if old is None:
        raise HTTPException(404, '群聊不存在')
    if old['account_id'] != form.account_id:
        raise HTTPException(409, '群聊所属账号不能变更，请在目标账号下新建群聊')
    if old['source_group_id'] != form.source_group_id or bool(old['is_active']) != form.is_active:
        require_quiet(connection, form.account_id, group_id)
    write_lock(connection)
    ensure_group_identity_can_change(connection, group_id=group_id,
        current_source_group_id=old['source_group_id'], requested_source_group_id=form.source_group_id)
    check_binding(connection, form.account_id, form.source_group_id, group_id)
    if not form.is_active:
        pause_target(connection, form.account_id, group_id)
    connection.execute('''UPDATE chat_groups SET name=?,description=?,source_group_id=?,is_active=?,updated_at=CURRENT_TIMESTAMP WHERE id=?''',
        (form.name, form.description, form.source_group_id, int(form.is_active), group_id))
    return {'id': group_id}
