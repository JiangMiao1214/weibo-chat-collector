import sqlite3
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..collectors.weibo_api import CookieProfileError, CookieProfileStore
from ..database import get_connection
from ..services.collection_worker import get_collection_worker, parse_utc_text
from ..settings import get_settings


router = APIRouter(prefix="/api", tags=["collection-jobs"])

VALID_STATUSES = {
    "pending",
    "queued",
    "awaiting_confirmation",
    "running",
    "stopped",
    "completed",
    "failed",
    "cancelled",
}


class CreateCollectionJobRequest(BaseModel):
    account_id: int
    group_id: int
    range_start: str
    range_end: str
    collector_type: str = "time_range_placeholder"


class UpdateCollectionJobStatusRequest(BaseModel):
    status: str
    error_message: str | None = None


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def parse_datetime(value: str, field_name: str) -> datetime:
    normalized = value.replace("T", " ")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} must be an ISO-like datetime string.",
        ) from error


def normalize_datetime(value: str, field_name: str) -> str:
    return parse_datetime(value, field_name).strftime("%Y-%m-%d %H:%M:%S")


def ensure_account_and_group(
    connection: sqlite3.Connection,
    account_id: int,
    group_id: int,
) -> tuple[sqlite3.Row, sqlite3.Row]:
    account = connection.execute(
        "SELECT id, display_name FROM weibo_accounts WHERE id = ?",
        (account_id,),
    ).fetchone()
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")

    group = connection.execute(
        """
        SELECT id, account_id, name
        FROM chat_groups
        WHERE id = ?
        """,
        (group_id,),
    ).fetchone()
    if group is None:
        raise HTTPException(status_code=404, detail="Group not found")
    if int(group["account_id"]) != account_id:
        raise HTTPException(
            status_code=400,
            detail="The selected group does not belong to the selected account.",
        )

    return account, group


