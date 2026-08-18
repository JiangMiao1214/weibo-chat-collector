from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.api import weibo_api as weibo_api_module  # noqa: E402
from app.api.weibo_api import (  # noqa: E402
    SaveAccountCookiesRequest,
    UpsertApiTargetRequest,
    load_target,
    public_collection_error,
    save_account_cookies,
    upsert_api_target,
)
from app.collectors.base import AttachmentCandidate, CollectionCandidate  # noqa: E402
from app.collectors.weibo_api import CookieProfileStore, WeiboApiError  # noqa: E402
from app.importer import import_file  # noqa: E402
from app.services.collection_runner import (  # noqa: E402
    create_running_api_job,
    ingest_api_candidates,
    mark_api_job_failed,
)


RANGE_START = datetime(2026, 7, 1, 0, 0, 0)
RANGE_END = datetime(2026, 7, 2, 0, 0, 0)


class WeiboApiDatabaseIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "collector.sqlite3"
        self.connection = self._open_connection()
        self.addCleanup(self.connection.close)
        schema = (PROJECT_DIR / "scripts" / "schema.sql").read_text(encoding="utf-8")
        self.connection.executescript(schema)
        self.connection.commit()

    def _open_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _committed_connection(self):
        connection = self._open_connection()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _create_target(
        self,
        account_name: str = "采集账号",
        group_name: str = "测试群",
        source_group_id: str = "10001",
    ) -> tuple[int, int]:
        result = upsert_api_target(
            UpsertApiTargetRequest(
                account_name=account_name,
                group_name=group_name,
                source_group_id=source_group_id,
            ),
            self.connection,
        )
        return int(result["account"]["id"]), int(result["group"]["id"])

    @staticmethod
    def _candidate(
        account_id: int,
        group_id: int,
        *,
        source_message_id: str = "message-1",
        source_user_id: str = "user-1",
        content_text: str = "hello from api",
        attachments: list[AttachmentCandidate] | None = None,
        raw_payload: dict | None = None,
        is_red_packet: bool = False,
        sender_name: str = "API 用户",
        sender_avatar_url: str = "https://img.example/avatar.jpg",
        sent_at: datetime = datetime(2026, 7, 1, 12, 34, 56),
    ) -> CollectionCandidate:
        return CollectionCandidate(
            account_id=account_id,
            group_id=group_id,
            source_message_id=source_message_id,
            source_user_id=source_user_id,
            sender_name=sender_name,
            sender_avatar_url=sender_avatar_url,
            sent_at=sent_at,
            message_type="red_packet" if is_red_packet else "mixed",
            content_text=content_text,
            raw_payload=raw_payload or {"id": source_message_id, "opaque": {"keep": True}},
            is_red_packet=is_red_packet,
            attachments=attachments or [],
        )

    def test_same_named_groups_and_cookie_profiles_are_isolated_by_account(self) -> None:
        first_account_id, first_group_id = self._create_target(
            "账号甲", "同名群", "100001"
        )
        second_account_id, second_group_id = self._create_target(
            "账号乙", "同名群", "200002"
        )

        self.assertNotEqual(first_account_id, second_account_id)
        self.assertNotEqual(first_group_id, second_group_id)
        groups = self.connection.execute(
            "SELECT account_id, name, source_group_id FROM chat_groups ORDER BY account_id"
        ).fetchall()
        self.assertEqual(
            [tuple(row) for row in groups],
            [
                (first_account_id, "同名群", "100001"),
                (second_account_id, "同名群", "200002"),
            ],
        )

        auth_dir = Path(self.temp_dir.name) / "cookie-profiles"
        settings = SimpleNamespace(weibo_api_auth_dir=auth_dir)
        with patch.object(weibo_api_module, "get_settings", return_value=settings):
            first_status = save_account_cookies(
                first_account_id,
                SaveAccountCookiesRequest(
                    cookies=[
                        {
                            "name": "SUB",
                            "value": "secret-for-account-a",
                            "domain": ".weibo.com",
                        }
                    ]
                ),
                self.connection,
            )
            second_status = save_account_cookies(
                second_account_id,
                SaveAccountCookiesRequest(
                    cookies=[
                        {
                            "name": "SUB",
                            "value": "secret-for-account-b",
                            "domain": ".weibo.com",
                        }
                    ]
                ),
                self.connection,
            )

        self.assertTrue(first_status["authenticated"])
        self.assertTrue(second_status["authenticated"])
        store = CookieProfileStore(auth_dir)
        self.assertEqual(store.cookie_header(first_account_id), "SUB=secret-for-account-a")
        self.assertEqual(store.cookie_header(second_account_id), "SUB=secret-for-account-b")
        self.assertNotIn("account-b", store.cookie_header(first_account_id))
        self.assertEqual(
            sorted(path.name for path in auth_dir.glob("*.json")),
            [
                f"account-{first_account_id}.cookies.json",
                f"account-{second_account_id}.cookies.json",
            ],
        )
        profile_names = self.connection.execute(
            "SELECT id, login_profile_name FROM weibo_accounts ORDER BY id"
        ).fetchall()
        self.assertEqual(
            [tuple(row) for row in profile_names],
            [
                (first_account_id, f"account-{first_account_id}.cookies.json"),
                (second_account_id, f"account-{second_account_id}.cookies.json"),
            ],
        )

        updated = upsert_api_target(
            UpsertApiTargetRequest(
                account_name="账号甲",
                group_name="同名群",
                source_group_id="100003",
            ),
            self.connection,
        )
        self.assertEqual(int(updated["account"]["id"]), first_account_id)
        self.assertEqual(int(updated["group"]["id"]), first_group_id)
        self.assertEqual(updated["group"]["source_group_id"], "100003")

        with self.assertRaises(weibo_api_module.HTTPException) as duplicate_source:
            upsert_api_target(
                UpsertApiTargetRequest(
                    account_id=first_account_id,
                    account_name="账号甲",
                    group_name="另一个群",
                    source_group_id="100003",
                ),
                self.connection,
            )
        self.assertEqual(duplicate_source.exception.status_code, 409)

        account, group = load_target(self.connection, first_account_id, first_group_id)
        self.assertEqual(int(account["id"]), first_account_id)
        self.assertEqual(int(group["account_id"]), first_account_id)
        with self.assertRaises(weibo_api_module.HTTPException) as wrong_owner:
            load_target(self.connection, first_account_id, second_group_id)
        self.assertEqual(wrong_owner.exception.status_code, 400)

    def test_ingest_persists_avatar_attachments_raw_payload_and_job_counts(self) -> None:
        account_id, group_id = self._create_target()
        job_id = create_running_api_job(
            self.connection,
            account_id=account_id,
            group_id=group_id,
            range_start=RANGE_START,
            range_end=RANGE_END,
        )
        raw_payload = {
            "id": "api-message-9007199254740993",
            "future_field": {"nested": [1, "二", True]},
        }
        message = self._candidate(
            account_id,
            group_id,
            source_message_id="api-message-9007199254740993",
            raw_payload=raw_payload,
            attachments=[
                AttachmentCandidate(
                    attachment_type="image",
                    source_url="https://img.example/photo.jpg",
                    file_name="photo.jpg",
                    mime_type="image/jpeg",
                    file_size=1234,
                ),
                AttachmentCandidate(
                    attachment_type="link",
                    source_url="https://weibo.com/share/42",
                    title="链接标题",
                    description="链接摘要",
                ),
            ],
        )
        red_packet = self._candidate(
            account_id,
            group_id,
            source_message_id="red-packet-1",
            source_user_id="user-red",
            content_text="[微博红包]",
            is_red_packet=True,
        )

        summary = ingest_api_candidates(
            self.connection,
            collection_job_id=job_id,
            account_id=account_id,
            group_id=group_id,
            candidates=[red_packet, message],
            source_total_count=9,
            page_count=3,
        )

        self.assertEqual(summary.collection_job_id, job_id)
        self.assertEqual(summary.source_total_count, 9)
        self.assertEqual(summary.total_count, 2)
        self.assertEqual(summary.inserted_count, 1)
        self.assertEqual(summary.skipped_count, 1)
        self.assertEqual(summary.red_packet_count, 1)
        self.assertEqual(summary.attachment_count, 2)
        self.assertEqual(summary.page_count, 3)

        stored_message = self.connection.execute(
            """
            SELECT source_message_id, content_text, normalized_text, raw_payload,
                   collection_job_id, is_red_packet, is_deleted
            FROM messages
            """
        ).fetchone()
        self.assertIsNotNone(stored_message)
        self.assertEqual(stored_message["source_message_id"], "api-message-9007199254740993")
        self.assertEqual(stored_message["content_text"], "hello from api")
        self.assertEqual(stored_message["normalized_text"], "hello from api")
        self.assertEqual(json.loads(stored_message["raw_payload"]), raw_payload)
        self.assertEqual(int(stored_message["collection_job_id"]), job_id)
        self.assertEqual(int(stored_message["is_red_packet"]), 0)
        self.assertEqual(int(stored_message["is_deleted"]), 0)

        stored_user = self.connection.execute(
            "SELECT source_user_id, display_name, avatar_url FROM chat_users"
        ).fetchone()
        self.assertEqual(stored_user["source_user_id"], "user-1")
        self.assertEqual(stored_user["display_name"], "API 用户")
        self.assertEqual(stored_user["avatar_url"], "https://img.example/avatar.jpg")

        attachments = self.connection.execute(
            """
            SELECT attachment_type, source_url, file_name, mime_type, file_size,
                   title, description, download_status
            FROM attachments ORDER BY id
            """
        ).fetchall()
        self.assertEqual(len(attachments), 2)
        self.assertEqual(
            tuple(attachments[0]),
            (
                "image",
                "https://img.example/photo.jpg",
                "photo.jpg",
                "image/jpeg",
                1234,
                None,
                None,
                "pending",
            ),
        )
        self.assertEqual(attachments[1]["attachment_type"], "link")
        self.assertEqual(attachments[1]["title"], "链接标题")
        self.assertEqual(attachments[1]["description"], "链接摘要")

        job = self.connection.execute(
            """
            SELECT status, total_seen_count, inserted_count, skipped_count,
                   failed_count, collector_type, finished_at, error_message
            FROM collection_jobs WHERE id = ?
            """,
            (job_id,),
        ).fetchone()
        self.assertEqual(job["status"], "completed")
        self.assertEqual(int(job["total_seen_count"]), 2)
        self.assertEqual(int(job["inserted_count"]), 1)
        self.assertEqual(int(job["skipped_count"]), 1)
        self.assertEqual(int(job["failed_count"]), 0)
        self.assertEqual(job["collector_type"], "weibo_api_v1")
        self.assertIsNotNone(job["finished_at"])
        self.assertIsNone(job["error_message"])

    def test_repeated_ingest_is_idempotent_even_after_soft_delete(self) -> None:
        account_id, group_id = self._create_target()
        attachment = AttachmentCandidate(
            attachment_type="file",
            source_url="https://files.example/report.pdf",
            file_name="report.pdf",
        )
        candidate = self._candidate(
            account_id,
            group_id,
            source_message_id="stable-api-id",
            attachments=[attachment],
        )

        first_job_id = create_running_api_job(
            self.connection,
            account_id=account_id,
            group_id=group_id,
            range_start=RANGE_START,
            range_end=RANGE_END,
        )
        first = ingest_api_candidates(
            self.connection,
            collection_job_id=first_job_id,
            account_id=account_id,
            group_id=group_id,
            candidates=[candidate],
        )
        self.assertEqual(first.inserted_count, 1)

        original_message = self.connection.execute(
            "SELECT id FROM messages WHERE source_message_id = 'stable-api-id'"
        ).fetchone()
        self.connection.execute(
            """
            UPDATE messages
            SET is_deleted = 1, deleted_at = '2026-07-03 00:00:00'
            WHERE id = ?
            """,
            (int(original_message["id"]),),
        )

        second_job_id = create_running_api_job(
            self.connection,
            account_id=account_id,
            group_id=group_id,
            range_start=RANGE_START,
            range_end=RANGE_END,
        )
        second = ingest_api_candidates(
            self.connection,
            collection_job_id=second_job_id,
            account_id=account_id,
            group_id=group_id,
            candidates=[candidate],
        )
        self.connection.commit()

        self.assertEqual(second.inserted_count, 0)
        self.assertEqual(second.duplicate_count, 1)
        self.assertEqual(second.skipped_count, 1)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1
        )
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM attachments").fetchone()[0], 1
        )
        persisted = self.connection.execute(
            """
            SELECT id, collection_job_id, is_deleted, deleted_at
            FROM messages WHERE source_message_id = 'stable-api-id'
            """
        ).fetchone()
        self.assertEqual(int(persisted["id"]), int(original_message["id"]))
        self.assertEqual(int(persisted["collection_job_id"]), first_job_id)
        self.assertEqual(int(persisted["is_deleted"]), 1)
        self.assertEqual(persisted["deleted_at"], "2026-07-03 00:00:00")

        second_job = self.connection.execute(
            """
            SELECT status, total_seen_count, inserted_count, skipped_count, failed_count
            FROM collection_jobs WHERE id = ?
            """,
            (second_job_id,),
        ).fetchone()
        self.assertEqual(
            tuple(second_job),
            ("completed", 1, 0, 1, 0),
        )

    def test_api_failure_status_survives_separate_commits(self) -> None:
        account_id, group_id = self._create_target()
        self.connection.commit()

        with self._committed_connection() as connection:
            job_id = create_running_api_job(
                connection,
                account_id=account_id,
                group_id=group_id,
                range_start=RANGE_START,
                range_end=RANGE_END,
            )

        try:
            raise WeiboApiError("offline simulated API failure")
        except WeiboApiError as error:
            with self._committed_connection() as connection:
                mark_api_job_failed(connection, job_id, str(error))

        with self._committed_connection() as connection:
            job = connection.execute(
                """
                SELECT status, failed_count, finished_at, error_message, collector_type
                FROM collection_jobs WHERE id = ?
                """,
                (job_id,),
            ).fetchone()

        self.assertEqual(job["status"], "failed")
        self.assertEqual(int(job["failed_count"]), 1)
        self.assertIsNotNone(job["finished_at"])
        self.assertEqual(job["error_message"], "offline simulated API failure")
        self.assertEqual(job["collector_type"], "weibo_api_v1")

    def test_unexpected_collection_errors_do_not_reflect_secrets(self) -> None:
        detail = public_collection_error(RuntimeError("Cookie: SUB=do-not-reflect"))
        self.assertNotIn("SUB", detail)
        self.assertNotIn("do-not-reflect", detail)

    def test_backfilling_old_messages_does_not_regress_current_user_profile(self) -> None:
        account_id, group_id = self._create_target()
        recent_job_id = create_running_api_job(
            self.connection,
            account_id=account_id,
            group_id=group_id,
            range_start=RANGE_START,
            range_end=RANGE_END,
        )
        recent = self._candidate(
            account_id,
            group_id,
            source_message_id="recent-profile",
            sender_name="新昵称",
            sender_avatar_url="https://img.example/new.jpg",
            sent_at=datetime(2026, 7, 2, 12, 0, 0),
        )
        ingest_api_candidates(
            self.connection,
            collection_job_id=recent_job_id,
            account_id=account_id,
            group_id=group_id,
            candidates=[recent],
        )

        old_job_id = create_running_api_job(
            self.connection,
            account_id=account_id,
            group_id=group_id,
            range_start=RANGE_START,
            range_end=RANGE_END,
        )
        old = self._candidate(
            account_id,
            group_id,
            source_message_id="old-profile",
            sender_name="旧昵称",
            sender_avatar_url="https://img.example/old.jpg",
            sent_at=datetime(2026, 7, 1, 12, 0, 0),
        )
        ingest_api_candidates(
            self.connection,
            collection_job_id=old_job_id,
            account_id=account_id,
            group_id=group_id,
            candidates=[old],
        )

        user = self.connection.execute(
            "SELECT id, display_name, avatar_url FROM chat_users WHERE source_user_id = 'user-1'"
        ).fetchone()
        member = self.connection.execute(
            """
            SELECT display_name_in_group, first_seen_at, last_seen_at
            FROM group_members WHERE group_id = ? AND user_id = ?
            """,
            (group_id, int(user["id"])),
        ).fetchone()
        self.assertEqual(user["display_name"], "新昵称")
        self.assertEqual(user["avatar_url"], "https://img.example/new.jpg")
        self.assertEqual(member["display_name_in_group"], "新昵称")
        self.assertEqual(member["first_seen_at"], "2026-07-01 12:00:00")
        self.assertEqual(member["last_seen_at"], "2026-07-02 12:00:00")

    def test_legacy_file_import_supports_repeated_users_with_plain_sqlite_rows(self) -> None:
        file_db_path = Path(self.temp_dir.name) / "file-import.sqlite3"
        schema = (PROJECT_DIR / "scripts" / "schema.sql").read_text(encoding="utf-8")
        with sqlite3.connect(file_db_path) as connection:
            connection.executescript(schema)

        source_path = Path(self.temp_dir.name) / "messages.json"
        source_path.write_text(
            json.dumps(
                {
                    "account": "文件账号",
                    "group": "文件群",
                    "messages": [
                        {
                            "source_message_id": "file-1",
                            "source_user_id": "same-user",
                            "sender_name": "旧名称",
                            "sent_at": "2026-07-01 10:00:00",
                            "content_text": "one",
                        },
                        {
                            "source_message_id": "file-2",
                            "source_user_id": "same-user",
                            "sender_name": "新名称",
                            "sent_at": "2026-07-01 11:00:00",
                            "content_text": "two",
                        },
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        summary = import_file(file_db_path, source_path)
        self.assertEqual(summary.inserted_count, 2)
        with sqlite3.connect(file_db_path) as connection:
            user = connection.execute(
                "SELECT display_name FROM chat_users WHERE source_user_id = 'same-user'"
            ).fetchone()
        self.assertEqual(user[0], "新名称")

    def test_file_import_uses_shared_noise_filter_and_keeps_greetings(self) -> None:
        file_db_path = Path(self.temp_dir.name) / "filtered-import.sqlite3"
        schema = (PROJECT_DIR / "scripts" / "schema.sql").read_text(encoding="utf-8")
        with sqlite3.connect(file_db_path) as connection:
            connection.executescript(schema)

        source_path = Path(self.temp_dir.name) / "filtered-messages.json"
        base = {
            "source_user_id": "same-user",
            "sender_name": "群友",
            "sent_at": "2026-07-01 10:00:00",
        }
        source_path.write_text(
            json.dumps(
                {
                    "account": "文件账号",
                    "group": "文件群",
                    "messages": [
                        {
                            **base,
                            "source_message_id": "red-flag",
                            "content_text": "opaque",
                            "is_red_packet": True,
                        },
                        {
                            **base,
                            "source_message_id": "red-template",
                            "content_text": "最佳手气通知",
                            "raw_payload": {"template": "{{nick.DATA}} 是本轮最佳手气"},
                        },
                        {
                            **base,
                            "source_message_id": "badge",
                            "content_text": "标识通知",
                            "raw_payload": {
                                "template": "恭喜{{nick.DATA}}今日获得“{{title.DATA}}”标识"
                            },
                        },
                        {
                            **base,
                            "source_message_id": "greeting",
                            "content_text": "早上好",
                        },
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        summary = import_file(file_db_path, source_path)
        self.assertEqual(summary.inserted_count, 1)
        self.assertEqual(summary.red_packet_count, 2)
        self.assertEqual(summary.filtered_system_notice_count, 1)
        self.assertEqual(summary.skipped_count, 3)
        with sqlite3.connect(file_db_path) as connection:
            contents = connection.execute("SELECT content_text FROM messages").fetchall()
            job = connection.execute(
                """
                SELECT filtered_red_packet_count, filtered_system_notice_count
                FROM collection_jobs ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
        self.assertEqual(contents, [("早上好",)])
        self.assertEqual(job, (2, 1))

    def test_group_with_history_cannot_be_rebound_to_another_remote_group(self) -> None:
        account_id, group_id = self._create_target(source_group_id="400001")
        job_id = create_running_api_job(
            self.connection,
            account_id=account_id,
            group_id=group_id,
            range_start=RANGE_START,
            range_end=RANGE_END,
        )
        ingest_api_candidates(
            self.connection,
            collection_job_id=job_id,
            account_id=account_id,
            group_id=group_id,
            candidates=[self._candidate(account_id, group_id)],
        )

        for request in (
            UpsertApiTargetRequest(
                account_id=account_id,
                account_name="采集账号",
                group_id=group_id,
                group_name="测试群",
                source_group_id="400002",
            ),
            UpsertApiTargetRequest(
                account_name="采集账号",
                group_name="测试群",
                source_group_id="400002",
            ),
        ):
            with self.subTest(explicit_group_id=request.group_id is not None):
                with self.assertRaises(weibo_api_module.HTTPException) as raised:
                    upsert_api_target(request, self.connection)
                self.assertEqual(raised.exception.status_code, 409)

        stored = self.connection.execute(
            "SELECT source_group_id FROM chat_groups WHERE id = ?",
            (group_id,),
        ).fetchone()
        self.assertEqual(stored["source_group_id"], "400001")


if __name__ == "__main__":
    unittest.main()
