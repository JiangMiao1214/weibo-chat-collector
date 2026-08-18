from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import Request
from zoneinfo import ZoneInfo


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.collectors.weibo_api import (  # noqa: E402
    CookieProfileError,
    CookieProfileStore,
    HttpResponse,
    WeiboApiClient,
    WeiboApiCollector,
    WeiboAuthenticationError,
    WeiboGroupError,
    WeiboHttpError,
    WeiboIncompleteCollectionError,
    WeiboRateLimitError,
    WeiboResponseError,
    WeiboTimeoutError,
    _NoRedirectHandler,
    is_weibo_red_packet,
    map_weibo_message,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")


def epoch(hour: int, minute: int = 0) -> int:
    return int(datetime(2026, 1, 2, hour, minute, tzinfo=SHANGHAI).timestamp())


def raw_message(message_id: object, hour: int, *, text: str | None = None) -> dict:
    return {
        "id": message_id,
        "time": epoch(hour),
        "content": text if text is not None else f"message-{message_id}",
        "from_uid": 9007199254740993,
        "from_user": {"screen_name": "测试用户"},
    }


class CookieProfileStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = CookieProfileStore(Path(self.temp_dir.name) / "profiles")

    def test_profiles_are_isolated_filtered_atomic_and_private(self) -> None:
        first_secret = "first-secret-cookie"
        second_secret = "second-secret-cookie"
        first = self.store.save(
            1,
            [
                {"name": "SUB", "value": first_secret, "domain": "weibo.com", "path": "/"},
                {"name": "ALF", "value": "renew", "domain": ".api.weibo.com"},
                {"name": "SINA", "value": "sina", "domain": "login.sina.com.cn"},
                {"name": "EVIL", "value": "bad", "domain": "notweibo.com"},
                {"name": "OTHER", "value": "bad", "domain": "example.com"},
            ],
        )
        second = self.store.save(
            2,
            [{"name": "SUB", "value": second_secret, "domain": ".weibo.com"}],
        )

        self.assertTrue(first.authenticated)
        self.assertEqual(first.cookie_count, 3)
        self.assertEqual(second.cookie_count, 1)
        self.assertNotIn(first_secret, repr(first))
        self.assertNotIn(second_secret, json.dumps(second.to_dict()))
        self.assertIn(first_secret, self.store.cookie_header(1))
        self.assertIn("ALF=renew", self.store.cookie_header(1))
        self.assertNotIn("SINA=sina", self.store.cookie_header(1))
        self.assertNotIn(second_secret, self.store.cookie_header(1))
        self.assertIn(second_secret, self.store.cookie_header(2))

        profile_files = sorted((Path(self.temp_dir.name) / "profiles").glob("*.json"))
        self.assertEqual(len(profile_files), 2)
        for profile_file in profile_files:
            self.assertEqual(os.stat(profile_file).st_mode & 0o777, 0o600)
        stored = json.loads(profile_files[0].read_text(encoding="utf-8"))
        self.assertEqual([cookie["domain"] for cookie in stored], [
            ".weibo.com",
            ".api.weibo.com",
            ".login.sina.com.cn",
        ])

    def test_missing_sub_is_rejected_without_overwriting_existing_profile(self) -> None:
        self.store.save(7, [{"name": "SUB", "value": "keep-me", "domain": ".weibo.com"}])
        before = self.store.cookie_header(7)
        with self.assertRaisesRegex(CookieProfileError, "SUB"):
            self.store.save(7, [{"name": "ALF", "value": "x", "domain": ".weibo.com"}])
        self.assertEqual(self.store.cookie_header(7), before)

    def test_invalid_profile_status_is_redacted(self) -> None:
        profiles = Path(self.temp_dir.name) / "profiles"
        profiles.mkdir()
        (profiles / "account-3.cookies.json").write_text("not-json", encoding="utf-8")
        status = self.store.status(3)
        self.assertTrue(status.exists)
        self.assertFalse(status.authenticated)
        self.assertEqual(status.cookie_count, 0)

    def test_header_injection_and_wrong_domain_sub_are_rejected(self) -> None:
        with self.assertRaisesRegex(CookieProfileError, "SUB"):
            self.store.save(
                8,
                [{"name": "SUB", "value": "secret\r\nX-Leak: yes", "domain": ".weibo.com"}],
            )
        with self.assertRaisesRegex(CookieProfileError, "api.weibo.com"):
            self.store.save(
                8,
                [{"name": "SUB", "value": "sina-only", "domain": ".sina.com.cn"}],
            )


class MapperTests(unittest.TestCase):
    def test_maps_ids_timezone_user_avatar_raw_and_all_attachment_sources(self) -> None:
        raw = {
            "id": 9007199254740993123,
            "time": int(datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc).timestamp()),
            "content": " hello\n world ",
            "type": "mixed",
            "from_uid": 9007199254740993999,
            "from_user": {
                "screen_name": "Alice",
                "avatar_hd": "http://img.example/avatar.jpg",
            },
            "pic_urls": [
                {"url": "http://img.example/a.jpg"},
                {"large": {"url": "https://img.example/b.jpg"}},
            ],
            "pic": "http://img.example/c.jpg",
            "fids": [123456],
            "url_objects": [
                {
                    "url_ori": "http://weibo.com/share/1",
                    "info": {
                        "title": "分享标题",
                        "description": "分享摘要",
                        "video_url": "http://video.example/movie.mp4",
                    },
                    "status": {"pic_ids": ["pic-a"]},
                }
            ],
            "opaque_future_field": {"must": ["survive"]},
        }
        candidate = map_weibo_message(raw, 11, 22, "Asia/Shanghai")

        self.assertEqual(candidate.source_message_id, "9007199254740993123")
        self.assertEqual(candidate.source_user_id, "9007199254740993999")
        self.assertEqual(candidate.sender_name, "Alice")
        self.assertEqual(candidate.sender_avatar_url, "https://img.example/avatar.jpg")
        self.assertEqual(candidate.avatar_url, candidate.sender_avatar_url)
        self.assertEqual(candidate.sent_at.isoformat(), "2026-01-02T11:04:05+08:00")
        self.assertEqual(candidate.content_text, "hello world")
        self.assertEqual(candidate.message_type, "video")
        self.assertEqual(candidate.raw_payload, raw)
        self.assertIsNot(candidate.raw_payload, raw)

        by_type = {}
        for attachment in candidate.attachments:
            by_type.setdefault(attachment.attachment_type, []).append(attachment.source_url)
        self.assertEqual(
            by_type["image"],
            [
                "https://img.example/a.jpg",
                "https://img.example/b.jpg",
                "https://img.example/c.jpg",
                "https://upload.api.weibo.com/2/mss/msget?source=209678993&fid=123456",
                "https://wx1.sinaimg.cn/large/pic-a.jpg",
            ],
        )
        self.assertEqual(by_type["link"], ["https://weibo.com/share/1"])
        self.assertEqual(by_type["video"], ["https://video.example/movie.mp4"])
        link = next(item for item in candidate.attachments if item.attachment_type == "link")
        self.assertEqual(link.title, "分享标题")
        self.assertEqual(link.description, "分享摘要")

    def test_created_at_timezone_and_red_packet_detection(self) -> None:
        candidate = map_weibo_message(
            {
                "id_str": "00123",
                "created_at": "Fri, 02 Jan 2026 03:04:05 GMT",
                "content": "小明领取了你的红包",
                "msg_type": "notice",
            },
            1,
            2,
        )
        self.assertEqual(candidate.source_message_id, "00123")
        self.assertEqual(candidate.sent_at.hour, 11)
        self.assertTrue(candidate.is_red_packet)
        self.assertEqual(candidate.message_type, "system")
        self.assertTrue(is_weibo_red_packet({"type": "hongbao", "content": "anything"}))
        self.assertFalse(is_weibo_red_packet({"type": "text", "content": "今晚会发红包吗"}))
        self.assertFalse(
            is_weibo_red_packet({"type": "text", "content": "你领取了昨天的红包吗？"})
        )


