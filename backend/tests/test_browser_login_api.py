from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException


BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.api import browser_login as browser_login_module  # noqa: E402
from app.api.browser_login import (  # noqa: E402
    ConfirmBrowserGroupRequest,
    confirm_browser_group,
    ensure_browser_session_target,
    ensure_collection_is_idle,
    require_loopback_client,
)
from app.services.browser_login_manager import BrowserLoginSession  # noqa: E402


class ImmediateConfirmationManager:
    def __init__(self, session: BrowserLoginSession):
        self.session = session

    def confirm_group(self, session_id, candidate_source_group_id, bind):
        if session_id != self.session.session_id:
            raise AssertionError("wrong session")
        if candidate_source_group_id != self.session.candidate_source_group_id:
            raise AssertionError("wrong candidate")
        bind(self.session)
        self.session.status = "binding_confirmed"
        return self.session.public_dict()


class BrowserLoginApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.connection = sqlite3.connect(Path(self.temp_dir.name) / "collector.sqlite3")
        self.addCleanup(self.connection.close)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(
            (PROJECT_DIR / "scripts" / "schema.sql").read_text(encoding="utf-8")
        )
        self.connection.execute("INSERT INTO weibo_accounts (display_name) VALUES ('账号 A')")
        self.account_id = int(self.connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        self.connection.execute(
            "INSERT INTO chat_groups (account_id, name) VALUES (?, '群聊 A')",
            (self.account_id,),
        )
        self.group_id = int(self.connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        self.connection.commit()
        self.request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))

    def _session(self, candidate: str = "123456789") -> BrowserLoginSession:
        now = datetime.now(timezone.utc)
        return BrowserLoginSession(
            session_id="test-session",
            account_id=self.account_id,
            group_id=self.group_id,
            status="group_candidate_found",
            created_at=now,
            expires_at=now + timedelta(minutes=10),
            cookie_ready=True,
            candidate_source_group_id=candidate,
            candidate_captured_at=now,
        )

    def test_target_validation_rejects_missing_or_inactive_records(self) -> None:
        with self.assertRaises(HTTPException) as missing_account:
            ensure_browser_session_target(
                self.connection,
                account_id=999,
                group_id=None,
            )
        self.assertEqual(missing_account.exception.status_code, 404)

        self.connection.execute(
            "UPDATE chat_groups SET is_active = 0 WHERE id = ?",
            (self.group_id,),
        )
        with self.assertRaises(HTTPException) as inactive_group:
            ensure_browser_session_target(
                self.connection,
                account_id=self.account_id,
                group_id=self.group_id,
            )
        self.assertEqual(inactive_group.exception.status_code, 400)

    def test_running_or_awaiting_collection_blocks_browser_session(self) -> None:
        self.connection.execute(
            """
            INSERT INTO collection_jobs (
                account_id, group_id, range_start, range_end, status, collector_type
            ) VALUES (?, ?, '2026-08-01', '2026-08-02', 'awaiting_confirmation', 'weibo_api_v2')
            """,
            (self.account_id, self.group_id),
        )
        with self.assertRaises(HTTPException) as blocked:
            ensure_collection_is_idle(self.connection)
        self.assertEqual(blocked.exception.status_code, 409)

    def test_confirm_group_binds_candidate_but_rejects_duplicate(self) -> None:
        manager = ImmediateConfirmationManager(self._session())
        with patch.object(browser_login_module, "get_browser_login_manager", return_value=manager):
            result = confirm_browser_group(
                "test-session",
                ConfirmBrowserGroupRequest(candidate_source_group_id="123456789"),
                self.request,
                self.connection,
            )
        self.assertEqual(result["status"], "binding_confirmed")
        self.assertEqual(
            self.connection.execute(
                "SELECT source_group_id FROM chat_groups WHERE id = ?",
                (self.group_id,),
            ).fetchone()[0],
            "123456789",
        )
        self.connection.commit()

        self.connection.execute(
            "INSERT INTO chat_groups (account_id, name, source_group_id) VALUES (?, '群聊 B', '222')",
            (self.account_id,),
        )
        duplicate_group_id = int(
            self.connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        )
        self.connection.commit()
        duplicate_session = self._session("222")
        duplicate_session.group_id = self.group_id
        manager = ImmediateConfirmationManager(duplicate_session)
        with (
            patch.object(browser_login_module, "get_browser_login_manager", return_value=manager),
            self.assertRaises(HTTPException) as conflict,
        ):
            confirm_browser_group(
                "test-session",
                ConfirmBrowserGroupRequest(candidate_source_group_id="222"),
                self.request,
                self.connection,
            )
        self.assertEqual(conflict.exception.status_code, 409)
        self.assertNotEqual(duplicate_group_id, self.group_id)

    def test_remote_client_is_rejected(self) -> None:
        remote = SimpleNamespace(client=SimpleNamespace(host="192.168.1.20"))
        with self.assertRaises(HTTPException) as blocked:
            require_loopback_client(remote)
        self.assertEqual(blocked.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
