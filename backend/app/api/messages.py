import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..database import get_connection


router = APIRouter(prefix="/api", tags=["messages"])


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


class MessageFilterPayload(BaseModel):
    account_id: int | None = None
    account: str | None = None
    group_id: int | None = None
    group: str | None = None
    user: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    keyword: str | None = None
    message_type: str | None = None
    has_attachment: bool | None = None


class DeletePreviewRequest(BaseModel):
    filters: MessageFilterPayload


class SoftDeleteRequest(BaseModel):
    filters: MessageFilterPayload
    confirm: bool
    delete_attachments: bool = False
    created_by: str | None = None


class RestorePreviewRequest(BaseModel):
    filters: MessageFilterPayload


class RestoreRequest(BaseModel):
    filters: MessageFilterPayload
    confirm: bool
    created_by: str | None = None


class HardDeletePreviewRequest(BaseModel):
    filters: MessageFilterPayload


class HardDeleteRequest(BaseModel):
    filters: MessageFilterPayload
    confirm: bool
    delete_attachment_files: bool = False
    created_by: str | None = None


def build_message_filters(
    account_id: int | None,
    account: str | None,
    group_id: int | None,
    group: str | None,
    user: str | None,
    date_from: str | None,
    date_to: str | None,
    keyword: str | None,
    message_type: str | None,
    has_attachment: bool | None,
    include_deleted: bool,
    deleted_only: bool = False,
) -> tuple[list[str], list[Any]]:
    filters = []
    params: list[Any] = []

    if deleted_only:
        filters.append("m.is_deleted = 1")
    elif not include_deleted:
        filters.append("m.is_deleted = 0")

    if account_id is not None:
        filters.append("m.account_id = ?")
        params.append(account_id)

    if account:
        filters.append("wa.display_name LIKE ?")
        params.append(f"%{account}%")

    if group_id is not None:
        filters.append("m.group_id = ?")
        params.append(group_id)

    if group:
        filters.append("cg.name LIKE ?")
        params.append(f"%{group}%")

    if user:
        filters.append("(cu.display_name LIKE ? OR cu.source_user_id LIKE ?)")
        params.extend([f"%{user}%", f"%{user}%"])

    if date_from:
        filters.append("m.sent_at >= ?")
        params.append(date_from)

    if date_to:
        filters.append("m.sent_at <= ?")
        params.append(date_to)

    if message_type:
        filters.append("m.message_type = ?")
        params.append(message_type)

    if has_attachment is True:
        filters.append(
            "EXISTS (SELECT 1 FROM attachments a WHERE a.message_id = m.id)"
        )
    elif has_attachment is False:
        filters.append(
            "NOT EXISTS (SELECT 1 FROM attachments a WHERE a.message_id = m.id)"
        )

    if keyword:
        filters.append(
            """
            (
                m.content_text LIKE ?
                OR m.normalized_text LIKE ?
                OR EXISTS (
                    SELECT 1
                    FROM attachments a
                    WHERE a.message_id = m.id
                      AND (
                        a.file_name LIKE ?
                        OR a.title LIKE ?
                        OR a.description LIKE ?
                        OR a.source_url LIKE ?
                      )
                )
            )
            """
        )
        keyword_value = f"%{keyword}%"
        params.extend(
            [
                keyword_value,
                keyword_value,
                keyword_value,
                keyword_value,
                keyword_value,
                keyword_value,
            ]
        )

    return filters, params


def build_where_sql(filters: list[str]) -> str:
    return f"WHERE {' AND '.join(filters)}" if filters else ""


def build_filters_from_payload(
    payload: MessageFilterPayload,
    include_deleted: bool = False,
    deleted_only: bool = False,
) -> tuple[list[str], list[Any]]:
    return build_message_filters(
        account_id=payload.account_id,
        account=payload.account,
        group_id=payload.group_id,
        group=payload.group,
        user=payload.user,
        date_from=payload.date_from,
        date_to=payload.date_to,
        keyword=payload.keyword,
        message_type=payload.message_type,
        has_attachment=payload.has_attachment,
        include_deleted=include_deleted,
        deleted_only=deleted_only,
    )