class FakePageClient:
    def __init__(self, pages: dict[str, dict]):
        self.pages = pages
        self.calls: list[tuple[int, str, str, int]] = []

    def query_messages(
        self,
        account_id: int,
        source_group_id: str | int,
        *,
        max_mid: str | int,
        count: int,
    ) -> dict:
        cursor = str(max_mid)
        self.calls.append((account_id, str(source_group_id), cursor, count))
        return self.pages[cursor]


class CollectorTests(unittest.TestCase):
    def test_pages_backwards_deduplicates_and_filters_both_boundaries(self) -> None:
        client = FakePageClient(
            {
                # newest-first page
                "0": {"messages": [raw_message("6", 6), raw_message("5", 5)]},
                # cursor overlap, still newest-first
                "5": {"data": {"messages": [
                    raw_message("5", 5),
                    raw_message("4", 4),
                    raw_message("3", 3),
                ]}},
                # oldest-first page demonstrates both orders are accepted
                "3": {"list": [raw_message("1", 1), raw_message("2", 2), raw_message("3", 3)]},
            }
        )
        collector = WeiboApiCollector(client, "source-group", page_size=3, max_pages=10)
        collector.page_count = 999
        collector.raw_message_count = 999
        collector.total_seen_count = 999
        result = collector.collect(
            12,
            34,
            datetime(2026, 1, 2, 2, tzinfo=SHANGHAI),
            datetime(2026, 1, 2, 5, tzinfo=SHANGHAI),
        )

        self.assertEqual([item.source_message_id for item in result], ["2", "3", "4", "5"])
        self.assertEqual([call[2] for call in client.calls], ["0", "5", "3"])
        self.assertTrue(all(item.account_id == 12 and item.group_id == 34 for item in result))
        self.assertEqual(collector.page_count, 3)
        self.assertEqual(collector.raw_message_count, 8)
        self.assertEqual(collector.total_seen_count, 6)

    def test_repeated_cursor_before_start_is_reported_incomplete(self) -> None:
        repeated = {"messages": [raw_message("10", 10), raw_message("9", 9)]}
        client = FakePageClient({"0": repeated, "9": repeated})
        collector = WeiboApiCollector(client, 88, max_pages=50)
        with self.assertRaisesRegex(WeiboIncompleteCollectionError, "repeated"):
            collector.collect(
                1,
                2,
                datetime(2026, 1, 1, tzinfo=SHANGHAI),
                datetime(2026, 1, 3, tzinfo=SHANGHAI),
            )
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(collector.page_count, 2)
        self.assertEqual(collector.raw_message_count, 4)
        self.assertEqual(collector.total_seen_count, 2)

    def test_empty_page_terminates(self) -> None:
        client = FakePageClient({"0": {"data": []}})
        collector = WeiboApiCollector(client, 88)
        self.assertEqual(
            collector.collect(
                1,
                2,
                datetime(2020, 1, 1),
                datetime(2026, 1, 3),
            ),
            [],
        )

    def test_page_budget_before_start_raises_incomplete(self) -> None:
        client = FakePageClient({"0": {"messages": [raw_message("10", 10), raw_message("9", 9)]}})
        collector = WeiboApiCollector(client, 88, max_pages=1)
        with self.assertRaises(WeiboIncompleteCollectionError):
            collector.collect(
                1,
                2,
                datetime(2026, 1, 2, 1, tzinfo=SHANGHAI),
                datetime(2026, 1, 2, 12, tzinfo=SHANGHAI),
            )

    def test_non_monotonic_page_is_rejected(self) -> None:
        client = FakePageClient(
            {"0": {"messages": [raw_message("1", 1), raw_message("3", 3), raw_message("2", 2)]}}
        )
        collector = WeiboApiCollector(client, 88)
        with self.assertRaisesRegex(WeiboResponseError, "neither"):
            collector.collect(
                1,
                2,
                datetime(2026, 1, 1, tzinfo=SHANGHAI),
                datetime(2026, 1, 3, tzinfo=SHANGHAI),
            )

    def test_missing_message_container_is_not_treated_as_history_end(self) -> None:
        client = FakePageClient({"0": {"ok": 0, "error": "risk-control"}})
        collector = WeiboApiCollector(client, 88)
        with self.assertRaisesRegex(WeiboResponseError, "no recognized messages array"):
            collector.collect(
                1,
                2,
                datetime(2026, 1, 1, tzinfo=SHANGHAI),
                datetime(2026, 1, 3, tzinfo=SHANGHAI),
            )


class ClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = CookieProfileStore(self.temp_dir.name)
        self.store.save(4, [{"name": "SUB", "value": "private-sub", "domain": ".weibo.com"}])

    def test_request_matches_observed_auto_parameters_and_headers(self) -> None:
        requests = []

        def transport(request, timeout):
            requests.append((request, timeout))
            return HttpResponse(200, {}, '{"messages": []}')

        client = WeiboApiClient(self.store, transport=transport, clock=lambda: 1234.567)
        self.assertEqual(client.query_messages(4, "group-9", max_mid="88", count=37), {"messages": []})
        request, timeout = requests[0]
        query = parse_qs(urlparse(request.full_url).query)
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(query, {
            "convert_emoji": ["1"],
            "query_sender": ["1"],
            "count": ["37"],
            "id": ["group-9"],
            "max_mid": ["88"],
            "source": ["209678993"],
            "t": ["1234567"],
        })
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual(headers["cookie"], "SUB=private-sub")
        self.assertEqual(headers["referer"], "https://api.weibo.com/chat")
        self.assertEqual(headers["x-requested-with"], "XMLHttpRequest")
        self.assertEqual(timeout, 30.0)

    def test_default_transport_redirect_handler_never_creates_followup_request(self) -> None:
        original = Request(
            "https://api.weibo.com/webim/groupchat/query_messages.json",
            headers={"Cookie": "SUB=must-not-be-forwarded"},
        )
        redirected = _NoRedirectHandler().redirect_request(
            original,
            None,
            302,
            "Found",
            {"Location": "https://attacker.example/collect"},
            "https://attacker.example/collect",
        )
        self.assertIsNone(redirected)

    def test_auth_and_group_business_errors_are_classified_without_retry(self) -> None:
        for status, code, exception in (
            (200, 21301, WeiboAuthenticationError),
            (200, "21201", WeiboGroupError),
            (401, 21301, WeiboAuthenticationError),
        ):
            with self.subTest(status=status, code=code):
                calls = []

                def transport(request, timeout, response_code=code, response_status=status):
                    calls.append(request)
                    return HttpResponse(
                        response_status,
                        {},
                        json.dumps({"error_code": response_code, "error": "x"}),
                    )

                client = WeiboApiClient(self.store, transport=transport)
                with self.assertRaises(exception):
                    client.query_messages(4, 9)
                self.assertEqual(len(calls), 1)

    def test_429_5xx_and_timeout_each_stop_after_one_request(self) -> None:
        for response, exception in (
            (HttpResponse(429, {"Retry-After": "2"}, "busy"), WeiboRateLimitError),
            (HttpResponse(503, {}, "down"), WeiboHttpError),
            (socket.timeout("slow"), WeiboTimeoutError),
        ):
            with self.subTest(exception=exception.__name__):
                calls = []

                def transport(request, timeout, configured=response):
                    calls.append(request)
                    if isinstance(configured, BaseException):
                        raise configured
                    return configured

                client = WeiboApiClient(self.store, transport=transport)
                with self.assertRaises(exception):
                    client.query_messages(4, 9)
                self.assertEqual(len(calls), 1)

    def test_risk_business_codes_survive_http_error_status(self) -> None:
        for status, code in (
            (200, 10023),
            (403, 10023),
            (403, "10024"),
            (429, 99999),
        ):
            with self.subTest(status=status, code=code):
                client = WeiboApiClient(
                    self.store,
                    transport=lambda request, timeout, response_status=status, response_code=code: HttpResponse(
                        response_status,
                        {},
                        json.dumps({"error_code": response_code, "error": "risk"}),
                    ),
                )
                with self.assertRaises(WeiboResponseError) as raised:
                    client.query_messages(4, 9)
                self.assertEqual(str(raised.exception.error_code), str(code))
                self.assertEqual(
                    raised.exception.http_status,
                    status if status != 200 else None,
                )

    def test_invalid_json_and_unknown_business_error_are_classified(self) -> None:
        client = WeiboApiClient(
            self.store,
            transport=lambda request, timeout: HttpResponse(200, {}, "not json"),
        )
        with self.assertRaisesRegex(WeiboResponseError, "invalid JSON"):
            client.query_messages(4, 9)

        client = WeiboApiClient(
            self.store,
            transport=lambda request, timeout: HttpResponse(200, {}, '{"error_code": 99999}'),
        )
        with self.assertRaises(WeiboResponseError) as raised:
            client.query_messages(4, 9)
        self.assertEqual(raised.exception.error_code, 99999)

        client = WeiboApiClient(
            self.store,
            transport=lambda request, timeout: HttpResponse(
                200,
                {},
                '{"ok": 0, "error": "new failure shape"}',
            ),
        )
        with self.assertRaisesRegex(WeiboResponseError, "indicated failure"):
            client.query_messages(4, 9)


if __name__ == "__main__":
    unittest.main()
