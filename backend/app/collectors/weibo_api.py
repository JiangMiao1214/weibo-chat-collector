"""Authenticated Weibo group-chat collection using the webim JSON endpoint.

The endpoint used here is the same one observed by ``weibo-chat-auto``.  This
module deliberately owns no browser automation: callers import a Puppeteer
cookie array into :class:`CookieProfileStore`, then select the matching account
when collecting.  Credentials never appear in status objects or exceptions.
"""

from __future__ import annotations

import copy
import json
import os
import socket
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

from ..services.message_filter import classify_message_filter
from .base import AttachmentCandidate, CollectionCandidate


WEIBO_API_URL = "https://api.weibo.com/webim/groupchat/query_messages.json"
WEIBO_API_HOST = "api.weibo.com"
WEIBO_API_PATH = "/webim/groupchat/query_messages.json"
WEIBO_SOURCE = "209678993"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36"
)
_ALLOWED_COOKIE_DOMAINS = ("weibo.com", "sina.com.cn")
_COOKIE_NAME_SEPARATORS = frozenset('()<>@,;:\\"/[]?={} \t')


class CookieProfileError(ValueError):
    """The account cookie profile is missing or invalid."""


@dataclass(frozen=True, slots=True)
class CookieProfileStatus:
    """Non-sensitive information safe to return from an API or write to logs."""

    account_id: int
    exists: bool
    authenticated: bool
    cookie_count: int
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "exists": self.exists,
            "authenticated": self.authenticated,
            "cookie_count": self.cookie_count,
            "updated_at": self.updated_at,
        }


