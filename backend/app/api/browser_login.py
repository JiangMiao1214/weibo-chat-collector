from __future__ import annotations

import ipaddress
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from ..database import get_connection
from ..services.browser_login_manager import (
    BrowserLoginSession,
    BrowserSessionBusyError,
    BrowserSessionNotFoundError,
    BrowserSessionStateError,
    get_browser_login_manager,
)
from .collection_jobs import ensure_account_and_group
from .weibo_api import ensure_group_identity_can_change


router = APIRouter(prefix="/api/weibo-api/browser-sessions", tags=["weibo-browser-login"])


class StartBrowserSessionRequest(BaseModel):
    account_id: int
    group_id: int | None = None


class ConfirmBrowserGroupRequest(BaseModel):
    candidate_source_group_id: str

    @field_validator("candidate_source_group_id")
    @classmethod
    def validate_source_group_id(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped.isdigit():
            raise ValueError("candidate_source_group_id must contain digits only")
        return stripped


def require_loopback_client(request: Request) -> None:
    host = request.client.host if request.client is not None else ""
    if host in {"localhost", "testclient"}:
        return
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    raise HTTPException(
        status_code=403,
        detail="Browser login is only available from this computer over a loopback address.",
    )


def ensure_browser_session_target(
    connection: sqlite3.Connection,
    *,
    account_id: int,
    group_id: int | None,
) -> None:
    account = connection.execute(
        "SELECT id, is_active FROM weibo_accounts WHERE id = ?",
        (account_id,),
    ).fetchone()
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    if not int(account["is_active"]):
        raise HTTPException(status_code=400, detail="Account is inactive")
    if group_id is not None:
        ensure_account_and_group(connection, account_id, group_id)
        group = connection.execute(
            "SELECT is_active FROM chat_groups WHERE id = ?",
            (group_id,),
        ).fetchone()
        if group is None or not int(group["is_active"]):
            raise HTTPException(status_code=400, detail="Group is inactive")


def ensure_collection_is_idle(connection: sqlite3.Connection) -> None:
    active_job = connection.execute(
        """
        SELECT id, status
        FROM collection_jobs
        WHERE collector_type = 'weibo_api_v2'
          AND status IN ('awaiting_confirmation', 'running')
        ORDER BY id LIMIT 1
        """
    ).fetchone()
    if active_job is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Collection task #{int(active_job['id'])} is {str(active_job['status'])}. "
                "Stop or finish it before opening a browser login session."
            ),
        )


@router.post("", status_code=202)
def start_browser_session(
    payload: StartBrowserSessionRequest,
    request: Request,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict[str, Any]:
    require_loopback_client(request)
    ensure_browser_session_target(
        connection,
        account_id=payload.account_id,
        group_id=payload.group_id,
    )
    ensure_collection_is_idle(connection)
    try:
        return get_browser_login_manager().start_session(
            account_id=payload.account_id,
            group_id=payload.group_id,
        )
    except BrowserSessionBusyError as error:
        raise HTTPException(
            status_code=409,
            detail={"message": str(error), "session": error.session},
        ) from error
    except BrowserSessionStateError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.get("/active")
def get_active_browser_session(request: Request) -> dict[str, Any]:
    require_loopback_client(request)
    return {"session": get_browser_login_manager().get_active_session()}


@router.get("/{session_id}")
def get_browser_session(session_id: str, request: Request) -> dict[str, Any]:
    require_loopback_client(request)
    try:
        return get_browser_login_manager().get_session(session_id)
    except BrowserSessionNotFoundError as error:
        raise HTTPException(status_code=404, detail="Browser session not found") from error


@router.post("/{session_id}/rediscover")
def rediscover_browser_group(session_id: str, request: Request) -> dict[str, Any]:
    require_loopback_client(request)
    try:
        return get_browser_login_manager().rediscover(session_id)
    except BrowserSessionNotFoundError as error:
        raise HTTPException(status_code=404, detail="Browser session not found") from error
    except BrowserSessionStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/{session_id}/confirm-group")
def confirm_browser_group(
    session_id: str,
    payload: ConfirmBrowserGroupRequest,
    request: Request,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict[str, Any]:
    require_loopback_client(request)

    def bind(session: BrowserLoginSession) -> None:
        if session.group_id is None:
            raise BrowserSessionStateError("group_not_observed")
        try:
            connection.execute("BEGIN IMMEDIATE")
            ensure_browser_session_target(
                connection,
                account_id=session.account_id,
                group_id=session.group_id,
            )
            current = connection.execute(
                "SELECT source_group_id FROM chat_groups WHERE id = ?",
                (session.group_id,),
            ).fetchone()
            ensure_group_identity_can_change(
                connection,
                group_id=session.group_id,
                current_source_group_id=current["source_group_id"],
                requested_source_group_id=payload.candidate_source_group_id,
            )
            duplicate = connection.execute(
                """
                SELECT id FROM chat_groups
                WHERE account_id = ? AND source_group_id = ? AND id <> ?
                LIMIT 1
                """,
                (
                    session.account_id,
                    payload.candidate_source_group_id,
                    session.group_id,
                ),
            ).fetchone()
            if duplicate is not None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error_code": "group_binding_conflict",
                        "message": "该账号下已有其他群聊绑定此微博群 ID。",
                    },
                )
            connection.execute(
                """
                UPDATE chat_groups
                SET source_group_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND account_id = ?
                """,
                (
                    payload.candidate_source_group_id,
                    session.group_id,
                    session.account_id,
                ),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise HTTPException(status_code=409, detail="Group binding changed concurrently.")
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    try:
        return get_browser_login_manager().confirm_group(
            session_id,
            payload.candidate_source_group_id,
            bind,
        )
    except BrowserSessionNotFoundError as error:
        raise HTTPException(status_code=404, detail="Browser session not found") from error
    except BrowserSessionStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/{session_id}/cancel")
def cancel_browser_session(session_id: str, request: Request) -> dict[str, Any]:
    require_loopback_client(request)
    try:
        return get_browser_login_manager().cancel_session(session_id)
    except BrowserSessionNotFoundError as error:
        raise HTTPException(status_code=404, detail="Browser session not found") from error
