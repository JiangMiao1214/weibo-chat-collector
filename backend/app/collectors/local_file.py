import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..importer import (
    build_content_hash,
    classify_import_filter,
    create_import_batch,
    ensure_group_member,
    ensure_user,
    insert_attachments,
    insert_message,
    is_duplicate_message,
    read_source_file,
)


@dataclass
class SingleGroupFileCollectionSummary:
    account: str
    group: str
    source_file: str
    collection_job_id: int
    import_batch_id: int
    source_total_count: int = 0
    total_count: int = 0
    inserted_count: int = 0
    skipped_count: int = 0
    red_packet_count: int = 0
    filtered_system_notice_count: int = 0
    duplicate_count: int = 0
    attachment_count: int = 0
    out_of_range_count: int = 0
    invalid_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_datetime(value: str, field_name: str) -> datetime:
    normalized = value.replace("T", " ")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as error:
        raise ValueError(f"{field_name} must be an ISO-like datetime string.") from error


def normalize_datetime(value: str, field_name: str) -> str:
    return parse_datetime(value, field_name).strftime("%Y-%m-%d %H:%M:%S")


def create_running_collection_job(
    connection: sqlite3.Connection,
    account_id: int,
    group_id: int,
    range_start: str,
    range_end: str,
) -> int:
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
        VALUES (?, ?, ?, ?, 'running', CURRENT_TIMESTAMP, 'single_group_local_file')
        """,
        (account_id, group_id, range_start, range_end),
    )
    return int(cursor.lastrowid)


def finish_collection_job(
    connection: sqlite3.Connection,
    collection_job_id: int,
    summary: SingleGroupFileCollectionSummary,
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
            failed_count = ?,
            filtered_red_packet_count = ?,
            filtered_system_notice_count = ?,
            error_message = NULL
        WHERE id = ?
        """,
        (
            summary.total_count,
            summary.inserted_count,
            summary.skipped_count,
            summary.invalid_count,
            summary.red_packet_count,
            summary.filtered_system_notice_count,
            collection_job_id,
        ),
    )


def fail_collection_job(
    connection: sqlite3.Connection,
    collection_job_id: int,
    error_message: str,
) -> None:
    connection.execute(
        """
        UPDATE collection_jobs
        SET
            status = 'failed',
            finished_at = CURRENT_TIMESTAMP,
            error_message = ?
        WHERE id = ?
        """,
        (error_message[:1000], collection_job_id),
    )


def finish_import_batch(
    connection: sqlite3.Connection,
    import_batch_id: int,
    summary: SingleGroupFileCollectionSummary,
) -> None:
    connection.execute(
        """
        UPDATE import_batches
        SET imported_count = ?, skipped_count = ?, status = 'completed'
        WHERE id = ?
        """,
        (summary.inserted_count, summary.skipped_count, import_batch_id),
    )


def collect_single_group_from_file(
    connection: sqlite3.Connection,
    *,
    account_id: int,
    account_name: str,
    group_id: int,
    group_name: str,
    source_path: Path,
    range_start: str,
    range_end: str,
    attachments_dir: Path,
    copy_local_attachments: bool = True,
) -> SingleGroupFileCollectionSummary:
    payload = read_source_file(source_path)
    messages = payload["messages"]
    normalized_start = normalize_datetime(range_start, "range_start")
    normalized_end = normalize_datetime(range_end, "range_end")
    start_dt = parse_datetime(normalized_start, "range_start")
    end_dt = parse_datetime(normalized_end, "range_end")
    if start_dt >= end_dt:
        raise ValueError("range_start must be before range_end.")

    attachments_dir.mkdir(parents=True, exist_ok=True)
    collection_job_id = create_running_collection_job(
        connection,
        account_id,
        group_id,
        normalized_start,
        normalized_end,
    )
    import_batch_id = create_import_batch(connection, account_id, group_id, source_path)
    summary = SingleGroupFileCollectionSummary(
        account=account_name,
        group=group_name,
        source_file=str(source_path),
        collection_job_id=collection_job_id,
        import_batch_id=import_batch_id,
        source_total_count=len(messages),
    )

    try:
        for raw_message in messages:
            message = dict(raw_message)
            sent_at = message.get("sent_at")
            if not sent_at:
                summary.invalid_count += 1
                summary.skipped_count += 1
                continue

            try:
                sent_dt = parse_datetime(str(sent_at), "message.sent_at")
            except ValueError:
                summary.invalid_count += 1
                summary.skipped_count += 1
                continue

            if sent_dt < start_dt or sent_dt > end_dt:
                summary.out_of_range_count += 1
                continue

            message["sent_at"] = sent_dt.strftime("%Y-%m-%d %H:%M:%S")
            summary.total_count += 1

            filter_kind = classify_import_filter(message)
            if filter_kind == "red_packet":
                summary.red_packet_count += 1
                summary.skipped_count += 1
                continue
            if filter_kind == "fansgroup_badge":
                summary.filtered_system_notice_count += 1
                summary.skipped_count += 1
                continue

            user_id = ensure_user(connection, message)
            ensure_group_member(connection, group_id, user_id, message)

            content_hash = build_content_hash(message)
            if is_duplicate_message(
                connection,
                account_id,
                group_id,
                user_id,
                message,
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
                message,
                content_hash,
            )
            summary.attachment_count += insert_attachments(
                connection,
                message_id,
                message.get("attachments", []),
                attachments_dir,
                copy_local_attachments,
            )
            summary.inserted_count += 1

        finish_import_batch(connection, import_batch_id, summary)
        finish_collection_job(connection, collection_job_id, summary)
    except Exception as error:
        fail_collection_job(connection, collection_job_id, str(error))
        raise

    return summary
