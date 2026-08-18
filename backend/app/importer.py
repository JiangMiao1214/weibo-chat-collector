import csv
import hashlib
import json
import shutil
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .services.message_filter import MessageFilterKind, classify_message_filter


@dataclass
class ImportSummary:
    account: str
    group: str
    source_file: str
    total_count: int = 0
    inserted_count: int = 0
    skipped_count: int = 0
    red_packet_count: int = 0
    filtered_system_notice_count: int = 0
    duplicate_count: int = 0
    attachment_count: int = 0


def import_file(
    db_path: Path,
    source_path: Path,
    account_name: str | None = None,
    group_name: str | None = None,
    attachments_dir: Path | None = None,
    copy_local_attachments: bool = False,
) -> ImportSummary:
    payload = read_source_file(source_path)
    account = account_name or payload.get("account")
    group = group_name or payload.get("group")
    messages = payload["messages"]

    if not account:
        raise ValueError("Missing account. Provide it in the file or via --account.")
    if not group:
        raise ValueError("Missing group. Provide it in the file or via --group.")

    summary = ImportSummary(account=account, group=group, source_file=str(source_path))
    summary.total_count = len(messages)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if attachments_dir is not None:
        attachments_dir.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        account_id = ensure_account(connection, account)
        group_id = ensure_group(connection, account_id, group)
        import_batch_id = create_import_batch(connection, account_id, group_id, source_path)
        collection_job_id = create_collection_job(
            connection,
            account_id,
            group_id,
            messages,
        )

        for raw_message in messages:
            filter_kind = classify_import_filter(raw_message)
            if filter_kind == "red_packet":
                summary.red_packet_count += 1
                summary.skipped_count += 1
                continue
            if filter_kind == "fansgroup_badge":
                summary.filtered_system_notice_count += 1
                summary.skipped_count += 1
                continue

            user_id = ensure_user(connection, raw_message)
            ensure_group_member(connection, group_id, user_id, raw_message)

            content_hash = build_content_hash(raw_message)
            if is_duplicate_message(
                connection,
                account_id,
                group_id,
                user_id,
                raw_message,
                content_hash,
            ):
                summary.duplicate_count += 1
                summary.skipped_count += 1
                continue

            message_id = insert_message(
                connection,
                account_id,
                group_id,
                user_id,
                collection_job_id,
                raw_message,
                content_hash,
            )
            summary.attachment_count += insert_attachments(
                connection,
                message_id,
                raw_message.get("attachments", []),
                attachments_dir,
                copy_local_attachments,
            )
            summary.inserted_count += 1

        finish_import_batch(
            connection,
            import_batch_id,
            summary.inserted_count,
            summary.skipped_count,
        )
        finish_collection_job(connection, collection_job_id, summary)

    return summary


def read_source_file(source_path: Path) -> dict[str, Any]:
    suffix = source_path.suffix.lower()
    if suffix == ".json":
        return read_json_file(source_path)
    if suffix == ".csv":
        return read_csv_file(source_path)
    raise ValueError(f"Unsupported import file type: {suffix}")


