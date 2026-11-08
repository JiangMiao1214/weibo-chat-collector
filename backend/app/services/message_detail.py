from .archive_metadata import enrich_archives
import sqlite3
from typing import Any

from fastapi import HTTPException


def row_to_dict(row):
    return dict(row)


def read_message_detail(
    message_id: int,
    include_deleted: bool = False,
    connection: sqlite3.Connection = None,
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
    return enrich_archives(connection, [result])[0]
