from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.browser_login_manager import (  # noqa: E402
    BrowserLoginManager,
    BrowserSessionBusyError,
    BrowserSessionStateError,
)


class FakeStdin:
    def __init__(self) -> None:
        self.writes: list[str] = []

    def write(self, value: str) -> int:
        self.writes.append(value)
        return len(value)

    def flush(self) -> None:
        return None


class FakeProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.stdin = FakeStdin()

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.returncode = 0
        return 0

    def kill(self) -> None:
        self.returncode = -9


class BrowserLoginManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        helper = root / "login-session.js"
        helper.write_text("// test helper", encoding="utf-8")
        self.manager = BrowserLoginManager(
            helper_path=helper,
            node_path="node",
            chrome_path="",
            auth_dir=root / "auth",
            timeout_seconds=60,
        )

        def launch(session):
            session.process = FakeProcess()

        self.manager._launch_helper_locked = launch  # type: ignore[method-assign]
        self.addCleanup(self.manager.shutdown)

    def test_only_one_session_can_be_active_and_cancel_is_idempotent(self) -> None:
        first = self.manager.start_session(account_id=1, group_id=2)
        with self.assertRaises(BrowserSessionBusyError):
            self.manager.start_session(account_id=3, group_id=4)

        cancelled = self.manager.cancel_session(first["session_id"])
        repeated = self.manager.cancel_session(first["session_id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(repeated["status"], "cancelled")
        session = self.manager._sessions[first["session_id"]]
        self.assertEqual(session.process.stdin.writes, ['{"command":"close"}\n'])

    def test_cookie_event_is_not_exposed_and_candidate_requires_confirmation(self) -> None:
        created = self.manager.start_session(account_id=7, group_id=8)
        session_id = created["session_id"]
        saved: list[list[dict[str, str]]] = []

        def save(_session, cookies):
            saved.append(cookies)

        self.manager._save_cookies_locked = save  # type: ignore[method-assign]
        self.manager.handle_event(
            session_id,
            {"event": "group_candidate", "source_group_id": "123456789"},
        )
        secret_cookies = [{"name": "SUB", "value": "secret", "domain": ".weibo.com"}]
        self.manager.handle_event(session_id, {"event": "cookies", "cookies": secret_cookies})

        public = self.manager.get_session(session_id)
        self.assertEqual(saved, [secret_cookies])
        self.assertNotIn("cookies", public)
        self.assertNotIn("secret", repr(public))
        self.assertTrue(public["cookie_ready"])
        self.assertEqual(public["status"], "group_candidate_found")
        self.assertEqual(public["candidate_source_group_id"], "123456789")

        with self.assertRaises(BrowserSessionStateError) as mismatch:
            self.manager.confirm_group(session_id, "987654321", lambda _session: None)
        self.assertEqual(mismatch.exception.code, "group_candidate_changed")

        bound: list[tuple[int, int | None]] = []
        confirmed = self.manager.confirm_group(
            session_id,
            "123456789",
            lambda session: bound.append((session.account_id, session.group_id)),
        )
        self.assertEqual(bound, [(7, 8)])
        self.assertEqual(confirmed["status"], "binding_confirmed")
        self.assertEqual(
            self.manager.cancel_session(session_id)["status"],
            "binding_confirmed",
        )
        with self.assertRaises(BrowserSessionStateError):
            self.manager.rediscover(session_id)

    def test_rediscover_discards_old_candidate_and_timeout_is_terminal(self) -> None:
        created = self.manager.start_session(account_id=1, group_id=2)
        session_id = created["session_id"]
        self.manager._save_cookies_locked = lambda session, cookies: None  # type: ignore[method-assign]
        self.manager.handle_event(
            session_id,
            {
                "event": "cookies",
                "cookies": [{"name": "SUB", "value": "secret", "domain": ".weibo.com"}],
            },
        )
        self.manager.handle_event(
            session_id,
            {"event": "group_candidate", "source_group_id": "10001"},
        )
        rediscovering = self.manager.rediscover(session_id)
        self.assertEqual(rediscovering["status"], "awaiting_group_selection")
        self.assertIsNone(rediscovering["candidate_source_group_id"])

        self.manager._expire_session(session_id)
        timed_out = self.manager.get_session(session_id)
        self.assertEqual(timed_out["status"], "timed_out")
        self.assertEqual(timed_out["error_code"], "login_timeout")

    def test_helper_timeout_event_maps_to_timed_out(self) -> None:
        created = self.manager.start_session(account_id=1, group_id=None)
        self.manager.handle_event(
            created["session_id"],
            {"event": "error", "error_code": "login_timeout"},
        )
        timed_out = self.manager.get_session(created["session_id"])
        self.assertEqual(timed_out["status"], "timed_out")
        self.assertEqual(timed_out["error_code"], "login_timeout")


if __name__ == "__main__":
    unittest.main()
