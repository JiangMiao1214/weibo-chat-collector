from __future__ import annotations

import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.message_filter import classify_message_filter  # noqa: E402
from app.importer import classify_import_filter  # noqa: E402


class MessageFilterTests(unittest.TestCase):
    def test_recognizes_structured_red_packet_signals(self) -> None:
        red_packet_messages = [
            {"is_redenvelope": 1, "content": "任意文案"},
            {"is_redenvelope": "1"},
            {"is_red_packet": True},
            {"type": "hongbao"},
            {"msg_type": "RED_PACKET"},
            {"message_type": "weibo_red_packet"},
            {"icon": "https://example.test/redenvelope_autohint.png"},
        ]

        for message in red_packet_messages:
            with self.subTest(message=message):
                self.assertEqual(classify_message_filter(message), "red_packet")

    def test_import_adapter_uses_embedded_raw_payload_for_all_filter_reasons(self) -> None:
        self.assertEqual(
            classify_import_filter(
                {
                    "content_text": "normalized text",
                    "raw_payload": {
                        "template": "恭喜{{nick.DATA}}今日获得“{{title.DATA}}”标识"
                    },
                }
            ),
            "fansgroup_badge",
        )
        self.assertEqual(
            classify_import_filter(
                {
                    "content_text": "收到红包消息，请在手机上查看",
                    "raw_payload": "{}",
                }
            ),
            "red_packet",
        )
        self.assertIsNone(classify_import_filter({"content_text": "早上好"}))

    def test_recognizes_red_packet_content_and_templates(self) -> None:
        red_packet_messages = [
            {"content": "[红包]"},
            {"content": "[红包] 请在手机上查看"},
            {"content": "收到红包消息，请在手机上查看"},
            {"template": "{{nick.DATA}} 是本轮最佳手气"},
            {"content": "@nkaifan... 是本轮最佳手气"},
            {
                "appid": 2694923,
                "content": "1.24元，@tqtq 你知道吗？我稀罕你很久了",
            },
            {"appid": "2694923", "content": "8元,@someone 祝你开心"},
        ]

        for message in red_packet_messages:
            with self.subTest(message=message):
                self.assertEqual(classify_message_filter(message), "red_packet")

    def test_recognizes_fansgroup_badge_template_or_strict_system_shape(self) -> None:
        self.assertEqual(
            classify_message_filter(
                {
                    "template": "恭喜{{nick.DATA}}今日获得“{{title.DATA}}”标识",
                    "content": "任意文案",
                }
            ),
            "fansgroup_badge",
        )
        self.assertEqual(
            classify_message_filter(
                {
                    "appid": 2694923,
                    "content": "恭喜胖子快醒醒今日获得“早鸟”标识",
                    "from_user": {"profile_url": "fansgroups"},
                }
            ),
            "fansgroup_badge",
        )

    def test_does_not_use_generic_numeric_fields_as_filter_signals(self) -> None:
        self.assertIsNone(
            classify_message_filter(
                {
                    "type": 321,
                    "media_type": 0,
                    "sub_type": 101,
                    "content": "这是一条正常消息",
                }
            )
        )

    def test_does_not_misclassify_normal_red_packet_or_money_discussion(self) -> None:
        normal_messages = [
            {"content": "今晚会发红包吗"},
            {"content": "你领到昨天的红包了吗？"},
            {"content": "这件商品 1.24 元"},
            {"appid": 2694923, "content": "39元套餐，@someone 看看这个"},
            {"appid": 943, "content": "1.24元，@tqtq 这个价格可以吗"},
            {"content": "@someone 你是今天的最佳手气吗？"},
        ]

        for message in normal_messages:
            with self.subTest(message=message):
                self.assertIsNone(classify_message_filter(message))

    def test_badge_content_requires_app_and_fansgroup_sender(self) -> None:
        content = "恭喜胖子快醒醒今日获得“早鸟”标识"
        messages_missing_required_evidence = [
            {"content": content},
            {
                "appid": 2694923,
                "content": content,
                "from_user": {"profile_url": "u/123"},
            },
            {
                "appid": 943,
                "content": content,
                "from_user": {"profile_url": "fansgroups"},
            },
        ]

        for message in messages_missing_required_evidence:
            with self.subTest(message=message):
                self.assertIsNone(classify_message_filter(message))


if __name__ == "__main__":
    unittest.main()
