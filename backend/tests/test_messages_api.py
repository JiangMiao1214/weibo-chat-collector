from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.api.messages import (  # noqa: E402
    build_message_filters,
    get_filter_options,
    list_messages,
    summarize_matching_attachments,
)
from app.database import apply_database_migrations  # noqa: E402


class MessagesApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        apply_database_migrations(self.connection)

        self.connection.execute(
            "INSERT INTO weibo_accounts (display_name) VALUES ('测试账号')"
        )
        self.account_id = int(
            self.connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        )
        self.connection.execute(
            "INSERT INTO chat_groups (account_id, name) VALUES (?, '测试群')",
            (self.account_id,),
        )
        self.group_id = int(
            self.connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        )
        self.connection.execute(
            "INSERT INTO chat_users (display_name) VALUES ('测试用户')"
        )
        self.user_id = int(
            self.connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        )

        self.text_message_id = self._insert_message("text", "普通文本", "2026-08-23 10:00:00")
        self.image_message_id = self._insert_message(
            "image", "两张图片", "2026-08-23 10:01:00"
        )
        self.link_message_id = self._insert_message("link", "一个链接", "2026-08-23 10:02:00")
        self.deleted_message_id = self._insert_message(
            "file", "已删除文件", "2026-08-23 10:03:00", is_deleted=1
        )
        self.connection.executemany(
            "INSERT INTO attachments (message_id, attachment_type) VALUES (?, ?)",
            [
                (self.image_message_id, "image"),
                (self.image_message_id, "image"),
                (self.link_message_id, "link"),
                (self.deleted_message_id, "file"),
            ],
        )
        self.connection.commit()

    def tearDown(self) -> None:
        self.connection.close()

    def _insert_message(
        self,
        message_type: str,
        content_text: str,
        sent_at: str,
        *,
        is_deleted: int = 0,
    ) -> int:
        self.connection.execute(
            """
            INSERT INTO messages (
                account_id, group_id, user_id, sent_at, message_type,
                content_text, is_deleted
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.account_id,
                self.group_id,
                self.user_id,
                sent_at,
                message_type,
                content_text,
                is_deleted,
            ),
        )
        return int(self.connection.execute("SELECT last_insert_rowid()").fetchone()[0])

    def _build_filters(
        self,
        *,
        message_type: str | None = None,
        has_attachment: bool | None = None,
    ) -> tuple[list[str], list[object]]:
        return build_message_filters(
            account_id=self.account_id,
            account=None,
            group_id=self.group_id,
            group=None,
            user=None,
            date_from=None,
            date_to=None,
            keyword=None,
            message_type=message_type,
            has_attachment=has_attachment,
            include_deleted=False,
        )

    def test_filter_options_always_include_supported_types(self) -> None:
        options = get_filter_options(self.connection)

        self.assertEqual(
            options["message_types"],
            ["text", "image", "link", "file", "video", "system"],
        )

    def test_attachment_summary_counts_records_and_messages(self) -> None:
        filters, params = self._build_filters()

        summary = summarize_matching_attachments(self.connection, filters, params)

        self.assertEqual(summary["total_count"], 3)
        self.assertEqual(summary["message_count"], 2)
        self.assertEqual(summary["by_type"], {"image": 2, "link": 1})

    def test_attachment_summary_respects_message_filters(self) -> None:
        filters, params = self._build_filters(message_type="link")
        link_summary = summarize_matching_attachments(self.connection, filters, params)

        filters, params = self._build_filters(has_attachment=False)
        no_attachment_summary = summarize_matching_attachments(
            self.connection, filters, params
        )

        self.assertEqual(
            link_summary,
            {"total_count": 1, "message_count": 1, "by_type": {"link": 1}},
        )
        self.assertEqual(
            no_attachment_summary,
            {"total_count": 0, "message_count": 0, "by_type": {}},
        )

    def test_messages_response_includes_attachment_summary(self) -> None:
        result = list_messages(
            account_id=self.account_id,
            account=None,
            group_id=self.group_id,
            group=None,
            user=None,
            date_from=None,
            date_to=None,
            keyword=None,
            message_type=None,
            has_attachment=None,
            include_deleted=False,
            deleted_only=False,
            limit=50,
            offset=0,
            before_sent_at=None,
            before_id=None,
            connection=self.connection,
        )

        self.assertEqual(result["total"], 3)
        self.assertEqual(
            result["attachment_summary"],
            {"total_count": 3, "message_count": 2, "by_type": {"image": 2, "link": 1}},
        )


if __name__ == "__main__":
    unittest.main()