def read_json_file(source_path: Path) -> dict[str, Any]:
    data = json.loads(source_path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return {"messages": data}
    if "messages" not in data:
        raise ValueError("JSON import file must include a messages array.")
    return data


def read_csv_file(source_path: Path) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    account = None
    group = None

    with source_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            account = account or row.get("account")
            group = group or row.get("group")
            attachments = parse_attachments_json(row.get("attachments_json"))
            messages.append(
                {
                    "source_message_id": row.get("source_message_id") or None,
                    "sender_name": row.get("sender_name") or row.get("user_name"),
                    "source_user_id": row.get("source_user_id") or None,
                    "sent_at": row.get("sent_at"),
                    "message_type": row.get("message_type") or "text",
                    "content_text": row.get("content_text") or "",
                    "attachments": attachments,
                    "raw_payload": row,
                }
            )

    return {"account": account, "group": group, "messages": messages}


def parse_attachments_json(value: str | None) -> list[dict[str, Any]]:
    if not value:
        return []
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        raise ValueError("attachments_json must be a JSON array.")
    return parsed


def ensure_account(connection: sqlite3.Connection, display_name: str) -> int:
    row = connection.execute(
        "SELECT id FROM weibo_accounts WHERE display_name = ?",
        (display_name,),
    ).fetchone()
    if row:
        return int(row[0])
    cursor = connection.execute(
        "INSERT INTO weibo_accounts (display_name) VALUES (?)",
        (display_name,),
    )
    return int(cursor.lastrowid)


def ensure_group(connection: sqlite3.Connection, account_id: int, name: str) -> int:
    row = connection.execute(
        "SELECT id FROM chat_groups WHERE account_id = ? AND name = ?",
        (account_id, name),
    ).fetchone()
    if row:
        return int(row[0])
    cursor = connection.execute(
        "INSERT INTO chat_groups (account_id, name) VALUES (?, ?)",
        (account_id, name),
    )
    return int(cursor.lastrowid)


def ensure_user(connection: sqlite3.Connection, message: dict[str, Any]) -> int:
    source_user_id = message.get("source_user_id")
    display_name = message.get("sender_name") or "unknown_user"
    avatar_url = message.get("avatar_url")

    if source_user_id:
        row = connection.execute(
            """
            SELECT
                cu.id,
                (
                    SELECT MAX(gm.last_seen_at)
                    FROM group_members gm
                    WHERE gm.user_id = cu.id
                ) AS latest_seen_at
            FROM chat_users cu
            WHERE cu.source_user_id = ?
            """,
            (source_user_id,),
        ).fetchone()
        if row:
            sent_at = message.get("sent_at")
            latest_seen_at = row[1]
            if latest_seen_at is None or (
                sent_at is not None and str(sent_at) >= str(latest_seen_at)
            ):
                connection.execute(
                    """
                    UPDATE chat_users
                    SET display_name = ?,
                        avatar_url = COALESCE(?, avatar_url),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (display_name, avatar_url, int(row[0])),
                )
            return int(row[0])

    row = connection.execute(
        "SELECT id FROM chat_users WHERE display_name = ? AND source_user_id IS NULL",
        (display_name,),
    ).fetchone()
    if row:
        return int(row[0])

    cursor = connection.execute(
        "INSERT INTO chat_users (display_name, source_user_id, avatar_url) VALUES (?, ?, ?)",
        (display_name, source_user_id, avatar_url),
    )
    return int(cursor.lastrowid)


def ensure_group_member(
    connection: sqlite3.Connection,
    group_id: int,
    user_id: int,
    message: dict[str, Any],
) -> None:
    display_name = message.get("sender_name") or "unknown_user"
    sent_at = message.get("sent_at")
    connection.execute(
        """
        INSERT INTO group_members (
            group_id,
            user_id,
            display_name_in_group,
            first_seen_at,
            last_seen_at
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(group_id, user_id) DO UPDATE SET
            display_name_in_group = CASE
                WHEN group_members.last_seen_at IS NULL THEN excluded.display_name_in_group
                WHEN excluded.last_seen_at IS NULL THEN group_members.display_name_in_group
                WHEN excluded.last_seen_at >= group_members.last_seen_at
                    THEN excluded.display_name_in_group
                ELSE group_members.display_name_in_group
            END,
            first_seen_at = CASE
                WHEN group_members.first_seen_at IS NULL THEN excluded.first_seen_at
                WHEN excluded.first_seen_at IS NULL THEN group_members.first_seen_at
                WHEN excluded.first_seen_at < group_members.first_seen_at THEN excluded.first_seen_at
                ELSE group_members.first_seen_at
            END,
            last_seen_at = CASE
                WHEN group_members.last_seen_at IS NULL THEN excluded.last_seen_at
                WHEN excluded.last_seen_at IS NULL THEN group_members.last_seen_at
                WHEN excluded.last_seen_at > group_members.last_seen_at THEN excluded.last_seen_at
                ELSE group_members.last_seen_at
            END
        """,
        (group_id, user_id, display_name, sent_at, sent_at),
    )


def create_import_batch(
    connection: sqlite3.Connection,
    account_id: int,
    group_id: int,
    source_path: Path,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO import_batches (
            account_id,
            group_id,
            source_type,
            source_file,
            status
        )
        VALUES (?, ?, ?, ?, 'running')
        """,
        (account_id, group_id, source_path.suffix.lower().lstrip("."), str(source_path)),
    )
    return int(cursor.lastrowid)


def create_collection_job(
    connection: sqlite3.Connection,
    account_id: int,
    group_id: int,
    messages: list[dict[str, Any]],
) -> int:
    sent_times = [message.get("sent_at") for message in messages if message.get("sent_at")]
    range_start = min(sent_times) if sent_times else ""
    range_end = max(sent_times) if sent_times else ""
    cursor = connection.execute(
        """
        INSERT INTO collection_jobs (
            account_id,
            group_id,
            range_start,
            range_end,
            status,
            started_at,
            collector_type
        )
        VALUES (?, ?, ?, ?, 'running', CURRENT_TIMESTAMP, 'manual_import')
        """,
        (account_id, group_id, range_start, range_end),
    )
    return int(cursor.lastrowid)


def finish_import_batch(
    connection: sqlite3.Connection,
    import_batch_id: int,
    inserted_count: int,
    skipped_count: int,
) -> None:
    connection.execute(
        """
        UPDATE import_batches
        SET imported_count = ?, skipped_count = ?, status = 'completed'
        WHERE id = ?
        """,
        (inserted_count, skipped_count, import_batch_id),
    )


def finish_collection_job(
    connection: sqlite3.Connection,
    collection_job_id: int,
    summary: ImportSummary,
) -> None:
    connection.execute(
        """
        UPDATE collection_jobs
        SET
            status = 'completed',
            finished_at = CURRENT_TIMESTAMP,
            total_seen_count = ?,
            inserted_count = ?,
            skipped_count = ?,
            failed_count = 0,
            filtered_red_packet_count = ?,
            filtered_system_notice_count = ?
        WHERE id = ?
        """,
        (
            summary.total_count,
            summary.inserted_count,
            summary.skipped_count,
            summary.red_packet_count,
            summary.filtered_system_notice_count,
            collection_job_id,
        ),
    )


def is_red_packet(message: dict[str, Any]) -> bool:
    """Compatibility wrapper around the shared high-confidence classifier."""

    return classify_import_filter(message) == "red_packet"


def classify_import_filter(message: Mapping[str, Any]) -> MessageFilterKind | None:
    """Classify normalized imports together with any embedded raw payload."""

    raw_payload = message.get("raw_payload")
    if isinstance(raw_payload, str):
        try:
            decoded = json.loads(raw_payload)
        except (json.JSONDecodeError, TypeError):
            decoded = None
        raw_payload = decoded if isinstance(decoded, Mapping) else None

    combined = dict(raw_payload) if isinstance(raw_payload, Mapping) else {}
    combined.update(message)
    return classify_message_filter(combined)


def normalize_text(value: str) -> str:
    return " ".join(str(value).split())


def build_content_hash(message: dict[str, Any]) -> str:
    attachments = message.get("attachments", [])
    hash_input = json.dumps(
        {
            "message_type": message.get("message_type") or "text",
            "content_text": message.get("content_text") or "",
            "attachments": attachments,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(hash_input.encode("utf-8")).hexdigest()


def is_duplicate_message(
    connection: sqlite3.Connection,
    account_id: int,
    group_id: int,
    user_id: int,
    message: dict[str, Any],
    content_hash: str,
) -> bool:
    source_message_id = message.get("source_message_id")
    if source_message_id:
        row = connection.execute(
            """
            SELECT id
            FROM messages
            WHERE account_id = ?
              AND group_id = ?
              AND source_message_id = ?
            LIMIT 1
            """,
            (account_id, group_id, source_message_id),
        ).fetchone()
        return row is not None

    row = connection.execute(
        """
        SELECT id
        FROM messages
        WHERE account_id = ?
          AND group_id = ?
          AND user_id = ?
          AND sent_at = ?
          AND content_hash = ?
        LIMIT 1
        """,
        (
            account_id,
            group_id,
            user_id,
            message.get("sent_at"),
            content_hash,
        ),
    ).fetchone()
    return row is not None


def insert_message(
    connection: sqlite3.Connection,
    account_id: int,
    group_id: int,
    user_id: int,
    collection_job_id: int,
    message: dict[str, Any],
    content_hash: str,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO messages (
            account_id,
            group_id,
            user_id,
            source_message_id,
            sent_at,
            message_type,
            content_text,
            normalized_text,
            raw_payload,
            content_hash,
            collection_job_id,
            is_red_packet,
            is_system_message
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
        """,
        (
            account_id,
            group_id,
            user_id,
            message.get("source_message_id"),
            message.get("sent_at"),
            message.get("message_type") or "text",
            message.get("content_text") or "",
            normalize_text(message.get("content_text") or ""),
            json.dumps(message.get("raw_payload", message), ensure_ascii=False),
            content_hash,
            collection_job_id,
            1
            if message.get("is_system_message") or message.get("message_type") == "system"
            else 0,
        ),
    )
    return int(cursor.lastrowid)


def insert_attachments(
    connection: sqlite3.Connection,
    message_id: int,
    attachments: list[dict[str, Any]],
    attachments_dir: Path | None,
    copy_local_attachments: bool,
) -> int:
    inserted = 0
    for attachment in attachments:
        normalized = prepare_attachment(
            attachment,
            attachments_dir,
            copy_local_attachments,
        )
        connection.execute(
            """
            INSERT INTO attachments (
                message_id,
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
                downloaded_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_id,
                normalized["attachment_type"],
                normalized.get("source_url"),
                normalized.get("local_path"),
                normalized.get("file_name"),
                normalized.get("mime_type"),
                normalized.get("file_size"),
                normalized.get("content_hash"),
                normalized.get("title"),
                normalized.get("description"),
                normalized.get("download_status", "pending"),
                normalized.get("downloaded_at"),
            ),
        )
        inserted += 1
    return inserted


def prepare_attachment(
    attachment: dict[str, Any],
    attachments_dir: Path | None,
    copy_local_attachments: bool,
) -> dict[str, Any]:
    normalized = {
        "attachment_type": attachment.get("attachment_type")
        or attachment.get("type")
        or "unknown",
        "source_url": attachment.get("source_url") or attachment.get("url"),
        "local_path": attachment.get("local_path"),
        "file_name": attachment.get("file_name") or attachment.get("name"),
        "mime_type": attachment.get("mime_type"),
        "file_size": attachment.get("file_size"),
        "content_hash": attachment.get("content_hash"),
        "title": attachment.get("title"),
        "description": attachment.get("description"),
        "download_status": attachment.get("download_status") or "pending",
        "downloaded_at": attachment.get("downloaded_at"),
    }

    local_path = normalized.get("local_path")
    if copy_local_attachments and attachments_dir and local_path:
        source = Path(local_path)
        if source.exists() and source.is_file():
            content_hash = hash_file(source)
            file_name = normalized.get("file_name") or source.name
            target = attachments_dir / f"{content_hash[:16]}_{file_name}"
            if not target.exists():
                shutil.copy2(source, target)
            normalized["local_path"] = str(target)
            normalized["file_name"] = file_name
            normalized["file_size"] = source.stat().st_size
            normalized["content_hash"] = content_hash
            normalized["download_status"] = "stored"
            normalized["downloaded_at"] = datetime.now(timezone.utc).isoformat()
        else:
            normalized["download_status"] = "missing_local_file"

    return normalized


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
