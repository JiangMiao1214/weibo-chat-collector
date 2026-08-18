from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from fastapi import HTTPException


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.api.collection_jobs import (  # noqa: E402
    confirm_collection_job,
    resume_collection_job,
    stop_collection_job,
)
from app.collectors.weibo_api import (  # noqa: E402
    CookieProfileStore,
    WeiboAuthenticationError,
    WeiboResponseError,
    WeiboTimeoutError,
)
from app.database import apply_database_migrations, connection_context  # noqa: E402
from app.services.collection_runner import create_queued_api_job  # noqa: E402
from app.services.collection_runner import CollectionLeaseLostError  # noqa: E402
from app.services.collection_worker import (  # noqa: E402
    CollectionWorker,
    parse_utc_text,
    utc_now_text,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")


def raw_message(message_id: str, hour: int, content: str, **extra) -> dict:
    return {
        "id": message_id,
        "time": int(datetime(2026, 1, 2, hour, tzinfo=SHANGHAI).timestamp()),
        "content": content,
        "from_uid": f"user-{message_id}",
        "from_user": {"screen_name": f"用户-{message_id}"},
        **extra,
    }


class FakePageClient:
    def __init__(self, responses: dict[str, list[dict | Exception]]) -> None:
        self.responses = responses
        self.calls: list[str] = []
        self.on_request = None

    def query_messages(self, account_id, source_group_id, *, max_mid="0", count=20):
        cursor = str(max_mid)
        self.calls.append(cursor)
        if self.on_request is not None:
            self.on_request(cursor)
        response = self.responses[cursor].pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class CollectionWorkerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.db_path = root / "collector.sqlite3"
        self.auth_dir = root / "auth"
        self.settings = SimpleNamespace(
            database_path=self.db_path,
            weibo_api_auth_dir=self.auth_dir,
            weibo_api_timezone="Asia/Shanghai",
            weibo_api_page_size=20,
            weibo_api_max_pages=50,
            weibo_api_request_timeout_seconds=1.0,
            weibo_api_long_rest_every_pages=20,
            weibo_api_page_delay_min_seconds=0.0,
            weibo_api_page_delay_max_seconds=0.0,
            weibo_api_long_rest_min_seconds=0.0,
            weibo_api_long_rest_max_seconds=0.0,
        )
        with sqlite3.connect(self.db_path) as connection:
            apply_database_migrations(connection)

        self.connection = sqlite3.connect(self.db_path, timeout=5)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute(
            "INSERT INTO weibo_accounts (display_name) VALUES ('测试账号')"
        )
        self.account_id = int(self.connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        self.connection.execute(
            """
            INSERT INTO chat_groups (account_id, name, source_group_id)
            VALUES (?, '测试群', '4761715839862414')
            """,
            (self.account_id,),
        )
        self.group_id = int(self.connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        self.connection.commit()
        CookieProfileStore(self.auth_dir).save(
            self.account_id,
            [{"name": "SUB", "value": "test-cookie", "domain": ".weibo.com"}],
        )
        self.database_settings_patch = patch(
            "app.database.get_settings", return_value=self.settings
        )
        self.api_settings_patch = patch(
            "app.api.collection_jobs.get_settings", return_value=self.settings
        )
        self.database_settings_patch.start()
        self.api_settings_patch.start()

    def tearDown(self) -> None:
        self.api_settings_patch.stop()
        self.database_settings_patch.stop()
        self.connection.close()
        self.temp_dir.cleanup()

    def create_confirmed_job(self, start_hour: int = 8, end_hour: int = 11) -> int:
        job_id = create_queued_api_job(
            self.connection,
            account_id=self.account_id,
            group_id=self.group_id,
            source_group_id="4761715839862414",
            range_start=datetime(2026, 1, 2, start_hour, tzinfo=SHANGHAI),
            range_end=datetime(2026, 1, 2, end_hour, tzinfo=SHANGHAI),
            timezone_name="Asia/Shanghai",
            page_size=20,
        )
        self.connection.execute(
            "UPDATE collection_jobs SET status = 'awaiting_confirmation' WHERE id = ?",
            (job_id,),
        )
        self.connection.commit()
        confirm_collection_job(job_id, self.connection)
        self.connection.commit()
        return job_id

    def worker_for(self, client: FakePageClient, *, random_uniform=None) -> CollectionWorker:
        return CollectionWorker(
            settings_provider=lambda: self.settings,
            client_factory=lambda store, settings: client,
            random_uniform=random_uniform or (lambda minimum, maximum: minimum),
        )

    def job(self, job_id: int) -> sqlite3.Row:
        return self.connection.execute(
            "SELECT * FROM collection_jobs WHERE id = ?", (job_id,)
        ).fetchone()

    def test_page_transaction_filters_system_noise_but_keeps_greeting(self) -> None:
        job_id = self.create_confirmed_job()
        client = FakePageClient(
            {
                "0": [
                    {
                        "messages": [
                            raw_message("1000", 10, "早上好"),
                            raw_message("950", 9, "收到红包消息，请在手机上查看", is_redenvelope=1),
                            raw_message(
                                "900",
                                9,
                                "恭喜某人今日获得“早鸟”标识",
                                template="恭喜{{nick.DATA}}今日获得“{{title.DATA}}”标识",
                            ),
                            raw_message("700", 7, "范围外消息"),
                        ]
                    }
                ]
            }
        )

        self.assertTrue(self.worker_for(client).run_once())
        self.connection.commit()
        job = self.job(job_id)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["next_max_mid"], "700")
        self.assertEqual(int(job["page_count"]), 1)
        self.assertEqual(int(job["inserted_count"]), 1)
        self.assertEqual(int(job["filtered_red_packet_count"]), 1)
        self.assertEqual(int(job["filtered_system_notice_count"]), 1)
        self.assertEqual(
            [tuple(row) for row in self.connection.execute("SELECT content_text FROM messages")],
            [("早上好",)],
        )
        page = self.connection.execute(
            "SELECT * FROM collection_job_pages WHERE job_id = ?", (job_id,)
        ).fetchone()
        self.assertEqual((page["raw_count"], page["outside_range_count"]), (4, 1))

    def test_concurrent_confirm_and_stop_never_lose_the_stop_request(self) -> None:
        job_id = create_queued_api_job(
            self.connection,
            account_id=self.account_id,
            group_id=self.group_id,
            source_group_id="4761715839862414",
            range_start=datetime(2026, 1, 2, 8, tzinfo=SHANGHAI),
            range_end=datetime(2026, 1, 2, 11, tzinfo=SHANGHAI),
            timezone_name="Asia/Shanghai",
            page_size=20,
        )
        self.connection.execute(
            "UPDATE collection_jobs SET status = 'awaiting_confirmation' WHERE id = ?",
            (job_id,),
        )
        self.connection.commit()

        barrier = threading.Barrier(2)
        errors: list[HTTPException] = []

        def invoke(action) -> None:
            connection = sqlite3.connect(self.db_path, timeout=5)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            try:
                barrier.wait()
                action(job_id, connection)
                connection.commit()
            except HTTPException as error:
                errors.append(error)
                connection.rollback()
            finally:
                connection.close()

        threads = [
            threading.Thread(target=invoke, args=(confirm_collection_job,)),
            threading.Thread(target=invoke, args=(stop_collection_job,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.connection.commit()
        job = self.job(job_id)
        self.assertTrue(
            job["status"] == "stopped"
            or (job["status"] == "running" and job["stop_requested_at"] is not None)
        )
        if errors:
            self.assertEqual([error.status_code for error in errors], [409])

    def test_failed_page_is_not_retried_and_same_job_resumes_from_checkpoint(self) -> None:
        job_id = self.create_confirmed_job(start_hour=5)
        client = FakePageClient(
            {
                "0": [
                    {
                        "messages": [
                            raw_message("1000", 10, "第一页一"),
                            raw_message("900", 9, "第一页二"),
                        ]
                    }
                ],
                "900": [
                    WeiboTimeoutError("single request timeout"),
                    {
                        "messages": [
                            raw_message("800", 8, "续传消息"),
                            raw_message("400", 4, "范围外终点"),
                        ]
                    },
                ],
            }
        )
        worker = self.worker_for(client)

        self.assertTrue(worker.run_once())
        self.connection.commit()
        stopped = self.job(job_id)
        self.assertEqual(stopped["status"], "stopped")
        self.assertEqual(stopped["stop_code"], "timeout")
        self.assertEqual(stopped["next_max_mid"], "900")
        self.assertEqual(client.calls, ["0", "900"])
        self.assertEqual(
            [
                tuple(row)
                for row in self.connection.execute(
                    "SELECT request_max_mid FROM collection_job_pages WHERE job_id = ?",
                    (job_id,),
                )
            ],
            [("0",)],
        )

        resume_collection_job(job_id, self.connection)
        self.connection.commit()
        self.assertFalse(worker.run_once())  # queued -> awaiting confirmation
        self.connection.commit()
        confirm_collection_job(job_id, self.connection)
        self.connection.commit()
        self.assertTrue(worker.run_once())
        self.connection.commit()

        completed = self.job(job_id)
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["next_max_mid"], "400")
        self.assertEqual(int(completed["attempt_count"]), 2)
        self.assertEqual(client.calls, ["0", "900", "900"])
        self.assertEqual(
            [
                tuple(row)
                for row in self.connection.execute(
                    "SELECT status, start_max_mid FROM collection_job_attempts ORDER BY attempt_no"
                )
            ],
            [("stopped", "0"), ("completed", "900")],
        )

    def test_safe_stop_commits_current_page_then_stops(self) -> None:
        job_id = self.create_confirmed_job(start_hour=5)
        client = FakePageClient(
            {
                "0": [
                    {
                        "messages": [
                            raw_message("1000", 10, "提交后停止"),
                            raw_message("900", 9, "断点消息"),
                        ]
                    }
                ]
            }
        )

        def request_stop(cursor: str) -> None:
            with connection_context() as connection:
                connection.execute(
                    "UPDATE collection_jobs SET stop_requested_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (job_id,),
                )

        client.on_request = request_stop
        self.assertTrue(self.worker_for(client).run_once())
        self.connection.commit()
        job = self.job(job_id)
        self.assertEqual(job["status"], "stopped")
        self.assertEqual(job["stop_code"], "manual_stop")
        self.assertEqual(job["next_max_mid"], "900")
        self.assertEqual(int(job["inserted_count"]), 2)
        self.assertEqual(client.calls, ["0"])

    def test_risk_business_error_stops_once_without_advancing_and_enforces_cooldown(self) -> None:
        job_id = self.create_confirmed_job(start_hour=5)
        client = FakePageClient(
            {
                "0": [
                    WeiboResponseError(
                        "Weibo API business error 10023",
                        error_code=10023,
                        http_status=403,
                    )
                ]
            }
        )

        self.assertTrue(self.worker_for(client).run_once())
        self.connection.commit()
        job = self.job(job_id)
        self.assertEqual(job["status"], "stopped")
        self.assertEqual(job["stop_code"], "api_10023")
        self.assertEqual(job["next_max_mid"], "0")
        self.assertEqual(int(job["page_count"]), 0)
        self.assertIsNotNone(job["resume_not_before"])
        self.assertEqual(job["last_http_status"], 403)
        self.assertEqual(job["last_error_code"], "10023")
        self.assertEqual(client.calls, ["0"])
        with self.assertRaises(HTTPException) as cooldown:
            resume_collection_job(job_id, self.connection)
        self.assertEqual(cooldown.exception.status_code, 409)

    def test_http_429_cools_down_even_when_body_has_another_business_code(self) -> None:
        job_id = self.create_confirmed_job(start_hour=5)
        client = FakePageClient(
            {
                "0": [
                    WeiboResponseError(
                        "Weibo API business error 99999",
                        error_code=99999,
                        http_status=429,
                    )
                ]
            }
        )

        self.assertTrue(self.worker_for(client).run_once())
        self.connection.commit()
        job = self.job(job_id)
        self.assertEqual(job["status"], "stopped")
        self.assertEqual(job["stop_code"], "http_429")
        self.assertEqual(job["last_http_status"], 429)
        self.assertIsNotNone(job["resume_not_before"])
        self.assertEqual(client.calls, ["0"])

        code, _reason, status, error_code = CollectionWorker._classify_error(
            WeiboAuthenticationError("authentication body on a limited response", http_status=429)
        )
        self.assertEqual((code, status, error_code), ("http_429", 429, "21301"))

    def test_global_twentieth_request_uses_long_delay_instead_of_short_delay(self) -> None:
        job_id = self.create_confirmed_job(start_hour=5)
        self.settings.weibo_api_page_delay_min_seconds = 3.0
        self.settings.weibo_api_page_delay_max_seconds = 8.0
        self.settings.weibo_api_long_rest_min_seconds = 30.0
        self.settings.weibo_api_long_rest_max_seconds = 60.0
        self.connection.execute(
            "UPDATE collector_runtime_state SET global_request_count = 19, next_allowed_request_at = NULL"
        )
        self.connection.commit()
        worker = self.worker_for(FakePageClient({}), random_uniform=lambda minimum, maximum: minimum)
        self.assertIsNotNone(worker._claim_running_job())

        first_delay = worker._wait_and_reserve_request(job_id)
        self.assertEqual(first_delay, 30.0)
        runtime = self.connection.execute("SELECT * FROM collector_runtime_state").fetchone()
        conservative_delay = (
            parse_utc_text(runtime["next_allowed_request_at"])
            - parse_utc_text(runtime["last_request_at"])
        ).total_seconds()
        self.assertEqual(conservative_delay, 30.0)
        self.assertEqual(int(runtime["global_request_count"]), 20)
        worker._schedule_next_request(job_id, first_delay)
        runtime = self.connection.execute("SELECT * FROM collector_runtime_state").fetchone()
        first_remaining = (
            parse_utc_text(runtime["next_allowed_request_at"])
            - datetime.now(timezone.utc)
        ).total_seconds()
        self.assertGreater(first_remaining, 29.0)
        self.assertLessEqual(first_remaining, 30.0)

        self.connection.execute(
            "UPDATE collector_runtime_state SET next_allowed_request_at = NULL"
        )
        self.connection.commit()
        second_delay = worker._wait_and_reserve_request(job_id)
        self.assertEqual(second_delay, 3.0)
        runtime = self.connection.execute("SELECT * FROM collector_runtime_state").fetchone()
        conservative_delay = (
            parse_utc_text(runtime["next_allowed_request_at"])
            - parse_utc_text(runtime["last_request_at"])
        ).total_seconds()
        self.assertEqual(conservative_delay, 3.0)
        self.assertEqual(int(runtime["global_request_count"]), 21)

    def test_expired_foreign_lease_stops_instead_of_auto_resuming(self) -> None:
        job_id = self.create_confirmed_job(start_hour=5)
        self.connection.execute(
            """
            UPDATE collector_runtime_state
            SET worker_id = 'dead-worker', current_job_id = ?, lease_expires_at = ?
            WHERE id = 1
            """,
            (job_id, utc_now_text(datetime.now(timezone.utc) - timedelta(seconds=1))),
        )
        self.connection.commit()

        worker = self.worker_for(FakePageClient({}))
        self.assertIsNone(worker._claim_running_job())
        self.connection.commit()
        job = self.job(job_id)
        self.assertEqual(job["status"], "stopped")
        self.assertEqual(job["stop_code"], "process_interrupted")
        attempt = self.connection.execute(
            "SELECT status FROM collection_job_attempts WHERE job_id = ?", (job_id,)
        ).fetchone()
        self.assertEqual(attempt["status"], "stopped")

    def test_stale_worker_cannot_reclaim_live_foreign_lease_or_stop_job(self) -> None:
        job_id = self.create_confirmed_job(start_hour=5)
        worker = self.worker_for(FakePageClient({}))
        self.assertIsNotNone(worker._claim_running_job())
        self.connection.execute(
            """
            UPDATE collector_runtime_state
            SET worker_id = 'new-owner', current_job_id = ?, lease_expires_at = ?
            WHERE id = 1
            """,
            (job_id, utc_now_text(datetime.now(timezone.utc) + timedelta(minutes=5))),
        )
        self.connection.commit()

        with self.assertRaises(CollectionLeaseLostError):
            worker._wait_and_reserve_request(job_id)
        with self.assertRaises(CollectionLeaseLostError):
            worker._stop_job(job_id, "timeout", "stale", count_failure=True)
        runtime = self.connection.execute(
            "SELECT worker_id FROM collector_runtime_state WHERE id = 1"
        ).fetchone()
        self.assertEqual(runtime["worker_id"], "new-owner")
        self.assertEqual(self.job(job_id)["status"], "running")

    def test_self_expired_lease_is_fenced_and_requires_manual_resume(self) -> None:
        job_id = self.create_confirmed_job(start_hour=5)
        client = FakePageClient(
            {"0": [{"messages": [raw_message("900", 9, "不能由失租 worker 提交")]}]}
        )
        worker = self.worker_for(client)

        def expire_lease(cursor: str) -> None:
            with connection_context() as connection:
                connection.execute(
                    """
                    UPDATE collector_runtime_state
                    SET lease_expires_at = ?
                    WHERE id = 1 AND worker_id = ? AND current_job_id = ?
                    """,
                    (
                        utc_now_text(datetime.now(timezone.utc) - timedelta(seconds=1)),
                        worker.worker_id,
                        job_id,
                    ),
                )

        client.on_request = expire_lease
        self.assertTrue(worker.run_once())
        self.connection.commit()
        job = self.job(job_id)
        self.assertEqual(job["status"], "stopped")
        self.assertEqual(job["stop_code"], "process_interrupted")
        self.assertEqual(int(job["page_count"]), 0)
        self.assertEqual(job["next_max_mid"], "0")

    def test_circular_next_cursor_does_not_commit_failed_page(self) -> None:
        job_id = self.create_confirmed_job(start_hour=5)
        client = FakePageClient(
            {
                "0": [
                    {
                        "messages": [
                            raw_message("1000", 10, "第一页"),
                            raw_message("900", 9, "第一页断点"),
                        ]
                    }
                ],
                "900": [
                    {"messages": [raw_message("0", 8, "形成 0→900→0 游标环")]},
                    {"messages": []},
                ],
            }
        )
        worker = self.worker_for(client)
        self.assertTrue(worker.run_once())
        self.connection.commit()
        stopped = self.job(job_id)
        self.assertEqual(stopped["status"], "stopped")
        self.assertEqual(stopped["stop_code"], "pagination_incomplete")
        self.assertEqual(stopped["next_max_mid"], "900")
        self.assertEqual(int(stopped["page_count"]), 1)
        pages = self.connection.execute(
            "SELECT request_max_mid FROM collection_job_pages WHERE job_id = ?", (job_id,)
        ).fetchall()
        self.assertEqual([row["request_max_mid"] for row in pages], ["0"])

        resume_collection_job(job_id, self.connection)
        self.connection.commit()
        self.assertFalse(worker.run_once())
        self.connection.commit()
        confirm_collection_job(job_id, self.connection)
        self.connection.commit()
        self.assertTrue(worker.run_once())
        self.connection.commit()
        self.assertEqual(self.job(job_id)["status"], "completed")
        self.assertEqual(client.calls, ["0", "900", "900"])

    def test_state_transitions_reject_double_confirm_and_completed_resume(self) -> None:
        job_id = self.create_confirmed_job()
        with self.assertRaises(HTTPException) as duplicate_confirm:
            confirm_collection_job(job_id, self.connection)
        self.assertEqual(duplicate_confirm.exception.status_code, 409)

        self.connection.execute(
            "UPDATE collection_jobs SET status = 'completed' WHERE id = ?", (job_id,)
        )
        self.connection.commit()
        with self.assertRaises(HTTPException) as completed_resume:
            resume_collection_job(job_id, self.connection)
        self.assertEqual(completed_resume.exception.status_code, 409)

    def test_queue_promotes_only_one_task_and_waits_for_confirmation(self) -> None:
        first_job = create_queued_api_job(
            self.connection,
            account_id=self.account_id,
            group_id=self.group_id,
            source_group_id="4761715839862414",
            range_start=datetime(2026, 1, 2, 8, tzinfo=SHANGHAI),
            range_end=datetime(2026, 1, 2, 11, tzinfo=SHANGHAI),
            timezone_name="Asia/Shanghai",
            page_size=20,
        )
        second_job = create_queued_api_job(
            self.connection,
            account_id=self.account_id,
            group_id=self.group_id,
            source_group_id="4761715839862414",
            range_start=datetime(2026, 1, 1, 8, tzinfo=SHANGHAI),
            range_end=datetime(2026, 1, 1, 11, tzinfo=SHANGHAI),
            timezone_name="Asia/Shanghai",
            page_size=20,
        )
        self.connection.commit()
        worker = self.worker_for(FakePageClient({}))

        self.assertFalse(worker.run_once())
        self.connection.commit()
        statuses = {
            int(row["id"]): row["status"]
            for row in self.connection.execute(
                "SELECT id, status FROM collection_jobs WHERE id IN (?, ?)",
                (first_job, second_job),
            )
        }
        self.assertEqual(statuses[first_job], "awaiting_confirmation")
        self.assertEqual(statuses[second_job], "queued")


if __name__ == "__main__":
    unittest.main()