@router.get("/collection-jobs")
def list_collection_jobs(
    account_id: int | None = None,
    group_id: int | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict[str, Any]:
    filters = []
    params: list[Any] = []

    if account_id is not None:
        filters.append("cj.account_id = ?")
        params.append(account_id)
    if group_id is not None:
        filters.append("cj.group_id = ?")
        params.append(group_id)
    if status:
        filters.append("cj.status = ?")
        params.append(status)

    where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""
    total = int(
        connection.execute(
            f"""
            SELECT COUNT(*)
            FROM collection_jobs cj
            JOIN weibo_accounts wa ON wa.id = cj.account_id
            JOIN chat_groups cg ON cg.id = cj.group_id
            {where_sql}
            """,
            params,
        ).fetchone()[0]
    )

    rows = connection.execute(
        f"""
        SELECT
            cj.id,
            cj.account_id,
            wa.display_name AS account_name,
            cj.group_id,
            cg.name AS group_name,
            cj.range_start,
            cj.range_end,
            cj.status,
            cj.started_at,
            cj.finished_at,
            cj.total_seen_count,
            cj.inserted_count,
            cj.skipped_count,
            cj.failed_count,
            cj.error_message,
            cj.collector_type,
            cj.next_max_mid,
            cj.checkpoint_oldest_at,
            cj.page_count,
            cj.attempt_count,
            cj.duplicate_count,
            cj.filtered_red_packet_count,
            cj.filtered_system_notice_count,
            cj.stop_code,
            cj.stop_reason,
            cj.last_http_status,
            cj.last_error_code,
            cj.stop_requested_at,
            cj.last_progress_at,
            cj.resume_not_before,
            CASE WHEN cj.status = 'queued' THEN (
                SELECT COUNT(*)
                FROM collection_jobs queued
                WHERE queued.collector_type = 'weibo_api_v2'
                  AND queued.status = 'queued'
                  AND (
                      queued.created_at < cj.created_at
                      OR (queued.created_at = cj.created_at AND queued.id <= cj.id)
                  )
            ) ELSE NULL END AS queue_position,
            cj.created_at
        FROM collection_jobs cj
        JOIN weibo_accounts wa ON wa.id = cj.account_id
        JOIN chat_groups cg ON cg.id = cj.group_id
        {where_sql}
        ORDER BY cj.created_at DESC, cj.id DESC
        LIMIT ? OFFSET ?
        """,
        [*params, limit, offset],
    ).fetchall()

    return {
        "items": [row_to_dict(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/collection-jobs")
def create_collection_job(
    request: CreateCollectionJobRequest,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict[str, Any]:
    ensure_account_and_group(connection, request.account_id, request.group_id)
    range_start = normalize_datetime(request.range_start, "range_start")
    range_end = normalize_datetime(request.range_end, "range_end")
    if parse_datetime(range_start, "range_start") >= parse_datetime(range_end, "range_end"):
        raise HTTPException(status_code=400, detail="range_start must be before range_end.")

    cursor = connection.execute(
        """
        INSERT INTO collection_jobs (
            account_id,
            group_id,
            range_start,
            range_end,
            status,
            collector_type
        )
        VALUES (?, ?, ?, ?, 'pending', ?)
        """,
        (
            request.account_id,
            request.group_id,
            range_start,
            range_end,
            request.collector_type,
        ),
    )
    job_id = int(cursor.lastrowid)
    return get_collection_job(job_id, connection)


@router.get("/collection-jobs/{job_id}")
def get_collection_job(
    job_id: int,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT
            cj.*,
            wa.display_name AS account_name,
            cg.name AS group_name
        FROM collection_jobs cj
        JOIN weibo_accounts wa ON wa.id = cj.account_id
        JOIN chat_groups cg ON cg.id = cj.group_id
        WHERE cj.id = ?
        """,
        (job_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Collection job not found")
    return row_to_dict(row)


@router.get("/collection-jobs/{job_id}/attempts")
def list_collection_job_attempts(
    job_id: int,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict[str, Any]:
    get_collection_job(job_id, connection)
    rows = connection.execute(
        """
        SELECT * FROM collection_job_attempts
        WHERE job_id = ?
        ORDER BY attempt_no DESC
        """,
        (job_id,),
    ).fetchall()
    return {"items": [row_to_dict(row) for row in rows]}


@router.get("/collection-jobs/{job_id}/pages")
def list_collection_job_pages(
    job_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict[str, Any]:
    get_collection_job(job_id, connection)
    total = int(
        connection.execute(
            "SELECT COUNT(*) FROM collection_job_pages WHERE job_id = ?",
            (job_id,),
        ).fetchone()[0]
    )
    rows = connection.execute(
        """
        SELECT * FROM collection_job_pages
        WHERE job_id = ?
        ORDER BY job_page_no DESC
        LIMIT ? OFFSET ?
        """,
        (job_id, limit, offset),
    ).fetchall()
    return {
        "items": [row_to_dict(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/collection-jobs/{job_id}/confirm")
def confirm_collection_job(
    job_id: int,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict[str, Any]:
    job = get_collection_job(job_id, connection)
    if job["collector_type"] != "weibo_api_v2" or job["status"] != "awaiting_confirmation":
        raise HTTPException(
            status_code=409,
            detail="Only the API task currently awaiting confirmation can be started.",
        )
    try:
        CookieProfileStore(get_settings().weibo_api_auth_dir).cookie_header(int(job["account_id"]))
    except CookieProfileError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    connection.execute("BEGIN IMMEDIATE")
    other_running = connection.execute(
        """
        SELECT id FROM collection_jobs
        WHERE collector_type = 'weibo_api_v2' AND status = 'running' AND id <> ?
        LIMIT 1
        """,
        (job_id,),
    ).fetchone()
    if other_running is not None:
        raise HTTPException(status_code=409, detail="Another API collection is already running.")
    updated = connection.execute(
        """
        UPDATE collection_jobs
        SET status = 'running', started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
            finished_at = NULL, confirmed_at = CURRENT_TIMESTAMP,
            attempt_count = attempt_count + 1, stop_code = NULL,
            stop_reason = NULL, error_message = NULL, stop_requested_at = NULL,
            resume_not_before = NULL, heartbeat_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND status = 'awaiting_confirmation'
        """,
        (job_id,),
    ).rowcount
    if updated != 1:
        raise HTTPException(status_code=409, detail="Collection task state changed.")
    current = connection.execute(
        "SELECT attempt_count, next_max_mid FROM collection_jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    connection.execute(
        """
        INSERT INTO collection_job_attempts (
            job_id, attempt_no, status, start_max_mid, end_max_mid, started_at
        )
        VALUES (?, ?, 'running', ?, ?, CURRENT_TIMESTAMP)
        """,
        (
            job_id,
            int(current["attempt_count"]),
            str(current["next_max_mid"] or "0"),
            str(current["next_max_mid"] or "0"),
        ),
    )
    result = get_collection_job(job_id, connection)
    get_collection_worker().notify()
    return result


@router.post("/collection-jobs/{job_id}/resume")
def resume_collection_job(
    job_id: int,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict[str, Any]:
    job = get_collection_job(job_id, connection)
    if job["collector_type"] != "weibo_api_v2" or job["status"] != "stopped":
        raise HTTPException(status_code=409, detail="Only a stopped API task can be resumed.")
    resume_not_before = parse_utc_text(job.get("resume_not_before"))
    if resume_not_before and resume_not_before > datetime.now(timezone.utc):
        raise HTTPException(
            status_code=409,
            detail=f"Risk-control cooldown is active until {resume_not_before.isoformat()}.",
        )
    updated = connection.execute(
        """
        UPDATE collection_jobs
        SET status = 'queued', finished_at = NULL, stop_requested_at = NULL,
            resume_not_before = NULL, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND status = 'stopped'
        """,
        (job_id,),
    ).rowcount
    if updated != 1:
        raise HTTPException(status_code=409, detail="Collection task state changed.")
    result = get_collection_job(job_id, connection)
    get_collection_worker().notify()
    return result


@router.post("/collection-jobs/{job_id}/stop")
def stop_collection_job(
    job_id: int,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict[str, Any]:
    connection.execute("BEGIN IMMEDIATE")
    job = connection.execute(
        "SELECT id, collector_type, status FROM collection_jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    if job is None:
        raise HTTPException(status_code=404, detail="Collection job not found")
    if job["collector_type"] != "weibo_api_v2":
        raise HTTPException(status_code=409, detail="Safe stop is only available for API tasks.")
    if job["status"] == "running":
        connection.execute(
            """
            UPDATE collection_jobs
            SET stop_requested_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'running'
            """,
            (job_id,),
        )
    elif job["status"] in {"queued", "awaiting_confirmation"}:
        connection.execute(
            """
            UPDATE collection_jobs
            SET status = 'stopped', finished_at = CURRENT_TIMESTAMP,
                stop_code = 'manual_stop',
                stop_reason = 'Stopped before the next API request.',
                error_message = 'Stopped before the next API request.',
                stop_requested_at = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status IN ('queued', 'awaiting_confirmation')
            """,
            (job_id,),
        )
    else:
        raise HTTPException(status_code=409, detail="This task cannot be stopped in its current state.")
    result = get_collection_job(job_id, connection)
    get_collection_worker().notify()
    return result


@router.patch("/collection-jobs/{job_id}/status")
def update_collection_job_status(
    job_id: int,
    request: UpdateCollectionJobStatusRequest,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict[str, Any]:
    if request.status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid collection job status")

    current = connection.execute(
        "SELECT id, collector_type FROM collection_jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    if current is None:
        raise HTTPException(status_code=404, detail="Collection job not found")
    if current["collector_type"] == "weibo_api_v2":
        raise HTTPException(
            status_code=409,
            detail="Use confirm, resume, or stop actions for API collection tasks.",
        )

    started_at_sql = "CURRENT_TIMESTAMP" if request.status == "running" else "started_at"
    finished_at_sql = (
        "CURRENT_TIMESTAMP"
        if request.status in {"completed", "failed", "cancelled"}
        else "finished_at"
    )
    connection.execute(
        f"""
        UPDATE collection_jobs
        SET status = ?,
            error_message = ?,
            started_at = {started_at_sql},
            finished_at = {finished_at_sql}
        WHERE id = ?
        """,
        (request.status, request.error_message, job_id),
    )
    return get_collection_job(job_id, connection)
