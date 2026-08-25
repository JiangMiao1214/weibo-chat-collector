from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import signal
import shutil
import subprocess
import tempfile
import threading
from typing import Any, Callable

from ..collectors.weibo_api import CookieProfileError, CookieProfileStore
from ..database import connection_context
from ..settings import get_settings


ACTIVE_SESSION_STATUSES = {
    "launching_browser",
    "awaiting_scan",
    "login_detected",
    "cookie_saved",
    "awaiting_group_selection",
    "group_candidate_found",
    "binding_confirmed",
}
TERMINAL_SESSION_STATUSES = {"completed", "cancelled", "timed_out", "failed"}
REDISCOVERABLE_SESSION_STATUSES = {
    "cookie_saved",
    "awaiting_group_selection",
    "group_candidate_found",
}

SAFE_ERROR_MESSAGES = {
    "browser_not_found": "未找到可用的 Chrome、Chromium 或 Edge。",
    "browser_launch_failed": "浏览器启动失败，请确认浏览器助手依赖和 Chrome 安装状态。",
    "login_session_busy": "已有浏览器登录会话正在运行。",
    "login_timeout": "扫码登录会话已超时，请重新开始。",
    "browser_closed": "浏览器窗口已关闭。",
    "login_cookie_missing": "未提取到有效微博登录态，请重新扫码并等待登录完成。",
    "group_not_observed": "尚未捕获目标群聊请求，请在浏览器中重新打开目标群聊。",
    "group_candidate_changed": "候选群 ID 已变化，请核对最新候选值。",
    "helper_protocol_error": "浏览器助手返回了无法识别的状态。",
}


class BrowserSessionBusyError(RuntimeError):
    def __init__(self, session: dict[str, Any]):
        super().__init__(SAFE_ERROR_MESSAGES["login_session_busy"])
        self.session = session


class BrowserSessionNotFoundError(LookupError):
    pass


class BrowserSessionStateError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(SAFE_ERROR_MESSAGES.get(code, "浏览器登录会话状态不允许该操作。"))


@dataclass(slots=True)
class BrowserLoginSession:
    session_id: str
    account_id: int
    group_id: int | None
    status: str
    created_at: datetime
    expires_at: datetime
    cookie_ready: bool = False
    candidate_source_group_id: str | None = None
    candidate_captured_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    process: subprocess.Popen[str] | None = field(default=None, repr=False)
    timeout_timer: threading.Timer | None = field(default=None, repr=False)
    temp_profile_dir: Path | None = field(default=None, repr=False)
    stop_timer: threading.Timer | None = field(default=None, repr=False)

    def public_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "account_id": self.account_id,
            "group_id": self.group_id,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "cookie_ready": self.cookie_ready,
            "candidate_source_group_id": self.candidate_source_group_id,
            "candidate_captured_at": (
                self.candidate_captured_at.isoformat()
                if self.candidate_captured_at is not None
                else None
            ),
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