class CookieProfileStore:
    """Filesystem-backed, per-account Puppeteer cookie profiles.

    Every account is stored in a different file.  Writes use a temporary file,
    ``fsync`` and ``os.replace`` so an interrupted refresh cannot destroy the
    last usable login profile.
    """

    def __init__(self, root_dir: str | Path):
        self.root_dir = Path(root_dir)

    @staticmethod
    def _validate_account_id(account_id: int) -> int:
        if isinstance(account_id, bool) or not isinstance(account_id, int) or account_id < 1:
            raise CookieProfileError("account_id must be a positive integer")
        return account_id

    def _path(self, account_id: int) -> Path:
        account_id = self._validate_account_id(account_id)
        return self.root_dir / f"account-{account_id}.cookies.json"

    @staticmethod
    def _is_weibo_domain(domain: Any) -> bool:
        if not isinstance(domain, str):
            return False
        canonical = domain.strip().lower().lstrip(".").rstrip(".")
        return any(
            canonical == allowed or canonical.endswith(f".{allowed}")
            for allowed in _ALLOWED_COOKIE_DOMAINS
        )

    @classmethod
    def filter_weibo_cookies(cls, cookies: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        if isinstance(cookies, (str, bytes, bytearray)) or not isinstance(cookies, Sequence):
            raise CookieProfileError("cookies must be a Puppeteer cookie array")

        filtered: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for raw_cookie in cookies:
            if not isinstance(raw_cookie, Mapping):
                continue
            if not cls._is_weibo_domain(raw_cookie.get("domain")):
                continue
            name = raw_cookie.get("name")
            value = raw_cookie.get("value")
            if not isinstance(name, str) or not name or not isinstance(value, str):
                continue
            if any(ord(character) < 0x20 or ord(character) == 0x7F for character in name):
                continue
            if any(character in _COOKIE_NAME_SEPARATORS for character in name):
                continue
            if any(
                ord(character) < 0x20 or ord(character) == 0x7F or character == ";"
                for character in value
            ):
                continue

            cookie = copy.deepcopy(dict(raw_cookie))
            canonical_domain = str(cookie["domain"]).strip().lower().lstrip(".").rstrip(".")
            cookie["domain"] = f".{canonical_domain}"
            key = (cookie["domain"], name)
            if key in seen:
                continue
            seen.add(key)
            filtered.append(cookie)
        return filtered

    @staticmethod
    def _has_sub(cookies: Sequence[Mapping[str, Any]]) -> bool:
        return any(
            cookie.get("name") == "SUB"
            and isinstance(cookie.get("value"), str)
            and bool(cookie["value"])
            for cookie in cookies
        )

    @classmethod
    def _cookies_for_request(
        cls,
        cookies: Sequence[Mapping[str, Any]],
        *,
        host: str = WEIBO_API_HOST,
        request_path: str = WEIBO_API_PATH,
        secure: bool = True,
        now: float | None = None,
    ) -> list[dict[str, Any]]:
        """Apply browser-like domain, path, secure and expiry scoping."""

        current_time = time.time() if now is None else now
        selected: list[dict[str, Any]] = []
        for cookie in cookies:
            domain = str(cookie.get("domain") or "").lower().lstrip(".").rstrip(".")
            if host != domain and not host.endswith(f".{domain}"):
                continue

            cookie_path = str(cookie.get("path") or "/")
            if not cookie_path.startswith("/"):
                cookie_path = "/"
            path_matches = request_path == cookie_path or (
                request_path.startswith(cookie_path)
                and (cookie_path.endswith("/") or request_path[len(cookie_path) :].startswith("/"))
            )
            if not path_matches:
                continue
            if cookie.get("secure") is True and not secure:
                continue

            expires = cookie.get("expires")
            if isinstance(expires, (int, float)) and not isinstance(expires, bool):
                if expires > 0 and float(expires) <= current_time:
                    continue
            selected.append(dict(cookie))
        return selected

    def save(
        self,
        account_id: int,
        cookies: Sequence[Mapping[str, Any]],
    ) -> CookieProfileStatus:
        """Validate and atomically replace one account's cookie profile."""

        account_id = self._validate_account_id(account_id)
        filtered = self.filter_weibo_cookies(cookies)
        if not self._has_sub(self._cookies_for_request(filtered)):
            raise CookieProfileError(
                "cookie profile has no usable SUB login cookie for api.weibo.com"
            )

        try:
            serialized = json.dumps(filtered, ensure_ascii=False, indent=2).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise CookieProfileError("cookie profile is not valid JSON data") from error

        self.root_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "posix":
            os.chmod(self.root_dir, 0o700)

        target = self._path(account_id)
        temporary_path: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{target.name}.", suffix=".tmp", dir=self.root_dir
            )
            temporary_path = Path(temporary_name)
            try:
                if os.name == "posix":
                    os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "wb") as handle:
                    descriptor = -1
                    handle.write(serialized)
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
            os.replace(temporary_path, target)
            temporary_path = None
            if os.name == "posix":
                os.chmod(target, 0o600)
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass

        return self.status(account_id)

    # Explicit alias used by setup/import flows.
    save_profile = save

    def _load(self, account_id: int) -> list[dict[str, Any]]:
        path = self._path(account_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise CookieProfileError("cookie profile is not configured for this account") from error
        except (OSError, json.JSONDecodeError) as error:
            raise CookieProfileError("cookie profile cannot be read") from error

        if not isinstance(payload, list):
            raise CookieProfileError("cookie profile has an invalid format")
        filtered = self.filter_weibo_cookies(payload)
        if not self._has_sub(self._cookies_for_request(filtered)):
            raise CookieProfileError(
                "cookie profile has no usable SUB login cookie for api.weibo.com"
            )
        return filtered

    def cookie_header(self, account_id: int) -> str:
        """Build the private request header without exposing it in status output."""

        cookies = self._cookies_for_request(self._load(account_id))
        return "; ".join(f"{cookie['name']}={cookie['value']}" for cookie in cookies)

    def status(self, account_id: int) -> CookieProfileStatus:
        account_id = self._validate_account_id(account_id)
        path = self._path(account_id)
        if not path.is_file():
            return CookieProfileStatus(account_id, False, False, 0, None)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            cookies = self.filter_weibo_cookies(payload) if isinstance(payload, list) else []
        except (OSError, json.JSONDecodeError, CookieProfileError):
            cookies = []
        try:
            updated_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
        except OSError:
            updated_at = None
        return CookieProfileStatus(
            account_id=account_id,
            exists=True,
            authenticated=self._has_sub(self._cookies_for_request(cookies)),
            cookie_count=len(cookies),
            updated_at=updated_at,
        )


class WeiboApiError(RuntimeError):
    """Base class for sanitized Weibo API failures."""


class WeiboAuthenticationError(WeiboApiError):
    """The account cookie is absent or expired (Weibo error 21301)."""

    def __init__(self, message: str, *, http_status: int | None = None):
        self.http_status = http_status
        super().__init__(message)


class WeiboGroupError(WeiboApiError):
    """The group is missing or inaccessible (Weibo error 21201)."""

    def __init__(self, message: str, *, http_status: int | None = None):
        self.http_status = http_status
        super().__init__(message)


class WeiboRateLimitError(WeiboApiError):
    """The endpoint rejected the single request with HTTP 429."""

    status = 429


class WeiboHttpError(WeiboApiError):
    """A non-success HTTP status was returned."""

    def __init__(self, status: int):
        self.status = status
        super().__init__(f"Weibo API HTTP error {status}")


class WeiboTimeoutError(WeiboApiError):
    """The single request timed out."""


class WeiboTransportError(WeiboApiError):
    """The request failed for a non-timeout transport reason."""


class WeiboResponseError(WeiboApiError):
    """The HTTP response is invalid or contains an unhandled business error."""

    def __init__(
        self,
        message: str,
        *,
        error_code: int | str | None = None,
        http_status: int | None = None,
    ):
        self.error_code = error_code
        self.http_status = http_status
        super().__init__(message)


class WeiboIncompleteCollectionError(WeiboApiError):
    """The configured page budget ended before the requested start boundary."""


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes | str


Transport = Callable[[Request, float], HttpResponse]


class _NoRedirectHandler(HTTPRedirectHandler):
    """Never forward the explicit Cookie header to a redirect target."""

    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def _urllib_transport(request: Request, timeout: float) -> HttpResponse:
    opener = build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:  # noqa: S310 - fixed URL
            return HttpResponse(
                status=int(response.status),
                headers=dict(response.headers.items()),
                body=response.read(),
            )
    except HTTPError as error:
        return HttpResponse(
            status=int(error.code),
            headers=dict(error.headers.items()) if error.headers else {},
            body=error.read(),
        )


class WeiboApiClient:
    """Small injectable HTTP client for the observed webim endpoint."""

    def __init__(
        self,
        cookie_store: CookieProfileStore,
        *,
        transport: Transport | None = None,
        timeout: float = 30.0,
        clock: Callable[[], float] | None = None,
    ):
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.cookie_store = cookie_store
        self.transport = transport or _urllib_transport
        self.timeout = float(timeout)
        self.clock = clock or time.time

    def query_messages(
        self,
        account_id: int,
        source_group_id: str | int,
        *,
        max_mid: str | int = "0",
        count: int = 20,
    ) -> dict[str, Any]:
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ValueError("count must be a positive integer")
        if source_group_id is None or str(source_group_id).strip() == "":
            raise ValueError("source_group_id is required")

        parameters = {
            "convert_emoji": "1",
            "query_sender": "1",
            "count": str(count),
            "id": str(source_group_id),
            "max_mid": str(max_mid),
            "source": WEIBO_SOURCE,
            "t": str(int(self.clock() * 1000)),
        }
        request = Request(
            f"{WEIBO_API_URL}?{urlencode(parameters)}",
            headers={
                "Cookie": self.cookie_store.cookie_header(account_id),
                "User-Agent": DEFAULT_USER_AGENT,
                "Referer": "https://api.weibo.com/chat",
                "X-Requested-With": "XMLHttpRequest",
            },
            method="GET",
        )

        try:
            response = self.transport(request, self.timeout)
        except (TimeoutError, socket.timeout) as error:
            raise WeiboTimeoutError("Weibo API request timed out") from error
        except URLError as error:
            if isinstance(error.reason, (TimeoutError, socket.timeout)):
                raise WeiboTimeoutError("Weibo API request timed out") from error
            raise WeiboTransportError("Weibo API transport failed") from error

        status = int(response.status)
        if not 200 <= status <= 299:
            # Weibo commonly uses HTTP 200 for business errors, but retain
            # useful classifications when a gateway keeps the JSON body.
            try:
                error_payload = self._decode_json(response.body)
            except WeiboResponseError:
                error_payload = {}
            normalized_code = self._normalize_error_code(error_payload.get("error_code"))
            if normalized_code == 21301:
                raise WeiboAuthenticationError(
                    "Weibo account authentication is invalid (21301)",
                    http_status=status,
                )
            if normalized_code == 21201:
                raise WeiboGroupError(
                    "Weibo group is missing or inaccessible (21201)",
                    http_status=status,
                )
            if normalized_code not in (None, 0):
                raise WeiboResponseError(
                    f"Weibo API business error {normalized_code}",
                    error_code=normalized_code,
                    http_status=status,
                )
            if status == 429:
                raise WeiboRateLimitError("Weibo API rate limited the request (HTTP 429)")
            raise WeiboHttpError(status)

        payload = self._decode_json(response.body)
        normalized_code = self._normalize_error_code(payload.get("error_code"))
        if normalized_code == 21301:
            raise WeiboAuthenticationError("Weibo account authentication is invalid (21301)")
        if normalized_code == 21201:
            raise WeiboGroupError("Weibo group is missing or inaccessible (21201)")
        if normalized_code not in (None, 0):
            raise WeiboResponseError(
                f"Weibo API business error {normalized_code}", error_code=normalized_code
            )
        if payload.get("ok") in (False, 0, "0") or payload.get("error") not in (
            None,
            "",
            False,
        ):
            raise WeiboResponseError(
                "Weibo API indicated failure without a recognized error code"
            )
        return payload

    # Naming alias for consumers that think in pages rather than endpoints.
    fetch_messages = query_messages

    @staticmethod
    def _decode_json(body: bytes | str) -> dict[str, Any]:
        try:
            text = body.decode("utf-8") if isinstance(body, bytes) else body
            payload = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as error:
            raise WeiboResponseError("Weibo API returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise WeiboResponseError("Weibo API returned a non-object JSON response")
        return payload

    @staticmethod
    def _normalize_error_code(value: Any) -> int | str | None:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return str(value)

def _target_zone(timezone_name: str | ZoneInfo) -> ZoneInfo:
    return timezone_name if isinstance(timezone_name, ZoneInfo) else ZoneInfo(timezone_name)


def _first_present(mapping: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value is not None and value != "":
            return value
    return None


def _message_id(raw: Mapping[str, Any]) -> str:
    value = _first_present(raw, ("id_str", "id", "mid", "message_id"))
    if value is None or isinstance(value, bool):
        raise WeiboResponseError("Weibo message has no stable ID")
    return str(value)


def _parse_message_datetime(raw: Mapping[str, Any], zone: ZoneInfo) -> datetime:
    value = raw.get("time")
    if value is not None and value != "":
        try:
            timestamp = float(value)
            if timestamp > 0:
                # Webim currently sends epoch seconds, while accepting millis
                # here makes the mapper tolerant of captured browser payloads.
                if timestamp >= 100_000_000_000:
                    timestamp /= 1000
                return datetime.fromtimestamp(timestamp, timezone.utc).astimezone(zone)
        except (TypeError, ValueError, OSError, OverflowError):
            pass

    created_at = raw.get("created_at")
    if created_at is None or created_at == "":
        raise WeiboResponseError("Weibo message has no valid timestamp")
    if isinstance(created_at, (int, float)) and not isinstance(created_at, bool):
        timestamp = float(created_at)
        if timestamp >= 100_000_000_000:
            timestamp /= 1000
        try:
            return datetime.fromtimestamp(timestamp, timezone.utc).astimezone(zone)
        except (OSError, OverflowError, ValueError) as error:
            raise WeiboResponseError("Weibo message has an invalid timestamp") from error

    text = str(created_at).strip()
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            pass
    if parsed is None:
        raise WeiboResponseError("Weibo message has an invalid timestamp")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone)


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\r", " ").replace("\n", " ").split())


def _https_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    return f"https:{value[5:]}" if value.startswith("http:") else value


def _nested_url(value: Any) -> str | None:
    if isinstance(value, str):
        return _https_url(value)
    if not isinstance(value, Mapping):
        return None
    direct = _first_present(value, ("url", "pic", "src"))
    if direct:
        return _https_url(direct)
    large = value.get("large")
    if isinstance(large, Mapping):
        return _https_url(_first_present(large, ("url", "src")))
    return None


def is_weibo_red_packet(raw: Mapping[str, Any], content_text: str | None = None) -> bool:
    candidate = dict(raw)
    if content_text is not None:
        candidate["content"] = content_text
    return classify_message_filter(candidate) == "red_packet"


def _map_attachments(raw: Mapping[str, Any]) -> list[AttachmentCandidate]:
    attachments: list[AttachmentCandidate] = []
    seen: set[tuple[str, str]] = set()

    def add(
        attachment_type: str,
        url: Any,
        *,
        title: Any = None,
        description: Any = None,
    ) -> None:
        normalized = _https_url(url)
        if not normalized:
            return
        key = (attachment_type, normalized)
        if key in seen:
            return
        seen.add(key)
        attachments.append(
            AttachmentCandidate(
                attachment_type=attachment_type,
                source_url=normalized,
                title=_clean_text(title) or None,
                description=_clean_text(description) or None,
            )
        )

    pic_urls = raw.get("pic_urls")
    if isinstance(pic_urls, Sequence) and not isinstance(pic_urls, (str, bytes, bytearray)):
        for picture in pic_urls:
            add("image", _nested_url(picture))
    add("image", _nested_url(raw.get("pic")))

    fids = raw.get("fids")
    if isinstance(fids, Sequence) and not isinstance(fids, (str, bytes, bytearray)):
        for fid in fids:
            if fid is None or fid == "":
                continue
            add(
                "image",
                f"https://upload.api.weibo.com/2/mss/msget?{urlencode({'source': WEIBO_SOURCE, 'fid': str(fid)})}",
            )

    url_objects = raw.get("url_objects")
    if isinstance(url_objects, Sequence) and not isinstance(
        url_objects, (str, bytes, bytearray)
    ):
        for url_object in url_objects:
            if not isinstance(url_object, Mapping):
                continue
            info = url_object.get("info")
            info = info if isinstance(info, Mapping) else {}
            status = url_object.get("status")
            status = status if isinstance(status, Mapping) else {}

            share_url = _first_present(url_object, ("url_ori", "url")) or _first_present(
                info, ("url_long", "url_short")
            )
            title = _first_present(info, ("title",)) or _clean_text(status.get("text"))[:100]
            add(
                "link",
                share_url,
                title=title,
                description=info.get("description"),
            )

            # Only an explicit video_url is a video.  A short/share URL is not
            # silently reclassified as media.
            add("video", info.get("video_url"), title=title)
            add("video", url_object.get("video_url"), title=title)

            pic_ids = status.get("pic_ids")
            if isinstance(pic_ids, Sequence) and not isinstance(
                pic_ids, (str, bytes, bytearray)
            ):
                for pic_id in pic_ids:
                    if pic_id is not None and pic_id != "":
                        add("image", f"https://wx1.sinaimg.cn/large/{pic_id}.jpg")

    add("link", _first_present(raw, ("url", "short_url")))
    add("video", _first_present(raw, ("video_url", "videoUrl")))
    return attachments


def map_weibo_message(
    raw: Mapping[str, Any],
    account_id: int,
    group_id: int,
    timezone_name: str | ZoneInfo = "Asia/Shanghai",
) -> CollectionCandidate:
    """Map one raw webim object without discarding any source fields."""

    if not isinstance(raw, Mapping):
        raise WeiboResponseError("Weibo message is not an object")
    zone = _target_zone(timezone_name)
    message_id = _message_id(raw)
    sent_at = _parse_message_datetime(raw, zone)

    user = raw.get("from_user") or raw.get("sender")
    user = user if isinstance(user, Mapping) else {}
    source_user = _first_present(raw, ("from_uid", "sender_id")) or _first_present(
        user, ("idstr", "id", "uid")
    )
    source_user_id = None if source_user is None else str(source_user)
    sender_name_value = _first_present(user, ("screen_name", "name")) or source_user_id
    sender_name = str(sender_name_value) if sender_name_value is not None else "未知用户"
    avatar_url = _https_url(
        _first_present(user, ("avatar_hd", "avatar_large", "profile_image_url", "avatar_url"))
    )

    content_text = _clean_text(_first_present(raw, ("content", "text", "message", "body")))
    source_type_value = _first_present(raw, ("type", "msg_type", "message_type", "media_type"))
    normalized_source_type = str(source_type_value or "").strip().lower()
    attachments = _map_attachments(raw)
    is_system_message = raw.get("is_system_message") is True or normalized_source_type in {
        "system",
        "notice",
        "system_message",
    }
    attachment_types = {attachment.attachment_type for attachment in attachments}
    if is_system_message:
        message_type = "system"
    elif "video" in attachment_types:
        message_type = "video"
    elif "link" in attachment_types:
        message_type = "link"
    elif "image" in attachment_types:
        message_type = "image"
    else:
        message_type = "text"

    return CollectionCandidate(
        account_id=account_id,
        group_id=group_id,
        source_user_id=source_user_id,
        sender_name=sender_name,
        sender_avatar_url=avatar_url,
        sent_at=sent_at,
        message_type=message_type,
        content_text=content_text,
        source_message_id=message_id,
        raw_payload=copy.deepcopy(dict(raw)),
        is_red_packet=is_weibo_red_packet(raw, content_text),
        is_system_message=is_system_message,
        attachments=attachments,
    )


def _extract_messages(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    missing = object()
    candidates: Any = payload.get("messages", missing)
    if candidates is missing and "data" in payload:
        data = payload["data"]
        if isinstance(data, Mapping):
            candidates = data.get("messages", missing)
        elif isinstance(data, list):
            candidates = data
        else:
            candidates = data
    if candidates is missing:
        candidates = payload.get("list", missing)
    if candidates is missing:
        raise WeiboResponseError("Weibo API response has no recognized messages array")
    if not isinstance(candidates, list):
        raise WeiboResponseError("Weibo API messages field is not an array")
    if not all(isinstance(message, Mapping) for message in candidates):
        raise WeiboResponseError("Weibo API message array contains a non-object")
    return candidates


def _coerce_range(value: datetime, zone: ZoneInfo, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None:
        return value.replace(tzinfo=zone)
    return value.astimezone(zone)


def _id_sort_key(value: str | None) -> tuple[int, int | str]:
    if value is not None:
        try:
            return (0, int(value))
        except ValueError:
            return (1, value)
    return (1, "")


@dataclass(frozen=True, slots=True)
class WeiboCollectionPage:
    """One validated API page and the cursor that may be committed with it."""

    request_max_mid: str
    next_max_mid: str
    candidates: tuple[CollectionCandidate, ...]
    raw_count: int
    newest_sent_at: datetime | None
    oldest_sent_at: datetime | None
    reached_range_start: bool
    terminal: bool


class WeiboApiCollector:
    """Page from newest history toward the requested start, then filter exactly."""

    collector_type = "weibo_api"

    def __init__(
        self,
        client: WeiboApiClient,
        source_group_id: str | int,
        *,
        page_size: int = 20,
        max_pages: int = 500,
        timezone_name: str | ZoneInfo = "Asia/Shanghai",
        inter_page_delay: float = 0.0,
        sleep: Callable[[float], None] | None = None,
    ):
        if page_size < 1:
            raise ValueError("page_size must be at least 1")
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        if inter_page_delay < 0:
            raise ValueError("inter_page_delay cannot be negative")
        if source_group_id is None or str(source_group_id).strip() == "":
            raise ValueError("source_group_id is required")
        self.client = client
        self.source_group_id = source_group_id
        self.page_size = page_size
        self.max_pages = max_pages
        self.zone = _target_zone(timezone_name)
        self.inter_page_delay = inter_page_delay
        self.sleep = sleep or time.sleep
        self.page_count = 0
        self.raw_message_count = 0
        self.total_seen_count = 0

    def fetch_page(
        self,
        account_id: int,
        group_id: int,
        *,
        max_mid: str | int,
        range_start: datetime,
        previous_oldest_at: datetime | None = None,
    ) -> WeiboCollectionPage:
        """Fetch and validate exactly one page without sleeping or persisting it."""

        start = _coerce_range(range_start, self.zone, "range_start")
        request_cursor = str(max_mid)
        payload = self.client.query_messages(
            account_id,
            self.source_group_id,
            max_mid=request_cursor,
            count=self.page_size,
        )
        raw_messages = _extract_messages(payload)
        self.page_count += 1
        self.raw_message_count += len(raw_messages)
        if not raw_messages:
            return WeiboCollectionPage(
                request_max_mid=request_cursor,
                next_max_mid=request_cursor,
                candidates=(),
                raw_count=0,
                newest_sent_at=None,
                oldest_sent_at=None,
                reached_range_start=True,
                terminal=True,
            )

        candidates = tuple(
            map_weibo_message(raw, account_id, group_id, self.zone)
            for raw in raw_messages
        )
        self._validate_page_order(candidates)
        oldest = min(
            candidates,
            key=lambda item: (item.sent_at, _id_sort_key(item.source_message_id)),
        )
        newest = max(
            candidates,
            key=lambda item: (item.sent_at, _id_sort_key(item.source_message_id)),
        )
        next_cursor = oldest.source_message_id
        if next_cursor is None:
            raise WeiboResponseError("oldest Weibo message has no cursor ID")
        if next_cursor == request_cursor:
            raise WeiboIncompleteCollectionError(
                "Weibo pagination repeated the current cursor"
            )
        if previous_oldest_at is not None:
            previous = _coerce_range(previous_oldest_at, self.zone, "previous_oldest_at")
            if oldest.sent_at > previous:
                raise WeiboResponseError(
                    "Weibo pagination moved forward instead of toward older messages"
                )

        return WeiboCollectionPage(
            request_max_mid=request_cursor,
            next_max_mid=next_cursor,
            candidates=candidates,
            raw_count=len(raw_messages),
            newest_sent_at=newest.sent_at,
            oldest_sent_at=oldest.sent_at,
            # Equality deliberately continues for same-second boundary messages.
            reached_range_start=oldest.sent_at < start,
            terminal=False,
        )

    def collect(
        self,
        account_id: int,
        group_id: int,
        range_start: datetime,
        range_end: datetime,
    ) -> list[CollectionCandidate]:
        # Public run metrics are reset for every invocation. raw_message_count
        # includes API overlap; total_seen_count is unique IDs before range
        # filtering and is therefore the useful ingestion/job metric.
        self.page_count = 0
        self.raw_message_count = 0
        self.total_seen_count = 0
        start = _coerce_range(range_start, self.zone, "range_start")
        end = _coerce_range(range_end, self.zone, "range_end")
        if start > end:
            raise ValueError("range_start must not be after range_end")

        results: list[CollectionCandidate] = []
        seen_message_ids: set[str] = set()
        seen_cursors: set[str] = {"0"}
        max_mid = "0"
        previous_oldest: datetime | None = None
        covered_start = False
        terminal_page = False

        for page_index in range(self.max_pages):
            page = self.fetch_page(
                account_id,
                group_id,
                max_mid=max_mid,
                range_start=start,
                previous_oldest_at=previous_oldest,
            )
            if page.terminal:
                terminal_page = True
                break

            for candidate in page.candidates:
                message_id = candidate.source_message_id
                if message_id is None or message_id in seen_message_ids:
                    continue
                seen_message_ids.add(message_id)
                self.total_seen_count += 1
                if start <= candidate.sent_at <= end:
                    results.append(candidate)

            # Continue beyond equality: a following page can contain additional
            # messages with the exact same second as range_start.
            if page.reached_range_start:
                covered_start = True
                break

            if page.next_max_mid in seen_cursors:
                raise WeiboIncompleteCollectionError(
                    "Weibo pagination repeated a cursor before range_start was covered"
                )
            seen_cursors.add(page.next_max_mid)
            max_mid = page.next_max_mid
            previous_oldest = page.oldest_sent_at

            if page_index + 1 < self.max_pages and self.inter_page_delay:
                self.sleep(self.inter_page_delay)

        if not covered_start and not terminal_page:
            raise WeiboIncompleteCollectionError(
                f"collection stopped at max_pages={self.max_pages} before range_start"
            )

        results.sort(key=lambda item: (item.sent_at, _id_sort_key(item.source_message_id)))
        return results

    @staticmethod
    def _validate_page_order(page: Sequence[CollectionCandidate]) -> None:
        timestamps = [candidate.sent_at for candidate in page]
        ascending = all(left <= right for left, right in zip(timestamps, timestamps[1:]))
        descending = all(left >= right for left, right in zip(timestamps, timestamps[1:]))
        if not ascending and not descending:
            raise WeiboResponseError(
                "Weibo API page is neither oldest-first nor newest-first"
            )


__all__ = [
    "CookieProfileError",
    "CookieProfileStatus",
    "CookieProfileStore",
    "DEFAULT_USER_AGENT",
    "HttpResponse",
    "WEIBO_API_URL",
    "WEIBO_SOURCE",
    "WeiboApiClient",
    "WeiboApiCollector",
    "WeiboCollectionPage",
    "WeiboApiError",
    "WeiboAuthenticationError",
    "WeiboGroupError",
    "WeiboHttpError",
    "WeiboIncompleteCollectionError",
    "WeiboRateLimitError",
    "WeiboResponseError",
    "WeiboTimeoutError",
    "WeiboTransportError",
    "is_weibo_red_packet",
    "map_weibo_message",
]
