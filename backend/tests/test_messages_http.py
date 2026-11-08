"""Route-level regressions. No lifespan, real database, Cookie or worker is used."""
from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.database import apply_database_migrations, get_connection
from app.main import app


class MessagesHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        apply_database_migrations(self.db)
        self.addCleanup(self.db.close)
        self.db.executemany(
            "INSERT INTO weibo_accounts (id, display_name) VALUES (?, ?)",
            [(1, "账号甲"), (2, "账号乙")],
        )
        self.db.executemany(
            "INSERT INTO chat_groups (id, account_id, name) VALUES (?, ?, ?)",
            [(11, 1, "同名群"), (12, 1, "另一群"), (21, 2, "同名群")],
        )
        self.db.execute("INSERT INTO chat_users (id, display_name) VALUES (1, '用户')")
        self.db.executemany(
            """INSERT INTO messages
               (id, account_id, group_id, user_id, sent_at, message_type, content_text, is_deleted)
               VALUES (?, ?, ?, 1, ?, ?, '相同正文', ?)""",
            [(1, 1, 11, "2026-08-23 10:00:00", "image", 0),
             (2, 1, 12, "2026-08-23 10:01:00", "link", 0),
             (3, 2, 21, "2026-08-23 10:00:00", "image", 0),
             (4, 1, 11, "2026-08-23 10:02:00", "file", 1),
             (5, 2, 21, "2026-08-23 10:02:00", "file", 1)],
        )
        self.db.executemany(
            "INSERT INTO attachments (message_id, attachment_type, title) VALUES (?, ?, ?)",
            [(1, "image", "图一"), (1, "image", "图二"), (2, "link", "独特链接标题"),
             (3, "image", "乙图"), (4, "file", "甲删除附件"), (5, "file", "乙删除附件")],
        )
        self.db.commit()

        def isolated_connection():
            with self.db:
                yield self.db

        previous = app.dependency_overrides.copy()
        self.addCleanup(self.restore_overrides, previous)
        app.dependency_overrides[get_connection] = isolated_connection
        # Deliberately NOT a context manager: production lifespan must never run.
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    @staticmethod
    def restore_overrides(previous):
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)

    def messages(self, **params):
        response = self.client.get("/api/messages", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def post(self, action, filters, **extra):
        response = self.client.post(f"/api/messages/{action}", json={"filters": filters, **extra})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_account_only_filter_isolates_messages_and_attachment_totals(self):
        data = self.messages(account_id=1)
        self.assertEqual({m["id"] for m in data["items"]}, {1, 2})
        self.assertEqual(data["total"], 2)
        self.assertEqual(data["attachment_summary"], {
            "total_count": 3, "message_count": 2, "by_type": {"image": 2, "link": 1},
        })

    def test_group_only_filter_and_mismatched_account_group(self):
        self.assertEqual([m["id"] for m in self.messages(group_id=11)["items"]], [1])
        self.assertEqual(self.messages(account_id=2, group_id=11)["total"], 0)

    def test_pagination_keeps_full_filtered_summary(self):
        first = self.messages(account_id=1, limit=1)
        self.assertEqual(len(first["items"]), 1)
        self.assertTrue(first["has_more"])
        second = self.messages(account_id=1, limit=1, **first["next_cursor"])
        self.assertEqual([m["id"] for m in first["items"] + second["items"]], [2, 1])
        self.assertEqual(second["total"], 2)
        self.assertEqual(first["attachment_summary"]["total_count"], 3)
        self.assertEqual(first["attachment_summary"], second["attachment_summary"])
        self.assertFalse(second["has_more"])

    def test_equal_timestamp_cursor_has_no_missing_or_repeated_messages(self):
        seen = []
        cursor = {}
        while True:
            data = self.messages(limit=1, **cursor)
            seen.extend(m["id"] for m in data["items"])
            if not data["has_more"]:
                break
            self.assertLess(len(seen), 4, "cursor must make progress")
            cursor = data["next_cursor"]
        self.assertEqual(seen, [2, 3, 1])

    def test_link_option_filter_keyword_and_inclusive_time_boundary(self):
        options = self.client.get("/api/filter-options").json()
        self.assertIn("link", options["message_types"])
        data = self.messages(account_id=1, message_type="link", keyword="独特链接标题",
                             date_from="2026-08-23 10:01:00", date_to="2026-08-23 10:01:00")
        self.assertEqual([m["id"] for m in data["items"]], [2])
        self.assertEqual(data["attachment_summary"]["by_type"], {"link": 1})
        self.assertEqual(self.messages(has_attachment=False)["total"], 0)

    def test_invalid_http_query_and_missing_detail(self):
        for params in ({"limit": 0}, {"limit": 201}, {"offset": -1}, {"account_id": "bad"}):
            with self.subTest(params=params):
                self.assertEqual(self.client.get("/api/messages", params=params).status_code, 422)
        self.assertEqual(self.client.get("/api/messages/999").status_code, 404)
        self.assertEqual(self.client.get("/api/messages/4").status_code, 404)
        self.assertEqual(self.client.get("/api/messages/4?include_deleted=true").status_code, 200)

    def test_delete_restore_and_hard_delete_remain_account_scoped(self):
        filters = {"account_id": 1}
        self.assertEqual(self.post("delete-preview", filters)["preview_count"], 2)
        self.assertEqual(self.post("soft-delete", filters, confirm=True)["deleted_count"], 2)
        self.assertEqual(self.messages(account_id=2)["total"], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM attachments").fetchone()[0], 6)
        self.assertEqual(self.post("restore-preview", filters)["preview_count"], 3)
        self.assertEqual(self.post("restore", filters, confirm=True)["restored_count"], 3)
        self.assertEqual(self.messages(account_id=2, deleted_only=True)["total"], 1)
        self.post("soft-delete", {"account_id": 1, "group_id": 11}, confirm=True)
        preview = self.post("hard-delete-preview", filters)
        self.assertEqual((preview["preview_count"], preview["attachment_record_count"]), (2, 3))
        result = self.post("hard-delete", filters, confirm=True)
        self.assertEqual((result["deleted_count"], result["attachment_record_count"]), (2, 3))
        self.assertFalse(result["delete_attachment_files"])
        self.assertEqual([r[0] for r in self.db.execute("SELECT id FROM messages ORDER BY id")], [2, 3, 5])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM attachments").fetchone()[0], 3)

    def test_mutations_require_explicit_confirmation(self):
        for action in ("soft-delete", "restore", "hard-delete"):
            with self.subTest(action=action):
                response = self.client.post(f"/api/messages/{action}", json={"filters": {"account_id": 1}, "confirm": False})
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 5)
        self.assertEqual(self.messages()["total"], 3)


if __name__ == "__main__":
    unittest.main()