class BrowserLoginManager:
    def __init__(
        self,
        *,
        helper_path: Path,
        node_path: str,
        chrome_path: str,
        auth_dir: Path,
        timeout_seconds: int,
        process_factory: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
    ) -> None:
        self.helper_path = helper_path
        self.node_path = node_path
        self.chrome_path = chrome_path
        self.auth_dir = auth_dir
        self.timeout_seconds = timeout_seconds
        self.process_factory = process_factory
        self._lock = threading.RLock()
        self._sessions: dict[str, BrowserLoginSession] = {}
        self._active_session_id: str | None = None

    def start_session(self, *, account_id: int, group_id: int | None) -> dict[str, Any]:
        with self._lock:
            active = self._active_session_locked()
            if active is not None:
                raise BrowserSessionBusyError(active.public_dict())

            now = datetime.now(timezone.utc)
            session = BrowserLoginSession(
                session_id=secrets.token_urlsafe(24),
                account_id=account_id,
                group_id=group_id,
                status="launching_browser",
                created_at=now,
                expires_at=now + timedelta(seconds=self.timeout_seconds),
                temp_profile_dir=Path(tempfile.mkdtemp(prefix="weibo-collector-browser-")),
            )
            self._sessions[session.session_id] = session
            self._active_session_id = session.session_id
            self._trim_sessions_locked()

            try:
                self._launch_helper_locked(session)
            except Exception:
                self._fail_locked(session, "browser_launch_failed")
                self._cleanup_profile_locked(session)
                self._active_session_id = None
                raise BrowserSessionStateError("browser_launch_failed")

            timer = threading.Timer(
                self.timeout_seconds,
                self._expire_session,
                args=(session.session_id,),
            )
            timer.daemon = True
            session.timeout_timer = timer
            timer.start()
            return session.public_dict()

    def get_session(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise BrowserSessionNotFoundError(session_id)
            return session.public_dict()

    def get_active_session(self) -> dict[str, Any] | None:
        with self._lock:
            active = self._active_session_locked()
            return active.public_dict() if active is not None else None

    def cancel_session(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._require_session_locked(session_id)
            if (
                session.status in TERMINAL_SESSION_STATUSES
                or session.status == "binding_confirmed"
            ):
                return session.public_dict()
            session.status = "cancelled"
            session.error_code = None
            session.error_message = None
            self._request_stop_locked(session)
            return session.public_dict()

    def rediscover(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._require_session_locked(session_id)
            if (
                not session.cookie_ready
                or session.status not in REDISCOVERABLE_SESSION_STATUSES
            ):
                raise BrowserSessionStateError("group_not_observed")
            session.candidate_source_group_id = None
            session.candidate_captured_at = None
            session.status = "awaiting_group_selection"
            return session.public_dict()

    def confirm_group(
        self,
        session_id: str,
        candidate_source_group_id: str,
        bind: Callable[[BrowserLoginSession], None],
    ) -> dict[str, Any]:
        with self._lock:
            session = self._require_session_locked(session_id)
            if session.group_id is None or not session.cookie_ready:
                raise BrowserSessionStateError("group_not_observed")
            if session.candidate_source_group_id != candidate_source_group_id:
                raise BrowserSessionStateError("group_candidate_changed")
            if session.status != "group_candidate_found":
                raise BrowserSessionStateError("group_not_observed")
            bind(session)
            session.status = "binding_confirmed"
            self._request_stop_locked(session)
            return session.public_dict()

    def shutdown(self) -> None:
        with self._lock:
            for session in self._sessions.values():
                if session.process is None or session.process.poll() is not None:
                    self._cleanup_profile_locked(session)
                    continue
                if session.status not in TERMINAL_SESSION_STATUSES:
                    session.status = "cancelled"
                self._request_stop_locked(session, force=True)

    def handle_event(self, session_id: str, event: dict[str, Any]) -> None:
        """Consume one helper event without ever logging or returning raw cookies."""

        event_name = event.get("event")
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.status in TERMINAL_SESSION_STATUSES:
                return
            if event_name == "browser_opened":
                session.status = "awaiting_scan"
            elif event_name == "awaiting_scan":
                session.status = "awaiting_scan"
            elif event_name == "login_detected":
                session.status = "login_detected"
            elif event_name == "cookies":
                cookies = event.get("cookies")
                if not isinstance(cookies, list):
                    self._fail_locked(session, "helper_protocol_error")
                    self._request_stop_locked(session)
                    return
                try:
                    self._save_cookies_locked(session, cookies)
                except (CookieProfileError, ValueError):
                    self._fail_locked(session, "login_cookie_missing")
                    self._request_stop_locked(session)
                    return
                except Exception:
                    self._fail_locked(session, "browser_launch_failed")
                    self._request_stop_locked(session)
                    return
                session.cookie_ready = True
                session.status = (
                    "group_candidate_found"
                    if session.candidate_source_group_id is not None
                    else "cookie_saved"
                )
            elif event_name == "awaiting_group_selection":
                if session.cookie_ready and session.candidate_source_group_id is None:
                    session.status = "awaiting_group_selection"
            elif event_name == "group_candidate":
                candidate = str(event.get("source_group_id") or "")
                if not candidate.isdigit():
                    return
                session.candidate_source_group_id = candidate
                session.candidate_captured_at = datetime.now(timezone.utc)
                if session.cookie_ready:
                    session.status = "group_candidate_found"
            elif event_name == "closed":
                if session.status == "binding_confirmed":
                    session.status = "completed"
                elif session.status not in TERMINAL_SESSION_STATUSES:
                    self._fail_locked(session, "browser_closed")
            elif event_name == "error":
                code = str(event.get("error_code") or "browser_launch_failed")
                if code not in SAFE_ERROR_MESSAGES:
                    code = "browser_launch_failed"
                if code == "login_timeout":
                    session.status = "timed_out"
                    session.error_code = code
                    session.error_message = SAFE_ERROR_MESSAGES[code]
                else:
                    self._fail_locked(session, code)
                self._request_stop_locked(session)
            else:
                self._fail_locked(session, "helper_protocol_error")
                self._request_stop_locked(session)

    def _launch_helper_locked(self, session: BrowserLoginSession) -> None:
        if not self.helper_path.is_file():
            raise FileNotFoundError(self.helper_path)
        env = os.environ.copy()
        env["WEIBO_BROWSER_SESSION_TIMEOUT_SECONDS"] = str(self.timeout_seconds)
        env["WEIBO_BROWSER_USER_DATA_DIR"] = str(session.temp_profile_dir)
        if self.chrome_path:
            env["WEIBO_BROWSER_CHROME_PATH"] = self.chrome_path
        session.process = self.process_factory(
            [self.node_path, str(self.helper_path)],
            cwd=str(self.helper_path.parent.parent),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            start_new_session=os.name != "nt",
        )
        threading.Thread(
            target=self._read_stdout,
            args=(session.session_id,),
            daemon=True,
            name=f"weibo-browser-{session.session_id[:8]}",
        ).start()
        threading.Thread(
            target=self._drain_stderr,
            args=(session.session_id,),
            daemon=True,
            name=f"weibo-browser-stderr-{session.session_id[:8]}",
        ).start()

    def _read_stdout(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            stream = session.process.stdout if session and session.process else None
        if stream is None:
            return
        for line in stream:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                self.handle_event(session_id, {"event": "invalid"})
                continue
            if isinstance(event, dict):
                self.handle_event(session_id, event)
            else:
                self.handle_event(session_id, {"event": "invalid"})
        self._handle_process_exit(session_id)

    def _drain_stderr(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            stream = session.process.stderr if session and session.process else None
        if stream is None:
            return
        for _line in stream:
            # Intentionally discard helper stderr. It may contain browser internals;
            # user-facing failures come from the sanitized JSON protocol only.
            pass

    def _handle_process_exit(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return
            if session.timeout_timer is not None:
                session.timeout_timer.cancel()
                session.timeout_timer = None
            if session.stop_timer is not None:
                session.stop_timer.cancel()
                session.stop_timer = None
            if session.status == "binding_confirmed":
                session.status = "completed"
            elif session.status not in TERMINAL_SESSION_STATUSES:
                self._fail_locked(session, "browser_closed")
            self._cleanup_profile_locked(session)
            if self._active_session_id == session_id:
                self._active_session_id = None

    def _save_cookies_locked(
        self,
        session: BrowserLoginSession,
        cookies: list[dict[str, Any]],
    ) -> None:
        CookieProfileStore(self.auth_dir).save(session.account_id, cookies)
        with connection_context() as connection:
            connection.execute(
                """
                UPDATE weibo_accounts
                SET auth_type = 'weibo_cookie_api', login_profile_name = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (f"account-{session.account_id}.cookies.json", session.account_id),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise RuntimeError("Account disappeared while saving browser login")

    def _expire_session(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.status in TERMINAL_SESSION_STATUSES:
                return
            session.status = "timed_out"
            session.error_code = "login_timeout"
            session.error_message = SAFE_ERROR_MESSAGES["login_timeout"]
            self._request_stop_locked(session)

    def _request_stop_locked(self, session: BrowserLoginSession, force: bool = False) -> None:
        process = session.process
        if process is None or process.poll() is not None:
            if session.timeout_timer is not None:
                session.timeout_timer.cancel()
                session.timeout_timer = None
            if session.stop_timer is not None:
                session.stop_timer.cancel()
                session.stop_timer = None
            self._cleanup_profile_locked(session)
            if self._active_session_id == session.session_id:
                self._active_session_id = None
            return
        if process.stdin is not None:
            try:
                process.stdin.write('{"command":"close"}\n')
                process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
        if force:
            if session.timeout_timer is not None:
                session.timeout_timer.cancel()
                session.timeout_timer = None
            if session.stop_timer is not None:
                session.stop_timer.cancel()
                session.stop_timer = None
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    if os.name == "nt" and isinstance(getattr(process, "pid", None), int):
                        subprocess.run(
                            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                            check=False,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=5,
                        )
                    elif isinstance(getattr(process, "pid", None), int):
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                    process.wait(timeout=1)
                except (OSError, subprocess.SubprocessError):
                    pass
            except OSError:
                pass
            self._cleanup_profile_locked(session)
            if self._active_session_id == session.session_id:
                self._active_session_id = None
            return
        if session.stop_timer is None:
            timer = threading.Timer(4, self._force_stop_session, args=(session.session_id,))
            timer.daemon = True
            session.stop_timer = timer
            timer.start()

    def _force_stop_session(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return
            session.stop_timer = None
            self._request_stop_locked(session, force=True)

    def _fail_locked(self, session: BrowserLoginSession, code: str) -> None:
        session.status = "failed"
        session.error_code = code
        session.error_message = SAFE_ERROR_MESSAGES.get(
            code,
            SAFE_ERROR_MESSAGES["browser_launch_failed"],
        )

    def _active_session_locked(self) -> BrowserLoginSession | None:
        if self._active_session_id is None:
            return None
        session = self._sessions.get(self._active_session_id)
        if session is None:
            self._active_session_id = None
            return None
        if session.process is not None and session.process.poll() is None:
            return session
        if session.status in ACTIVE_SESSION_STATUSES:
            return session
        self._active_session_id = None
        return None

    def _require_session_locked(self, session_id: str) -> BrowserLoginSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise BrowserSessionNotFoundError(session_id)
        return session

    def _cleanup_profile_locked(self, session: BrowserLoginSession) -> None:
        if session.temp_profile_dir is None:
            return
        shutil.rmtree(session.temp_profile_dir, ignore_errors=True)
        session.temp_profile_dir = None

    def _trim_sessions_locked(self) -> None:
        if len(self._sessions) <= 20:
            return
        removable = [
            session_id
            for session_id, session in self._sessions.items()
            if session_id != self._active_session_id
            and session.status in TERMINAL_SESSION_STATUSES
        ]
        for session_id in removable[: len(self._sessions) - 20]:
            self._sessions.pop(session_id, None)


_manager: BrowserLoginManager | None = None
_manager_lock = threading.Lock()


def get_browser_login_manager() -> BrowserLoginManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            settings = get_settings()
            _manager = BrowserLoginManager(
                helper_path=settings.weibo_browser_helper_path,
                node_path=settings.weibo_browser_helper_node_path,
                chrome_path=settings.weibo_browser_chrome_path,
                auth_dir=settings.weibo_api_auth_dir,
                timeout_seconds=settings.weibo_browser_session_timeout_seconds,
            )
        return _manager
