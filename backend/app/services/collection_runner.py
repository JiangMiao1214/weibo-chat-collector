from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import sqlite3
from typing import Any

from ..collectors.base import CollectionCandidate
from ..collectors.weibo_api import WeiboCollectionPage
from ..importer import (
    build_content_hash,
    ensure_group_member,
    ensure_user,
    insert_attachments,
    insert_message,
    is_duplicate_message,
    is_red_packet,
)
from .message_filter import classify_message_filter


class CollectionLeaseLostError(RuntimeError):
    """The worker no longer owns the persisted global collection lease."""


@dataclass(slots=True)
class ApiCollectionSummary:
    collection_job_id: int
    source_total_count: int = 0
    total_count: int = 0
    inserted_count: int = 0
    skipped_count: int = 0
    red_packet_count: int = 0
    filtered_system_notice_count: int = 0
    duplicate_count: int = 0
    attachment_count: int = 0
    invalid_count: int = 0
    page_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PageIngestResult:
    raw_count: int = 0
    in_range_count: int = 0
    inserted_count: int = 0
    skipped_count: int = 0
    duplicate_count: int = 0
    filtered_red_packet_count: int = 0
    filtered_system_notice_count: int = 0
    outside_range_count: int = 0
    attachment_count: int = 0


def create_queued_api_job(
    connection: sqlite3.Connection,
    *,
    account_id: int,
    group_id: int,
    source_group_id: str,
    range_start: datetime,
    range_end: datetime,
    timezone_name: str,
    page_size: int,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO collection_jobs (
            account_id,
            group_id,
            source_group_id,
            range_start,
            range_end,
            status,
            collector_type,
            timezone_name,
            page_size,
            next_max_mid,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, 'queued', 'weibo_api_v2', ?, ?, '0', CURRENT_TIMESTAMP)
        """,
        (
            account_id,
            group_id,
            source_group_id,
            range_start.strftime("%Y-%m-%d %H:%M:%S"),
            range_end.strftime("%Y-%m-%d %H:%M:%S"),
            timezone_name,
            page_size,
        ),
    )
    return int(cursor.lastrowid)


def create_running_api_job(
    connection: sqlite3.Connection,
    *,
    account_id: int,
    group_id: int,
    range_start: datetime,
    range_end: datetime,
) -> int:
    """Compatibility helper for legacy imports and integration tests."""

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
        VALUES (?, ?, ?, ?, 'running', CURRENT_TIMESTAMP, 'weibo_api_v1')
        """,
        (
            account_id,
            group_id,
            range_start.strftime("%Y-%m-%d %H:%M:%S"),
            range_end.strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    return int(cursor.lastrowid)


def mark_api_job_failed(
    connection: sqlite3.Connection,
    collection_job_id: int,
    error_message: str,
) -> None:
    """Compatibility state update for synchronous legacy callers."""

    connection.execute(
        """
        UPDATE collection_jobs
        SET status = 'failed',
            finished_at = CURRENT_TIMESTAMP,
            failed_count = CASE WHEN failed_count < 1 THEN 1 ELSE failed_count END,
            error_message = ?,
            stop_code = 'legacy_failure',
            stop_reason = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (error_message[:1000], error_message[:1000], collection_job_id),
    )


def ingest_api_page(
    connection: sqlite3.Connection,
    *,
    collection_job_id: int,
    attempt_id: int,
    account_id: int,
    group_id: int,
    range_start: datetime,
    range_end: datetime,
    page: WeiboCollectionPage,
    job_page_no: int,
    attempt_page_no: int,
    fetched_at: str,
    complete_job: bool = False,
    worker_id: str | None = None,
) -> PageIngestResult:
    """Atomically persist one page, its counters, and its continuation cursor."""

    if worker_id is not None:
        runtime = connection.execute(
            """
            SELECT worker_id, current_job_id, lease_expires_at
            FROM collector_runtime_state
            WHERE id = 1
            """
        ).fetchone()
        lease_expires_at = (
            datetime.fromisoformat(str(runtime["lease_expires_at"]))
            if runtime is not None and runtime["lease_expires_at"]
            else None
        )
        if (
            runtime is None
            or runtime["worker_id"] != worker_id
            or int(runtime["current_job_id"] or 0) != collection_job_id
            or lease_expires_at is None
            or lease_expires_at
            <= datetime.now(timezone.utc).replace(tzinfo=None)
        ):
            raise CollectionLeaseLostError("collection worker lease was lost")

    result = PageIngestResult(raw_count=page.raw_count)
    for candidate in sorted(page.candidates, key=lambda item: item.sent_at):
        if candidate.sent_at < range_start or candidate.sent_at > range_end:
            result.outside_range_count += 1
            continue

        result.in_range_count += 1
        message = candidate_to_message(candidate, account_id=account_id, group_id=group_id)
        raw_message = candidate.raw_payload if isinstance(candidate.raw_payload, Mapping) else {}
        filter_kind = classify_message_filter(raw_message)
        if filter_kind == "red_packet" or candidate.is_red_packet or is_red_packet(message):
            result.filtered_red_packet_count += 1
            result.skipped_count += 1
            continue
        if filter_kind == "fansgroup_badge":
            result.filtered_system_notice_count += 1
            result.skipped_count += 1
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
            result.duplicate_count += 1
            result.skipped_count += 1
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
        result.attachment_count += insert_attachments(
            connection,
            message_id,
            message["attachments"],
            attachments_dir=None,
            copy_local_attachments=False,
        )
        result.inserted_count += 1

    newest = page.newest_sent_at.strftime("%Y-%m-%d %H:%M:%S") if page.newest_sent_at else None
    oldest = page.oldest_sent_at.strftime("%Y-%m-%d %H:%M:%S") if page.oldest_sent_at else None
    connection.execute(
        """
        INSERT INTO collection_job_pages (
            job_id, attempt_id, job_page_no, attempt_page_no,
            request_max_mid, next_max_mid, newest_sent_at, oldest_sent_at,
            raw_count, in_range_count, inserted_count, skipped_count,
            duplicate_count, filtered_red_packet_count,
            filtered_system_notice_count, outside_range_count,
            fetched_at, committed_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (
            collection_job_id,
            attempt_id,
            job_page_no,
            attempt_page_no,
            page.request_max_mid,
            page.next_max_mid,
            newest,
            oldest,
            result.raw_count,
            result.in_range_count,
            result.inserted_count,
            result.skipped_count,
            result.duplicate_count,
            result.filtered_red_packet_count,
            result.filtered_system_notice_count,
            result.outside_range_count,
            fetched_at,
        ),
    )
    connection.execute(
        """
        UPDATE collection_jobs
        SET next_max_mid = ?,
            checkpoint_oldest_at = COALESCE(?, checkpoint_oldest_at),
            page_count = page_count + 1,
            total_seen_count = total_seen_count + ?,
            inserted_count = inserted_count + ?,
            skipped_count = skipped_count + ?,
            duplicate_count = duplicate_count + ?,
            filtered_red_packet_count = filtered_red_packet_count + ?,
            filtered_system_notice_count = filtered_system_notice_count + ?,
            last_progress_at = CURRENT_TIMESTAMP,
            heartbeat_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND status = 'running'
        """,
        (
            page.next_max_mid,
            oldest,
            result.raw_count,
            result.inserted_count,
            result.skipped_count,
            result.duplicate_count,
            result.filtered_red_packet_count,
            result.filtered_system_notice_count,
            collection_job_id,
        ),
    )
    if connection.execute("SELECT changes()").fetchone()[0] != 1:
        raise RuntimeError("collection job stopped before its page could be committed")
    connection.execute(
        """
        UPDATE collection_job_attempts
        SET end_max_mid = ?,
            page_count = page_count + 1,
            total_seen_count = total_seen_count + ?,
            inserted_count = inserted_count + ?,
            skipped_count = skipped_count + ?,
            duplicate_count = duplicate_count + ?,
            filtered_red_packet_count = filtered_red_packet_count + ?,
            filtered_system_notice_count = filtered_system_notice_count + ?
        WHERE id = ? AND status = 'running'
        """,
        (
            page.next_max_mid,
            result.raw_count,
            result.inserted_count,
            result.skipped_count,
            result.duplicate_count,
            result.filtered_red_packet_count,
            result.filtered_system_notice_count,
            attempt_id,
        ),
    )
    if connection.execute("SELECT changes()").fetchone()[0] != 1:
        raise RuntimeError("collection attempt stopped before its page could be committed")
    if complete_job:
        connection.execute(
            """
            UPDATE collection_jobs
            SET status = 'completed', finished_at = CURRENT_TIMESTAMP,
                stop_code = NULL, stop_reason = NULL, error_message = NULL,
                stop_requested_at = NULL, heartbeat_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'running'
            """,
            (collection_job_id,),
        )
        if connection.execute("SELECT changes()").fetchone()[0] != 1:
            raise RuntimeError("collection job could not be completed with its final page")
        connection.execute(
            """
            UPDATE collection_job_attempts
            SET status = 'completed', finished_at = CURRENT_TIMESTAMP,
                stop_code = NULL, stop_reason = NULL
            WHERE id = ? AND status = 'running'
            """,
            (attempt_id,),
        )
        if connection.execute("SELECT changes()").fetchone()[0] != 1:
            raise RuntimeError("collection attempt could not be completed with its final page")
    return result


def ingest_api_candidates(
    connection: sqlite3.Connection,
    *,
    collection_job_id: int,
    account_id: int,
    group_id: int,
    candidates: Iterable[CollectionCandidate],
    source_total_count: int | None = None,
    page_count: int = 0,
) -> ApiCollectionSummary:
    """Legacy all-at-once ingestion retained for existing file/test consumers."""

    materialized = sorted(candidates, key=lambda candidate: candidate.sent_at)
    summary = ApiCollectionSummary(
        collection_job_id=collection_job_id,
        source_total_count=source_total_count if source_total_count is not None else len(materialized),
        total_count=len(materialized),
        page_count=page_count,
    )
    for candidate in materialized:
        message = candidate_to_message(candidate, account_id=account_id, group_id=group_id)
        raw_message = candidate.raw_payload if isinstance(candidate.raw_payload, Mapping) else {}
        filter_kind = classify_message_filter(raw_message)
        if filter_kind == "red_packet" or candidate.is_red_packet or is_red_packet(message):
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
        if is_duplicate_message(connection, account_id, group_id, user_id, message, content_hash):
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
            message["attachments"],
            attachments_dir=None,
            copy_local_attachments=False,
        )
        summary.inserted_count += 1

    connection.execute(
        """
        UPDATE collection_jobs
        SET status = 'completed', finished_at = CURRENT_TIMESTAMP,
            total_seen_count = ?, inserted_count = ?, skipped_count = ?, failed_count = ?,
            page_count = ?, duplicate_count = ?, filtered_red_packet_count = ?,
            filtered_system_notice_count = ?, error_message = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            summary.total_count,
            summary.inserted_count,
            summary.skipped_count,
            summary.invalid_count,
            summary.page_count,
            summary.duplicate_count,
            summary.red_packet_count,
            summary.filtered_system_notice_count,
            collection_job_id,
        ),
    )
    return summary


def candidate_to_message(
    candidate: CollectionCandidate,
    *,
    account_id: int,
    group_id: int,
) -> dict[str, Any]:
    return {
        "account_id": account_id,
        "group_id": group_id,
        "source_message_id": candidate.source_message_id,
        "source_user_id": candidate.source_user_id,
        "sender_name": candidate.sender_name,
        "avatar_url": getattr(candidate, "sender_avatar_url", None),
        "sent_at": candidate.sent_at.strftime("%Y-%m-%d %H:%M:%S"),
        "message_type": candidate.message_type,
        "content_text": candidate.content_text or "",
        "raw_payload": candidate.raw_payload,
        "is_red_packet": candidate.is_red_packet,
        "is_system_message": candidate.is_system_message,
        "attachments": [asdict(attachment) for attachment in candidate.attachments],
    }
