from __future__ import annotations

from datetime import datetime, timedelta, timezone
import random
import sqlite3
import threading
from typing import Any, Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

from ..collectors.weibo_api import (
    CookieProfileError,
    CookieProfileStore,
    WeiboApiClient,
    WeiboApiCollector,
    WeiboApiError,
    WeiboAuthenticationError,
    WeiboGroupError,
    WeiboHttpError,
    WeiboIncompleteCollectionError,
    WeiboRateLimitError,
    WeiboResponseError,
    WeiboTimeoutError,
    WeiboTransportError,
)
from ..database import connection_context
from ..settings import Settings, get_settings
from .collection_runner import CollectionLeaseLostError, ingest_api_page


UTC_FORMAT = "%Y-%m-%d %H:%M:%S.%f"
RISK_COOLDOWN_CODES = {"http_429", "api_10023", "api_10024"}


def utc_now_text(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    return value.astimezone(timezone.utc).replace(tzinfo=None).strftime(UTC_FORMAT)


def parse_utc_text(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class CollectionWorker:
    """One persisted, globally serial worker for all Weibo API collection jobs."""

    def __init__(
        self,
        *,
        settings_provider: Callable[[], Settings] = get_settings,
        client_factory: Callable[[CookieProfileStore, Settings], WeiboApiClient] | None = None,
        random_uniform: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self.worker_id = uuid4().hex
        self.settings_provider = settings_provider
        self.client_factory = client_factory or (
            lambda store, settings: WeiboApiClient(
                store,
                timeout=settings.weibo_api_request_timeout_seconds,
            )
        )
        self.random_uniform = random_uniform
        self._condition = threading.Condition()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        # A fresh token on every actual thread start prevents pre-fork module
        # state, or a restarted thread, from sharing an old lease identity.
        self.worker_id = uuid4().hex
        self._stop_event.clear()
        self._recover_interrupted_job()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="weibo-api-collection-worker",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self.notify()
        thread = self._thread
        if thread and thread.is_alive():
            timeout = self.settings_provider().weibo_api_request_timeout_seconds + 5
            thread.join(timeout=timeout)

    def notify(self) -> None:
        with self._condition:
            self._condition.notify_all()

    def run_once(self) -> bool:
        """Advance queue state or process one confirmed job; useful for tests and the loop."""

        self._promote_next_job()
        job = self._claim_running_job()
        if job is None:
            return False
        self._process_job(job)
        return True

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                worked = self.run_once()
            except Exception:
                # Do not loop after an orchestration/database failure: the job
                # might still look runnable and another pass could repeat an API
                # request whose stop-state update failed.
                self._stop_event.set()
                break
            if not worked:
                with self._condition:
                    self._condition.wait(timeout=0.75)

    def _recover_interrupted_job(self) -> None:
        now = datetime.now(timezone.utc)
        with connection_context() as connection:
            connection.execute("BEGIN IMMEDIATE")
            runtime = connection.execute(
                "SELECT worker_id, lease_expires_at, current_job_id FROM collector_runtime_state WHERE id = 1"
            ).fetchone()
            lease_expires = parse_utc_text(runtime["lease_expires_at"]) if runtime else None
            if runtime and runtime["worker_id"] and lease_expires and lease_expires > now:
                return

            running_jobs = connection.execute(
                "SELECT id FROM collection_jobs WHERE collector_type = 'weibo_api_v2' AND status = 'running'"
            ).fetchall()
            for row in running_jobs:
                self._stop_job_in_transaction(
                    connection,
                    int(row["id"]),
                    stop_code="process_interrupted",
                    stop_reason="Collection process stopped before the task completed.",
                    count_failure=True,
                )
            connection.execute(
                """
                UPDATE collector_runtime_state
                SET worker_id = NULL, lease_expires_at = NULL, current_job_id = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = 1
                """
            )

    def _promote_next_job(self) -> None:
        with connection_context() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                """
                SELECT 1 FROM collection_jobs
                WHERE collector_type = 'weibo_api_v2'
                  AND status IN ('awaiting_confirmation', 'running')
                LIMIT 1
                """
            ).fetchone()
            if active is not None:
                return
            queued = connection.execute(
                """
                SELECT id FROM collection_jobs
                WHERE collector_type = 'weibo_api_v2' AND status = 'queued'
                ORDER BY created_at, id
                LIMIT 1
                """
            ).fetchone()
            if queued is not None:
                connection.execute(
                    """
                    UPDATE collection_jobs
                    SET status = 'awaiting_confirmation', updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND status = 'queued'
                    """,
                    (int(queued["id"]),),
                )

    def _claim_running_job(self) -> dict[str, Any] | None:
        now = datetime.now(timezone.utc)
        lease_text = utc_now_text(now + timedelta(seconds=self._lease_seconds()))
        with connection_context() as connection:
            connection.execute("BEGIN IMMEDIATE")
            runtime = connection.execute(
                """
                SELECT worker_id, lease_expires_at, current_job_id
                FROM collector_runtime_state WHERE id = 1
                """
            ).fetchone()
            lease_expires = parse_utc_text(runtime["lease_expires_at"]) if runtime else None
            if runtime and runtime["worker_id"]:
                if lease_expires and lease_expires > now:
                    return None

                # An expired owner represents an interrupted attempt.  Fence
                # it and require the user to resume; never silently take over
                # a job that was already making network requests.
                interrupted_job_id = int(runtime["current_job_id"] or 0)
                if interrupted_job_id:
                    self._stop_job_in_transaction(
                        connection,
                        interrupted_job_id,
                        stop_code="process_interrupted",
                        stop_reason="Collection worker lease expired before the task completed.",
                        count_failure=True,
                    )
                connection.execute(
                    """
                    UPDATE collector_runtime_state
                    SET worker_id = NULL, lease_expires_at = NULL,
                        current_job_id = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE id = 1
                    """
                )
                return None

            row = connection.execute(
                """
                SELECT * FROM collection_jobs
                WHERE collector_type = 'weibo_api_v2' AND status = 'running'
                ORDER BY confirmed_at, id
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE collector_runtime_state
                SET worker_id = ?, lease_expires_at = ?, current_job_id = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = 1
                """,
                (self.worker_id, lease_text, int(row["id"])),
            )
            return dict(row)

    def _lease_seconds(self) -> float:
        settings = self.settings_provider()
        return max(
            180.0,
            float(settings.weibo_api_request_timeout_seconds)
            + float(settings.weibo_api_long_rest_max_seconds)
            + 30.0,
        )

    def _process_job(self, initial_job: dict[str, Any]) -> None:
        job_id = int(initial_job["id"])
        attempt_id: int | None = None
        lease_was_lost = False
        try:
            settings = self.settings_provider()
            zone = ZoneInfo(str(initial_job["timezone_name"] or settings.weibo_api_timezone))
            range_start = datetime.fromisoformat(str(initial_job["range_start"])).replace(tzinfo=zone)
            range_end = datetime.fromisoformat(str(initial_job["range_end"])).replace(tzinfo=zone)
            previous_oldest = self._local_job_datetime(initial_job.get("checkpoint_oldest_at"), zone)
            attempt = self._running_attempt(job_id)
            if attempt is None:
                raise RuntimeError("running collection job has no running attempt")
            attempt_id = int(attempt["id"])
            store = CookieProfileStore(settings.weibo_api_auth_dir)
            # Validate before reserving a network request; query_messages reloads
            # the latest profile for every page so a later resume uses new cookies.
            store.cookie_header(int(initial_job["account_id"]))
            collector = WeiboApiCollector(
                self.client_factory(store, settings),
                str(initial_job["source_group_id"]),
                page_size=int(initial_job["page_size"]),
                max_pages=settings.weibo_api_max_pages,
                timezone_name=zone,
            )

            for _ in range(settings.weibo_api_max_pages):
                current = self._load_running_job(job_id)
                if current is None:
                    return
                if self._stop_event.is_set():
                    self._stop_job(
                        job_id,
                        "process_shutdown",
                        "Collection worker is shutting down.",
                        count_failure=False,
                    )
                    return
                if current["stop_requested_at"]:
                    self._stop_job(
                        job_id,
                        "manual_stop",
                        "Stopped safely at the last committed page.",
                        count_failure=False,
                    )
                    return

                request_cursor = str(current["next_max_mid"] or "0")
                if self._cursor_was_committed(job_id, request_cursor):
                    raise WeiboIncompleteCollectionError(
                        "Weibo pagination returned to an already committed cursor"
                    )
                request_delay = self._wait_and_reserve_request(job_id)
                if request_delay is None:
                    if self._stop_event.is_set():
                        self._stop_job(
                            job_id,
                            "process_shutdown",
                            "Collection worker is shutting down.",
                            count_failure=False,
                        )
                    elif self._stop_requested(job_id):
                        self._stop_job(
                            job_id,
                            "manual_stop",
                            "Stopped safely before the next API request.",
                            count_failure=False,
                        )
                    return

                try:
                    page = collector.fetch_page(
                        int(current["account_id"]),
                        int(current["group_id"]),
                        max_mid=request_cursor,
                        range_start=range_start,
                        previous_oldest_at=previous_oldest,
                    )
                    fetched_at = utc_now_text()
                    if (
                        not page.terminal
                        and not page.reached_range_start
                        and self._cursor_was_committed(job_id, page.next_max_mid)
                    ):
                        raise WeiboIncompleteCollectionError(
                            "Weibo pagination returned a previously committed next cursor"
                        )
                    attempt = self._running_attempt(job_id)
                    if attempt is None:
                        return
                    with connection_context() as connection:
                        connection.execute("BEGIN IMMEDIATE")
                        ingest_api_page(
                            connection,
                            collection_job_id=job_id,
                            attempt_id=int(attempt["id"]),
                            account_id=int(current["account_id"]),
                            group_id=int(current["group_id"]),
                            range_start=range_start,
                            range_end=range_end,
                            page=page,
                            job_page_no=int(current["page_count"]) + 1,
                            attempt_page_no=int(attempt["page_count"]) + 1,
                            fetched_at=fetched_at,
                            complete_job=page.terminal or page.reached_range_start,
                            worker_id=self.worker_id,
                        )
                finally:
                    # The delay starts only after the request and its page
                    # transaction finish, so request latency cannot consume
                    # the 3–8s pause or the 20th-request long rest.
                    self._schedule_next_request(job_id, request_delay)

                previous_oldest = page.oldest_sent_at or previous_oldest
                if page.terminal or page.reached_range_start:
                    return
                if self._stop_requested(job_id):
                    self._stop_job(
                        job_id,
                        "manual_stop",
                        "Stopped safely at the last committed page.",
                        count_failure=False,
                    )
                    return
                self._refresh_lease(job_id)

            self._stop_job(
                job_id,
                "page_limit",
                f"Stopped after the per-run page limit ({settings.weibo_api_max_pages}).",
                count_failure=False,
            )
        except CollectionLeaseLostError:
            # Another process fenced this worker.  It owns all subsequent
            # state transitions; this stale worker must not stop its job.
            lease_was_lost = True
            self._fence_abandoned_self_lease(job_id, attempt_id)
            return
        except Exception as error:
            code, reason, http_status, error_code = self._classify_error(error)
            self._stop_job(
                job_id,
                code,
                reason,
                count_failure=True,
                http_status=http_status,
                error_code=error_code,
            )
        finally:
            if not lease_was_lost:
                self._release_runtime(job_id)
            self._promote_next_job()

    def _fence_abandoned_self_lease(self, job_id: int, attempt_id: int | None) -> None:
        """Stop a running job only when this worker's own lease disappeared/expired."""

        now = datetime.now(timezone.utc)
        with connection_context() as connection:
            connection.execute("BEGIN IMMEDIATE")
            runtime = connection.execute(
                """
                SELECT worker_id, current_job_id, lease_expires_at
                FROM collector_runtime_state WHERE id = 1
                """
            ).fetchone()
            owner = runtime["worker_id"] if runtime else None
            current_job_id = int(runtime["current_job_id"] or 0) if runtime else 0
            lease_expires = parse_utc_text(runtime["lease_expires_at"]) if runtime else None

            owned_but_expired = (
                owner == self.worker_id
                and current_job_id == job_id
                and (lease_expires is None or lease_expires <= now)
            )
            running_attempt = connection.execute(
                """
                SELECT id FROM collection_job_attempts
                WHERE job_id = ? AND status = 'running'
                ORDER BY attempt_no DESC LIMIT 1
                """,
                (job_id,),
            ).fetchone()
            same_attempt = (
                attempt_id is not None
                and running_attempt is not None
                and int(running_attempt["id"]) == attempt_id
            )
            owned_but_expired = owned_but_expired and same_attempt
            owner_disappeared = owner is None and same_attempt
            if not owned_but_expired and not owner_disappeared:
                return

            self._stop_job_in_transaction(
                connection,
                job_id,
                stop_code="process_interrupted",
                stop_reason="Collection worker lost its lease before the task completed.",
                count_failure=True,
            )
            if owned_but_expired:
                connection.execute(
                    """
                    UPDATE collector_runtime_state
                    SET worker_id = NULL, lease_expires_at = NULL,
                        current_job_id = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE id = 1 AND worker_id = ? AND current_job_id = ?
                    """,
                    (self.worker_id, job_id),
                )

    def _wait_and_reserve_request(self, job_id: int) -> float | None:
        while not self._stop_event.is_set():
            if self._stop_requested(job_id):
                return None
            with connection_context() as connection:
                runtime = connection.execute(
                    """
                    SELECT worker_id, current_job_id, lease_expires_at,
                           next_allowed_request_at
                    FROM collector_runtime_state WHERE id = 1
                    """
                ).fetchone()
                self._require_runtime_owner(runtime, job_id)
            allowed_at = parse_utc_text(runtime["next_allowed_request_at"]) if runtime else None
            remaining = (allowed_at - datetime.now(timezone.utc)).total_seconds() if allowed_at else 0
            if remaining > 0:
                self._refresh_lease(job_id)
                self._stop_event.wait(min(remaining, 1.0))
                continue

            with connection_context() as connection:
                connection.execute("BEGIN IMMEDIATE")
                runtime = connection.execute(
                    """
                    SELECT worker_id, current_job_id, lease_expires_at,
                           global_request_count, next_allowed_request_at
                    FROM collector_runtime_state WHERE id = 1
                    """
                ).fetchone()
                self._require_runtime_owner(runtime, job_id)
                allowed_at = parse_utc_text(runtime["next_allowed_request_at"]) if runtime else None
                now = datetime.now(timezone.utc)
                if allowed_at and allowed_at > now:
                    continue
                request_count = int(runtime["global_request_count"] or 0) + 1
                settings = self.settings_provider()
                if request_count % settings.weibo_api_long_rest_every_pages == 0:
                    delay = self.random_uniform(
                        settings.weibo_api_long_rest_min_seconds,
                        settings.weibo_api_long_rest_max_seconds,
                    )
                else:
                    delay = self.random_uniform(
                        settings.weibo_api_page_delay_min_seconds,
                        settings.weibo_api_page_delay_max_seconds,
                    )
                connection.execute(
                    """
                    UPDATE collector_runtime_state
                    SET worker_id = ?, current_job_id = ?, global_request_count = ?,
                        last_request_at = ?, next_allowed_request_at = ?,
                        lease_expires_at = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = 1 AND worker_id = ? AND current_job_id = ?
                    """,
                    (
                        self.worker_id,
                        job_id,
                        request_count,
                        utc_now_text(now),
                        utc_now_text(now + timedelta(seconds=max(0.0, delay))),
                        utc_now_text(now + timedelta(seconds=self._lease_seconds())),
                        self.worker_id,
                        job_id,
                    ),
                )
                if connection.execute("SELECT changes()").fetchone()[0] != 1:
                    raise CollectionLeaseLostError("collection worker lease was lost")
                return max(0.0, float(delay))
        return None

    def _schedule_next_request(self, job_id: int, delay: float) -> None:
        now = datetime.now(timezone.utc)
        with connection_context() as connection:
            connection.execute("BEGIN IMMEDIATE")
            runtime = connection.execute(
                """
                SELECT worker_id, current_job_id, lease_expires_at
                FROM collector_runtime_state WHERE id = 1
                """
            ).fetchone()
            self._require_runtime_owner(runtime, job_id)
            connection.execute(
                """
                UPDATE collector_runtime_state
                SET next_allowed_request_at = ?, lease_expires_at = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = 1 AND worker_id = ? AND current_job_id = ?
                """,
                (
                    utc_now_text(now + timedelta(seconds=max(0.0, delay))),
                    utc_now_text(now + timedelta(seconds=self._lease_seconds())),
                    self.worker_id,
                    job_id,
                ),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise CollectionLeaseLostError("collection worker lease was lost")

    def _require_runtime_owner(
        self,
        runtime: sqlite3.Row | None,
        job_id: int,
    ) -> None:
        lease_expires = parse_utc_text(runtime["lease_expires_at"]) if runtime else None
        if (
            runtime is None
            or runtime["worker_id"] != self.worker_id
            or int(runtime["current_job_id"] or 0) != job_id
            or lease_expires is None
            or lease_expires <= datetime.now(timezone.utc)
        ):
            raise CollectionLeaseLostError("collection worker lease was lost")

    def _running_attempt(self, job_id: int) -> sqlite3.Row | None:
        with connection_context() as connection:
            return connection.execute(
                """
                SELECT * FROM collection_job_attempts
                WHERE job_id = ? AND status = 'running'
                ORDER BY attempt_no DESC LIMIT 1
                """,
                (job_id,),
            ).fetchone()

    def _load_running_job(self, job_id: int) -> sqlite3.Row | None:
        with connection_context() as connection:
            return connection.execute(
                "SELECT * FROM collection_jobs WHERE id = ? AND status = 'running'",
                (job_id,),
            ).fetchone()

    def _cursor_was_committed(self, job_id: int, cursor: str) -> bool:
        with connection_context() as connection:
            return connection.execute(
                "SELECT 1 FROM collection_job_pages WHERE job_id = ? AND request_max_mid = ?",
                (job_id, cursor),
            ).fetchone() is not None

    def _stop_requested(self, job_id: int) -> bool:
        with connection_context() as connection:
            row = connection.execute(
                "SELECT stop_requested_at FROM collection_jobs WHERE id = ? AND status = 'running'",
                (job_id,),
            ).fetchone()
            return row is None or bool(row["stop_requested_at"])

    def _refresh_lease(self, job_id: int) -> None:
        with connection_context() as connection:
            connection.execute("BEGIN IMMEDIATE")
            runtime = connection.execute(
                """
                SELECT worker_id, current_job_id, lease_expires_at
                FROM collector_runtime_state WHERE id = 1
                """
            ).fetchone()
            self._require_runtime_owner(runtime, job_id)
            connection.execute(
                """
                UPDATE collector_runtime_state
                SET lease_expires_at = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = 1 AND worker_id = ? AND current_job_id = ?
                """,
                (
                    utc_now_text(
                        datetime.now(timezone.utc) + timedelta(seconds=self._lease_seconds())
                    ),
                    self.worker_id,
                    job_id,
                ),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise CollectionLeaseLostError("collection worker lease was lost")

    def _stop_job(
        self,
        job_id: int,
        stop_code: str,
        stop_reason: str,
        *,
        count_failure: bool,
        http_status: int | None = None,
        error_code: str | None = None,
    ) -> None:
        with connection_context() as connection:
            connection.execute("BEGIN IMMEDIATE")
            runtime = connection.execute(
                """
                SELECT worker_id, current_job_id, lease_expires_at
                FROM collector_runtime_state WHERE id = 1
                """
            ).fetchone()
            self._require_runtime_owner(runtime, job_id)
            self._stop_job_in_transaction(
                connection,
                job_id,
                stop_code=stop_code,
                stop_reason=stop_reason,
                count_failure=count_failure,
                http_status=http_status,
                error_code=error_code,
            )

    def _stop_job_in_transaction(
        self,
        connection: sqlite3.Connection,
        job_id: int,
        *,
        stop_code: str,
        stop_reason: str,
        count_failure: bool,
        http_status: int | None = None,
        error_code: str | None = None,
    ) -> None:
        resume_not_before = None
        if stop_code in RISK_COOLDOWN_CODES or http_status == 429:
            resume_not_before = utc_now_text(
                datetime.now(timezone.utc) + timedelta(minutes=60)
            )
        connection.execute(
            """
            UPDATE collection_jobs
            SET status = 'stopped', finished_at = CURRENT_TIMESTAMP,
                failed_count = failed_count + ?, error_message = ?,
                stop_code = ?, stop_reason = ?, last_http_status = ?,
                last_error_code = ?, stop_requested_at = NULL,
                resume_not_before = ?, heartbeat_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'running'
            """,
            (
                1 if count_failure else 0,
                stop_reason[:1000],
                stop_code,
                stop_reason[:1000],
                http_status,
                error_code,
                resume_not_before,
                job_id,
            ),
        )
        connection.execute(
            """
            UPDATE collection_job_attempts
            SET status = 'stopped', finished_at = CURRENT_TIMESTAMP,
                failed_count = failed_count + ?, stop_code = ?, stop_reason = ?,
                last_http_status = ?, last_error_code = ?
            WHERE job_id = ? AND status = 'running'
            """,
            (
                1 if count_failure else 0,
                stop_code,
                stop_reason[:1000],
                http_status,
                error_code,
                job_id,
            ),
        )

    def _release_runtime(self, job_id: int) -> None:
        with connection_context() as connection:
            connection.execute(
                """
                UPDATE collector_runtime_state
                SET worker_id = NULL, lease_expires_at = NULL, current_job_id = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = 1 AND worker_id = ? AND current_job_id = ?
                """,
                (self.worker_id, job_id),
            )

    @staticmethod
    def _local_job_datetime(value: Any, zone: ZoneInfo) -> datetime | None:
        if not value:
            return None
        parsed = datetime.fromisoformat(str(value))
        return parsed.replace(tzinfo=zone) if parsed.tzinfo is None else parsed.astimezone(zone)

    @staticmethod
    def _classify_error(error: Exception) -> tuple[str, str, int | None, str | None]:
        if isinstance(error, CookieProfileError):
            return "cookie_invalid", str(error), None, None
        # HTTP rate limiting wins over the business-code subclass.  Some
        # gateways return a normal Weibo error body together with HTTP 429;
        # every HTTP 429 must still receive the global risk-control treatment.
        if getattr(error, "http_status", None) == 429:
            if isinstance(error, WeiboAuthenticationError):
                error_code = "21301"
            elif isinstance(error, WeiboGroupError):
                error_code = "21201"
            else:
                raw_error_code = getattr(error, "error_code", None)
                error_code = str(raw_error_code) if raw_error_code is not None else None
            return "http_429", str(error), 429, error_code
        if isinstance(error, WeiboAuthenticationError):
            return "api_21301", str(error), error.http_status, "21301"
        if isinstance(error, WeiboGroupError):
            return "api_21201", str(error), error.http_status, "21201"
        if isinstance(error, WeiboRateLimitError):
            return "http_429", str(error), 429, None
        if isinstance(error, WeiboHttpError):
            return f"http_{error.status}", str(error), error.status, None
        if isinstance(error, WeiboResponseError):
            code = str(error.error_code) if error.error_code is not None else None
            if error.http_status == 429:
                return "http_429", str(error), 429, code
            return (
                (f"api_{code}" if code else "invalid_response"),
                str(error),
                error.http_status,
                code,
            )
        if isinstance(error, WeiboIncompleteCollectionError):
            return "pagination_incomplete", str(error), None, None
        if isinstance(error, WeiboTimeoutError):
            return "timeout", str(error), None, None
        if isinstance(error, WeiboTransportError):
            return "transport", str(error), None, None
        if isinstance(error, sqlite3.Error):
            return "database_error", "Local database operation failed.", None, None
        if isinstance(error, WeiboApiError):
            return "weibo_api_error", str(error), None, None
        return "unexpected_error", "Collection stopped because of an unexpected local error.", None, None


_worker = CollectionWorker()


def get_collection_worker() -> CollectionWorker:
    return _worker