def count_matching_messages(
    connection: sqlite3.Connection,
    filters: list[str],
    params: list[Any],
) -> int:
    where_sql = build_where_sql(filters)
    return int(
        connection.execute(
            f"""
            SELECT COUNT(*)
            FROM messages m
            JOIN weibo_accounts wa ON wa.id = m.account_id
            JOIN chat_groups cg ON cg.id = m.group_id
            JOIN chat_users cu ON cu.id = m.user_id
            {where_sql}
            """,
            params,
        ).fetchone()[0]
    )


@router.get("/filter-options")
def get_filter_options(
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict[str, Any]:
    accounts = [
        row_to_dict(row)
        for row in connection.execute(
            """
            SELECT id, display_name, auth_type, login_profile_name, is_active
            FROM weibo_accounts
            ORDER BY id
            """
        )
    ]
    groups = [
        row_to_dict(row)
        for row in connection.execute(
            """
            SELECT
                cg.id,
                cg.account_id,
                cg.name,
                cg.source_group_id,
                wa.display_name AS account_name,
                cg.is_active
            FROM chat_groups cg
            JOIN weibo_accounts wa ON wa.id = cg.account_id
            ORDER BY wa.id, cg.id
            """
        )
    ]
    users = [
        row_to_dict(row)
        for row in connection.execute(
            """
            SELECT id, display_name, source_user_id
            FROM chat_users
            ORDER BY display_name
            """
        )
    ]
    message_types = [
        row[0]
        for row in connection.execute(
            """
            SELECT DISTINCT message_type
            FROM messages
            ORDER BY message_type
            """
        )
    ]

    return {
        "accounts": accounts,
        "groups": groups,
        "users": users,
        "message_types": message_types,
    }


@router.get("/messages")
def list_messages(
    account_id: int | None = None,
    account: str | None = None,
    group_id: int | None = None,
    group: str | None = None,
    user: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    keyword: str | None = None,
    message_type: str | None = None,
    has_attachment: bool | None = None,
    include_deleted: bool = False,
    deleted_only: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    before_sent_at: str | None = None,
    before_id: int | None = None,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict[str, Any]:
    filters, params = build_message_filters(
        account_id=account_id,
        account=account,
        group_id=group_id,
        group=group,
        user=user,
        date_from=date_from,
        date_to=date_to,
        keyword=keyword,
        message_type=message_type,
        has_attachment=has_attachment,
        include_deleted=include_deleted,
        deleted_only=deleted_only,
    )
    total = count_matching_messages(connection, filters, params)

    cursor_filters = [*filters]
    cursor_params = [*params]
    if before_sent_at and before_id is not None:
        cursor_filters.append("(m.sent_at < ? OR (m.sent_at = ? AND m.id < ?))")
        cursor_params.extend([before_sent_at, before_sent_at, before_id])
    elif before_sent_at:
        cursor_filters.append("m.sent_at < ?")
        cursor_params.append(before_sent_at)

    where_sql = build_where_sql(cursor_filters)
    fetch_limit = limit + 1

    rows = connection.execute(
        f"""
        SELECT
            m.id,
            m.account_id,
            wa.display_name AS account_name,
            m.group_id,
            cg.name AS group_name,
            m.user_id,
            cu.display_name AS sender_name,
            cu.source_user_id,
            m.source_message_id,
            m.sent_at,
            m.message_type,
            m.content_text,
            m.is_deleted,
            m.deleted_at,
            (
                SELECT COUNT(*)
                FROM attachments a
                WHERE a.message_id = m.id
            ) AS attachment_count
        FROM messages m
        JOIN weibo_accounts wa ON wa.id = m.account_id
        JOIN chat_groups cg ON cg.id = m.group_id
        JOIN chat_users cu ON cu.id = m.user_id
        {where_sql}
        ORDER BY m.sent_at DESC, m.id DESC
        LIMIT ? OFFSET ?
        """,
        [*cursor_params, fetch_limit, 0 if before_sent_at else offset],
    ).fetchall()

    has_more = len(rows) > limit
    page_rows = rows[:limit]
    items = [row_to_dict(row) for row in page_rows]
    next_cursor = None
    if has_more and items:
        last_item = items[-1]
        next_cursor = {
            "before_sent_at": last_item["sent_at"],
            "before_id": last_item["id"],
        }

    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": 0 if before_sent_at else offset,
        "has_more": has_more,
        "next_cursor": next_cursor,
    }


@router.post("/messages/delete-preview")
def preview_delete_messages(
    request: DeletePreviewRequest,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict[str, Any]:
    filters, params = build_filters_from_payload(request.filters, include_deleted=False)
    preview_count = count_matching_messages(connection, filters, params)
    return {
        "preview_count": preview_count,
        "delete_mode": "soft",
        "filters": request.filters.model_dump(),
    }


@router.post("/messages/soft-delete")
def soft_delete_messages(
    request: SoftDeleteRequest,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict[str, Any]:
    if not request.confirm:
        raise HTTPException(status_code=400, detail="Delete confirmation is required")

    filters, params = build_filters_from_payload(request.filters, include_deleted=False)
    where_sql = build_where_sql(filters)
    preview_count = count_matching_messages(connection, filters, params)

    cursor = connection.execute(
        """
        INSERT INTO deletion_jobs (
            account_id,
            group_id,
            filter_json,
            preview_count,
            deleted_count,
            delete_attachments,
            status,
            created_by
        )
        VALUES (?, ?, ?, ?, 0, ?, 'running', ?)
        """,
        (
            request.filters.account_id,
            request.filters.group_id,
            request.filters.model_dump_json(),
            preview_count,
            1 if request.delete_attachments else 0,
            request.created_by,
        ),
    )
    deletion_job_id = int(cursor.lastrowid)

    delete_cursor = connection.execute(
        f"""
        UPDATE messages
        SET is_deleted = 1,
            deleted_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE id IN (
            SELECT m.id
            FROM messages m
            JOIN weibo_accounts wa ON wa.id = m.account_id
            JOIN chat_groups cg ON cg.id = m.group_id
            JOIN chat_users cu ON cu.id = m.user_id
            {where_sql}
        )
        """,
        params,
    )
    deleted_count = int(delete_cursor.rowcount)

    connection.execute(
        """
        UPDATE deletion_jobs
        SET deleted_count = ?,
            status = 'completed',
            finished_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (deleted_count, deletion_job_id),
    )

    return {
        "deletion_job_id": deletion_job_id,
        "preview_count": preview_count,
        "deleted_count": deleted_count,
        "delete_mode": "soft",
    }


@router.post("/messages/restore-preview")
def preview_restore_messages(
    request: RestorePreviewRequest,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict[str, Any]:
    filters, params = build_filters_from_payload(
        request.filters,
        include_deleted=True,
        deleted_only=True,
    )
    preview_count = count_matching_messages(connection, filters, params)
    return {
        "preview_count": preview_count,
        "restore_mode": "soft_delete_restore",
        "filters": request.filters.model_dump(),
    }


@router.post("/messages/restore")
def restore_messages(
    request: RestoreRequest,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict[str, Any]:
    if not request.confirm:
        raise HTTPException(status_code=400, detail="Restore confirmation is required")

    filters, params = build_filters_from_payload(
        request.filters,
        include_deleted=True,
        deleted_only=True,
    )
    where_sql = build_where_sql(filters)
    preview_count = count_matching_messages(connection, filters, params)

    restore_cursor = connection.execute(
        f"""
        UPDATE messages
        SET is_deleted = 0,
            deleted_at = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id IN (
            SELECT m.id
            FROM messages m
            JOIN weibo_accounts wa ON wa.id = m.account_id
            JOIN chat_groups cg ON cg.id = m.group_id
            JOIN chat_users cu ON cu.id = m.user_id
            {where_sql}
        )
        """,
        params,
    )
    restored_count = int(restore_cursor.rowcount)

    return {
        "preview_count": preview_count,
        "restored_count": restored_count,
        "restore_mode": "soft_delete_restore",
    }


@router.post("/messages/hard-delete-preview")
def preview_hard_delete_messages(
    request: HardDeletePreviewRequest,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict[str, Any]:
    filters, params = build_filters_from_payload(
        request.filters,
        include_deleted=True,
        deleted_only=True,
    )
    preview_count = count_matching_messages(connection, filters, params)
    attachment_count = int(
        connection.execute(
            f"""
            SELECT COUNT(*)
            FROM attachments
            WHERE message_id IN (
                SELECT m.id
                FROM messages m
                JOIN weibo_accounts wa ON wa.id = m.account_id
                JOIN chat_groups cg ON cg.id = m.group_id
                JOIN chat_users cu ON cu.id = m.user_id
                {build_where_sql(filters)}
            )
            """,
            params,
        ).fetchone()[0]
    )
    return {
        "preview_count": preview_count,
        "attachment_record_count": attachment_count,
        "delete_mode": "hard",
        "delete_attachment_files": False,
        "filters": request.filters.model_dump(),
    }


@router.post("/messages/hard-delete")
def hard_delete_messages(
    request: HardDeleteRequest,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict[str, Any]:
    if not request.confirm:
        raise HTTPException(status_code=400, detail="Hard delete confirmation is required")

    filters, params = build_filters_from_payload(
        request.filters,
        include_deleted=True,
        deleted_only=True,
    )
    where_sql = build_where_sql(filters)
    preview_count = count_matching_messages(connection, filters, params)

    message_id_subquery = f"""
        SELECT m.id
        FROM messages m
        JOIN weibo_accounts wa ON wa.id = m.account_id
        JOIN chat_groups cg ON cg.id = m.group_id
        JOIN chat_users cu ON cu.id = m.user_id
        {where_sql}
    """

    attachment_cursor = connection.execute(
        f"""
        DELETE FROM attachments
        WHERE message_id IN ({message_id_subquery})
        """,
        params,
    )
    connection.execute(
        f"""
        DELETE FROM search_indexes
        WHERE message_id IN ({message_id_subquery})
        """,
        params,
    )
    message_cursor = connection.execute(
        f"""
        DELETE FROM messages
        WHERE id IN ({message_id_subquery})
        """,
        params,
    )

    return {
        "preview_count": preview_count,
        "deleted_count": int(message_cursor.rowcount),
        "attachment_record_count": int(attachment_cursor.rowcount),
        "delete_mode": "hard",
        "delete_attachment_files": False,
    }


@router.get("/messages/{message_id}")
def get_message_detail(
    message_id: int,
    include_deleted: bool = False,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict[str, Any]:
    deleted_filter = "" if include_deleted else "AND m.is_deleted = 0"
    row = connection.execute(
        f"""
        SELECT
            m.*,
            wa.display_name AS account_name,
            cg.name AS group_name,
            cu.display_name AS sender_name,
            cu.source_user_id
        FROM messages m
        JOIN weibo_accounts wa ON wa.id = m.account_id
        JOIN chat_groups cg ON cg.id = m.group_id
        JOIN chat_users cu ON cu.id = m.user_id
        WHERE m.id = ?
        {deleted_filter}
        """,
        (message_id,),
    ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Message not found")

    attachments = [
        row_to_dict(attachment)
        for attachment in connection.execute(
            """
            SELECT
                id,
                attachment_type,
                source_url,
                local_path,
                file_name,
                mime_type,
                file_size,
                content_hash,
                title,
                description,
                download_status,
                downloaded_at,
                created_at
            FROM attachments
            WHERE message_id = ?
            ORDER BY id
            """,
            (message_id,),
        )
    ]

    result = row_to_dict(row)
    result["attachments"] = attachments
    return result
