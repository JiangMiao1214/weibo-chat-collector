"""Quote lookup against isolated SQLite; never starts the production lifespan."""
import sqlite3
import sys
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.database import apply_database_migrations, get_connection
from app.main import app


class QuoteContextHttpTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        apply_database_migrations(self.db)
        self.addCleanup(self.db.close)
        self.db.executemany("INSERT INTO weibo_accounts (id, display_name) VALUES (?, ?)", [(1, "甲"), (2, "乙")])
        self.db.executemany("INSERT INTO chat_groups (id, account_id, name) VALUES (?, ?, '同名群')", [(11, 1), (12, 1), (21, 2)])
        self.db.execute("INSERT INTO chat_users (id, display_name) VALUES (1, '同名用户')")
        self.insert(1, "完整原文", "2026-08-01 10:00:00")
        self.insert(10, "「完整原文」 - - - 回复", "2026-08-23 10:00:00")
        self.insert(21, "完整原文", "2026-08-01 10:00:00", account=2, group=21)
        self.insert(12, "完整原文", "2026-08-01 10:00:00", group=12)
        self.insert(30, "完整原文", "2026-08-01 10:00:00", deleted=1)
        self.db.execute("INSERT INTO attachments (message_id, attachment_type, title) VALUES (1, 'image', '隔离图片')")
        self.db.commit()
        previous = app.dependency_overrides.copy()
        def restore():
            app.dependency_overrides.clear()
            app.dependency_overrides.update(previous)
        self.addCleanup(restore)
        app.dependency_overrides[get_connection] = lambda: self.db
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def insert(self, id, text, sent_at, account=1, group=11, deleted=0):
        self.db.execute("""INSERT INTO messages
            (id, account_id, group_id, user_id, sent_at, message_type, content_text, is_deleted, raw_payload)
            VALUES (?, ?, ?, 1, ?, 'text', ?, ?, '{"private":"not-for-context"}')""",
            (id, account, group, sent_at, text, deleted))

    def lookup(self, source=10, **params):
        return self.client.get(f"/api/messages/{source}/quote-context", params={"account_id": 1, "group_id": 11, **params})

    def test_finds_unloaded_message_outside_date_filter_without_mutating_database(self):
        visible = self.client.get("/api/messages", params={"account_id": 1, "group_id": 11, "date_from": "2026-08-23", "limit": 1}).json()
        self.assertEqual([m["id"] for m in visible["items"]], [10])
        before = self.db.total_changes
        response = self.lookup()
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["status"], "matched")
        self.assertEqual(data["match_method"], "full_text")
        self.assertEqual(data["target"]["id"], 1)
        self.assertEqual(data["target"]["attachments"][0]["title"], "隔离图片")
        self.assertNotIn("raw_payload", data["target"])
        self.assertTrue(all(m["account_id"] == 1 and m["group_id"] == 11 and m["is_deleted"] == 0 for m in data["items"]))
        self.assertEqual(self.db.total_changes, before)

    def test_ambiguous_matches_return_no_target_or_content(self):
        self.insert(2, "完整原文", "2026-08-02 10:00:00")
        data = self.lookup().json()
        self.assertEqual(data.get("status"), "ambiguous")
        self.assertIsNone(data["target"])
        self.assertEqual(data["items"], [])

    def test_no_match_and_malformed_quotes_are_explicit(self):
        self.db.execute("DELETE FROM attachments WHERE message_id=1")
        self.db.execute("DELETE FROM messages WHERE id=1")
        self.assertEqual(self.lookup().json().get("status"), "not_found")
        for text in ("普通正文", "「缺少闭合 - - - 回复", "「原文」没有分隔符"):
            self.db.execute("UPDATE messages SET content_text=? WHERE id=10", (text,))
            self.assertEqual(self.lookup().json().get("status"), "not_quote")

    def test_request_scope_missing_source_and_deleted_source_are_checked(self):
        self.assertEqual(self.lookup(account_id=2).status_code, 404)
        self.assertEqual(self.lookup(group_id=12).status_code, 404)
        self.assertEqual(self.lookup(source=999).status_code, 404)
        self.assertEqual(self.lookup(deleted_only=True).status_code, 404)
        self.assertEqual(self.client.get("/api/messages/10/quote-context").status_code, 422)
        self.assertEqual(self.lookup(account_id=0).status_code, 422)

    def test_deleted_view_only_matches_deleted_messages(self):
        self.insert(31, "「完整原文」 - - - 已删除回复", "2026-08-23 10:00:00", deleted=1)
        self.assertEqual(self.lookup(source=31).status_code, 404)
        data = self.lookup(source=31, deleted_only=True).json()
        self.assertEqual(data.get("status"), "matched")
        self.assertEqual(data["target"]["id"], 30)
        self.assertTrue(all(m["is_deleted"] == 1 for m in data["items"]))

    def test_future_equal_time_and_text_prefix_are_not_candidates(self):
        self.db.execute("UPDATE messages SET sent_at='2026-08-23 10:00:00' WHERE id=1")
        self.insert(2, "完整原文", "2026-08-24 10:00:00")
        self.insert(3, "完整原文加后缀", "2026-08-01 10:00:00")
        self.insert(4, "完整 原文", "2026-08-01 10:00:00")
        self.assertEqual(self.lookup().json().get("status"), "not_found")

    def test_nested_quotes_and_line_endings_match_without_wildcards(self):
        text = "「嵌套」 - - - 100%_'\n内容"
        self.db.execute("UPDATE messages SET content_text=? WHERE id=1", ("  " + text.replace("\n", "\r\n") + "\n",))
        self.db.execute("UPDATE messages SET content_text=? WHERE id=10", ("「" + text + "」 - - - 回复",))
        self.assertEqual(self.lookup().json().get("status"), "matched")

    def test_context_is_bounded_and_ordered_with_equal_timestamps(self):
        for id in range(100, 120):
            self.insert(id, f"邻居{id}", "2026-08-01 10:00:00")
        for id in range(200, 220):
            self.insert(id, f"更早{id}", "2026-07-31 10:00:00")
        data = self.lookup().json()
        self.assertEqual(data.get("status"), "matched")
        ids = [m["id"] for m in data["items"]]
        self.assertEqual(ids, [104, 103, 102, 101, 100, 1, 219, 218, 217, 216, 215])
        self.assertTrue(data["has_older"])
        self.assertTrue(data["has_newer"])


if __name__ == "__main__":
    unittest.main()
