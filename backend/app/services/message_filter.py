from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal


MessageFilterKind = Literal["red_packet", "fansgroup_badge"]

_RED_PACKET_TYPES = frozenset(
    {
        "red_packet",
        "red_envelope",
        "redenvelope",
        "hongbao",
        "weibo_red_packet",
        "weibo_hongbao",
        "红包",
    }
)
_RED_PACKET_TEXT_PREFIXES = ("[红包]", "[微博红包]", "收到红包消息")
_RED_PACKET_EXACT_TEXTS = {"红包", "微博红包", "发了一个红包", "领取了红包"}
_BEST_LUCK_TEMPLATE = "{{nick.DATA}} 是本轮最佳手气"
_FANSGROUP_BADGE_TEMPLATE = "恭喜{{nick.DATA}}今日获得“{{title.DATA}}”标识"
_BEST_LUCK_CONTENT = re.compile(r"^@\S+\s+是本轮最佳手气$")
_RED_PACKET_AMOUNT_CONTENT = re.compile(
    r"^\d+(?:\.\d{1,2})?元[，,]\s*@\S+"
)
_FANSGROUP_BADGE_CONTENT = re.compile(r"^恭喜.+今日获得“[^”]+”标识$")


def classify_message_filter(message: Mapping[str, Any]) -> MessageFilterKind | None:
    """Return the high-confidence reason for filtering a raw Weibo message."""

    content = str(message.get("content") or message.get("content_text") or "").strip()
    template = str(message.get("template") or "").strip()
    icon = str(message.get("icon") or "").strip().lower()
    appid = str(message.get("appid") or "").strip()

    message_types = {
        str(message.get(field) or "").strip().lower()
        for field in ("type", "msg_type", "message_type")
    }
    is_system_notice = bool(
        message.get("is_system_message") is True
        or message_types & {"system", "notice", "system_message"}
    )
    if (
        message.get("is_redenvelope") in (True, 1, "1")
        or message.get("is_red_packet") in (True, 1, "1")
        or bool(message_types & _RED_PACKET_TYPES)
        or content in _RED_PACKET_EXACT_TEXTS
        or content.startswith(_RED_PACKET_TEXT_PREFIXES)
        or "redenvelope_" in icon
        or template == _BEST_LUCK_TEMPLATE
        or _BEST_LUCK_CONTENT.fullmatch(content) is not None
        or (
            appid == "2694923"
            and _RED_PACKET_AMOUNT_CONTENT.match(content) is not None
        )
        or (is_system_notice and "领取了" in content and "红包" in content)
    ):
        return "red_packet"

    from_user = message.get("from_user")
    profile_url = (
        str(from_user.get("profile_url") or "").strip()
        if isinstance(from_user, Mapping)
        else ""
    )
    if template == _FANSGROUP_BADGE_TEMPLATE or (
        appid == "2694923"
        and profile_url == "fansgroups"
        and _FANSGROUP_BADGE_CONTENT.fullmatch(content) is not None
    ):
        return "fansgroup_badge"

    return None
