import hashlib
import json
import re
import sqlite3
from datetime import date, datetime, time, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..database import get_connection
from ..importer import (
    build_content_hash,
    classify_import_filter,
    ensure_group_member,
    ensure_user,
    insert_message,
    is_duplicate_message,
)
from .collection_jobs import ensure_account_and_group, normalize_datetime, parse_datetime
from .messages import row_to_dict


router = APIRouter(prefix="/api", tags=["browser-captures"])

SCRIPT_VERSION = "visible_blocks_scroll_v9"
PARSER_VERSION = "visible_blocks_parser_v3"
MAX_BLOCKS = 20_000
MAX_TEXT_LENGTH = 5_000_000
MIN_CHAT_MESSAGE_LEFT = 320
MESSAGE_LEFT_THRESHOLD = 700
DATE_TOKEN_PATTERN = r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?|\d{1,2}[-/月]\d{1,2}日?|今天|昨天|前天"
TIME_PREFIX_RE = re.compile(r"^(?P<time>\d{1,2}:\d{2})\s+(?P<body>.+)$", re.S)
FULL_DATETIME_PREFIX_RE = re.compile(
    rf"^(?P<date>{DATE_TOKEN_PATTERN})\s*(?P<time>\d{{1,2}}:\d{{2}})\s+(?P<body>.+)$",
    re.S,
)
MONTH_DAY_TIME_PREFIX_RE = re.compile(
    rf"^(?P<date>{DATE_TOKEN_PATTERN})\s*(?P<time>\d{{1,2}}:\d{{2}})\s+(?P<body>.+)$",
    re.S,
)
RELATIVE_TIME_PREFIX_RE = re.compile(
    r"^(?P<date>今天|昨天|前天)\s*(?P<time>\d{1,2}:\d{2})\s+(?P<body>.+)$",
    re.S,
)
SENDER_TIME_PREFIX_RE = re.compile(
    rf"^(?P<sender>\S{{1,80}})\s+(?P<time>\d{{1,2}}:\d{{2}})\s+(?P<body>.+)$",
    re.S,
)
SENDER_DATE_TIME_PREFIX_RE = re.compile(
    rf"^(?P<sender>\S{{1,80}})\s+(?P<date>{DATE_TOKEN_PATTERN})\s+(?P<time>\d{{1,2}}:\d{{2}})\s+(?P<body>.+)$",
    re.S,
)
DATE_MARKER_RE = re.compile(
    rf"^(?P<date>{DATE_TOKEN_PATTERN})(?:\s+(?:星期|周)[一二三四五六日天])?$"
)
SENDER_RE = re.compile(r"^(?P<sender>\S+)\s+(?P<content>.+)$", re.S)
SENDER_ONLY_RE = re.compile(r"^[\w\u4e00-\u9fff·._@\-（）()]+(?:真爱粉)?$")
URL_RE = re.compile(r"https?://\S+|(?:t\.cn|weibo\.com)/\S+", re.I)


class CaptureBlock(BaseModel):
    text: str = Field(max_length=8000)
    tag: str | None = None
    kind: str | None = None
    top: float | None = None
    left: float | None = None
    width: float | None = None
    height: float | None = None
    scroll_top: float | None = None
    round_index: int | None = None


class BrowserCaptureRequest(BaseModel):
    account_id: int
    group_id: int
    range_start: str
    range_end: str
    page_url: str | None = None
    page_title: str | None = None
    captured_at: str
    visible_text: str | None = None
    blocks: list[CaptureBlock]
    script_version: str = SCRIPT_VERSION


class BrowserCaptureImportRequest(BaseModel):
    confirm: bool = False


def normalize_range(range_start: str, range_end: str) -> tuple[str, str]:
    start = normalize_datetime(range_start, "range_start")
    end = normalize_datetime(range_end, "range_end")
    if parse_datetime(start, "range_start") >= parse_datetime(end, "range_end"):
        raise HTTPException(status_code=400, detail="range_start must be before range_end.")
    return start, end


def load_browser_capture(capture_id: int, connection: sqlite3.Connection) -> sqlite3.Row:
    row = connection.execute(
        """
        SELECT *
        FROM browser_page_captures
        WHERE id = ?
        """,
        (capture_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Browser capture not found")
    return row


def parse_capture_blocks(blocks_json: str) -> list[dict[str, Any]]:
    try:
        blocks = json.loads(blocks_json)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=500, detail="Capture blocks JSON is invalid") from error
    if not isinstance(blocks, list):
        raise HTTPException(status_code=500, detail="Capture blocks JSON must be a list")
    return [block for block in blocks if isinstance(block, dict)]


def combine_capture_date(base_start: datetime, range_end: datetime, hhmm: str) -> str | None:
    hour, minute = [int(part) for part in hhmm.split(":", 1)]
    candidate = datetime.combine(base_start.date(), time(hour=hour, minute=minute))
    if candidate < base_start and range_end.date() > base_start.date():
        candidate += timedelta(days=1)
    if candidate < base_start or candidate > range_end:
        return None
    return candidate.strftime("%Y-%m-%d %H:%M:%S")


def parse_date_token(token: str, *, range_start: datetime, range_end: datetime) -> date | None:
    normalized = (
        token.strip()
        .replace("年", "-")
        .replace("月", "-")
        .replace("日", "")
        .replace("/", "-")
    )
    if normalized == "今天":
        return range_end.date()
    if normalized == "昨天":
        return (range_end - timedelta(days=1)).date()
    if normalized == "前天":
        return (range_end - timedelta(days=2)).date()

    for fmt in ("%Y-%m-%d", "%m-%d"):
        try:
            parsed = datetime.strptime(normalized, fmt)
        except ValueError:
            continue
        if fmt == "%m-%d":
            year = range_end.year
            parsed = parsed.replace(year=year)
            if parsed.date() > range_end.date() and range_start.year < range_end.year:
                parsed = parsed.replace(year=range_start.year)
        return parsed.date()
    return None


def combine_date_and_time(day: date, hhmm: str) -> str:
    hour, minute = [int(part) for part in hhmm.split(":", 1)]
    return datetime.combine(day, time(hour=hour, minute=minute)).strftime("%Y-%m-%d %H:%M:%S")


def block_position(block: dict[str, Any]) -> float:
    return float(block.get("scroll_top") or 0) + float(block.get("top") or 0)


def parse_message_prefix(
    text: str,
    *,
    current_date: date,
    range_start: datetime,
    range_end: datetime,
) -> tuple[str, str, bool] | None:
    sender_date_match = SENDER_DATE_TIME_PREFIX_RE.match(text)
    if sender_date_match is not None:
        day = parse_date_token(
            sender_date_match.group("date"),
            range_start=range_start,
            range_end=range_end,
        )
        if day is None:
            return None
        sent_at = combine_date_and_time(day, sender_date_match.group("time"))
        body = f"{sender_date_match.group('sender')} {sender_date_match.group('body')}"
        return sent_at, body, True

    for regex in (FULL_DATETIME_PREFIX_RE, MONTH_DAY_TIME_PREFIX_RE, RELATIVE_TIME_PREFIX_RE):
        match = regex.match(text)
        if match is None:
            continue
        day = parse_date_token(match.group("date"), range_start=range_start, range_end=range_end)
        if day is None:
            return None
        return combine_date_and_time(day, match.group("time")), match.group("body"), True

    sender_time_match = SENDER_TIME_PREFIX_RE.match(text)
    if sender_time_match is not None:
        sent_at = combine_date_and_time(current_date, sender_time_match.group("time"))
        body = f"{sender_time_match.group('sender')} {sender_time_match.group('body')}"
        return sent_at, body, False

    match = TIME_PREFIX_RE.match(text)
    if match is None:
        return None
    return combine_date_and_time(current_date, match.group("time")), match.group("body"), False


def detect_message_type(content: str) -> str:
    if "发来了一条链接消息" in content or URL_RE.search(content):
        return "link"
    if "发来了一张图片" in content or "图片" == content.strip():
        return "image"
    if "发来了一份文件" in content or "文件" == content.strip():
        return "file"
    return "text"


def build_stable_source_message_id(
    *,
    account_id: int,
    group_id: int,
    sender_name: str,
    sent_at: str,
    content_text: str,
) -> str:
    raw = json.dumps(
        {
            "account_id": account_id,
            "group_id": group_id,
            "sender_name": sender_name,
            "sent_at": sent_at,
            "content_text": content_text,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return f"browser:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def parse_capture_messages(
    capture: sqlite3.Row,
    connection: sqlite3.Connection,
) -> dict[str, Any]:
    blocks = sorted(parse_capture_blocks(capture["blocks_json"]), key=block_position)
    start_dt = parse_datetime(capture["range_start"], "range_start")
    end_dt = parse_datetime(capture["range_end"], "range_end")
    items: list[dict[str, Any]] = []
    skipped_blocks = 0
    seen_source_ids: set[str] = set()
    seen_raw_message_texts: set[str] = set()
    current_date = start_dt.date()
    last_message_dt: datetime | None = None

    for index, block in enumerate(blocks):
        text = str(block.get("text") or "").strip()
        if not text:
            continue
        if text.startswith("正在滚动采集微博群聊消息"):
            continue

        date_marker = DATE_MARKER_RE.match(text)
        if date_marker is not None and (block.get("kind") == "date_marker" or block.get("tag") != "li"):
            parsed_date = parse_date_token(
                date_marker.group("date"),
                range_start=start_dt,
                range_end=end_dt,
            )
            if parsed_date is not None:
                current_date = parsed_date
            continue

        block_left = float(block.get("left") or 0)
        is_message_block = (
            block.get("kind") in {"message", "message_candidate", "raw_text"}
            and block_left >= MIN_CHAT_MESSAGE_LEFT
        ) or (
            block.get("tag") in {"li", "article", "section", "div"}
            and block_left >= MESSAGE_LEFT_THRESHOLD
        )
        if not is_message_block:
            continue

        raw_message_text = " ".join(text.split())
        if raw_message_text in seen_raw_message_texts:
            skipped_blocks += 1
            continue
        seen_raw_message_texts.add(raw_message_text)

        parsed_prefix = parse_message_prefix(
            text,
            current_date=current_date,
            range_start=start_dt,
            range_end=end_dt,
        )
        if parsed_prefix is None:
            skipped_blocks += 1
            continue

        sent_at, body_text, explicit_date = parsed_prefix
        sent_dt = parse_datetime(sent_at, "message.sent_at")
        if not explicit_date and last_message_dt is not None and sent_dt < last_message_dt:
            next_date = current_date + timedelta(days=1)
            if next_date <= end_dt.date():
                current_date = next_date
                parsed_prefix = parse_message_prefix(
                    text,
                    current_date=current_date,
                    range_start=start_dt,
                    range_end=end_dt,
                )
                if parsed_prefix is not None:
                    sent_at, body_text, explicit_date = parsed_prefix
                    sent_dt = parse_datetime(sent_at, "message.sent_at")
        if sent_dt < start_dt or sent_dt > end_dt:
            skipped_blocks += 1
            continue
        last_message_dt = sent_dt

        body = " ".join(body_text.split())
        sender_match = SENDER_RE.match(body)
        if sender_match is None:
            if len(body) <= 80 and SENDER_ONLY_RE.match(body):
                skipped_blocks += 1
                continue
            sender_name = "unknown_user"
            content_text = body
        else:
            sender_name = sender_match.group("sender").strip()
            content_text = sender_match.group("content").strip()

        if not content_text:
            skipped_blocks += 1
            continue

        message_type = detect_message_type(content_text)
        source_message_id = build_stable_source_message_id(
            account_id=int(capture["account_id"]),
            group_id=int(capture["group_id"]),
            sender_name=sender_name,
            sent_at=sent_at,
            content_text=content_text,
        )
        if source_message_id in seen_source_ids:
            skipped_blocks += 1
            continue
        seen_source_ids.add(source_message_id)

        message = {
            "source_message_id": source_message_id,
            "source_user_id": None,
            "sender_name": sender_name,
            "sent_at": sent_at,
            "message_type": message_type,
            "content_text": content_text,
            "attachments": [],
            "browser_capture_id": int(capture["id"]),
            "parser_version": PARSER_VERSION,
            "block": block,
        }
        duplicate = connection.execute(
            """
            SELECT id
            FROM messages
            WHERE account_id = ?
              AND group_id = ?
              AND source_message_id = ?
              AND is_deleted = 0
            LIMIT 1
            """,
            (capture["account_id"], capture["group_id"], source_message_id),
        ).fetchone()
        items.append(
            {
                "index": len(items) + 1,
                "sent_at": sent_at,
                "sender_name": sender_name,
                "message_type": message_type,
                "content_text": content_text,
                "source_message_id": source_message_id,
                "is_duplicate": duplicate is not None,
                "block_top": block.get("top"),
                "raw_message": message,
            }
        )

    return {
        "capture_id": int(capture["id"]),
        "parser_version": PARSER_VERSION,
        "total_blocks": len(blocks),
        "skipped_blocks": skipped_blocks,
        "parsed_count": len(items),
        "duplicate_count": sum(1 for item in items if item["is_duplicate"]),
        "items": items,
    }


def build_capture_script(
    *,
    account_id: int,
    group_id: int,
    range_start: str,
    range_end: str,
) -> str:
    payload = {
        "account_id": account_id,
        "group_id": group_id,
        "range_start": range_start,
        "range_end": range_end,
        "endpoint": "http://127.0.0.1:8000/api/browser-captures",
        "script_version": SCRIPT_VERSION,
        "max_blocks": MAX_BLOCKS,
    }
    payload_json = json.dumps(payload, ensure_ascii=False)
    return f"""(() => {{
  const cfg = {payload_json};
  const norm = (value) => String(value || "").replace(/\\s+/g, " ").trim();
  const visible = (el) => {{
    const style = window.getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity) === 0) return false;
    const rect = el.getBoundingClientRect();
    return rect.width >= 20 && rect.height >= 8 && rect.bottom >= 0 && rect.right >= 0 && rect.top <= window.innerHeight && rect.left <= window.innerWidth;
  }};
  const elements = Array.from(document.querySelectorAll("article,section,li,div,p,span,time,a"));
  const seen = new Set();
  const blocks = [];
  for (const el of elements) {{
    if (!visible(el)) continue;
    const text = norm(el.innerText || el.textContent);
    if (!text || text.length > 2000) continue;
    const key = text + "|" + Math.round(el.getBoundingClientRect().top) + "|" + Math.round(el.getBoundingClientRect().left);
    if (seen.has(key)) continue;
    seen.add(key);
    const rect = el.getBoundingClientRect();
    blocks.push({{
      text,
      tag: el.tagName.toLowerCase(),
      top: Math.round(rect.top),
      left: Math.round(rect.left),
      width: Math.round(rect.width),
      height: Math.round(rect.height)
    }});
  }}
  blocks.sort((a, b) => (a.top - b.top) || (a.left - b.left));
  const selected = blocks.slice(0, cfg.max_blocks);
  fetch(cfg.endpoint, {{
    method: "POST",
    headers: {{"Content-Type": "application/json"}},
    body: JSON.stringify({{
      account_id: cfg.account_id,
      group_id: cfg.group_id,
      range_start: cfg.range_start,
      range_end: cfg.range_end,
      page_url: location.href,
      page_title: document.title,
      captured_at: new Date().toISOString(),
      visible_text: selected.map((item) => item.text).join("\\n"),
      blocks: selected,
      script_version: cfg.script_version
    }})
  }})
    .then(async (response) => {{
      const data = await response.json().catch(() => ({{}}));
      if (!response.ok) throw new Error(data.detail || "capture failed");
      alert(`已保存当前页面快照 #${{data.id}}，文本块 ${{data.block_count}} 条。`);
    }})
    .catch((error) => alert(`页面快照保存失败：${{error.message}}`));
}})();"""


def build_scroll_capture_script(
    *,
    account_id: int,
    group_id: int,
    range_start: str,
    range_end: str,
) -> str:
    payload = {
        "account_id": account_id,
        "group_id": group_id,
        "range_start": range_start,
        "range_end": range_end,
        "endpoint": "http://127.0.0.1:8000/api/browser-captures",
        "script_version": SCRIPT_VERSION,
        "max_blocks": MAX_BLOCKS,
        "max_scroll_steps": 1600,
        "scroll_delay_ms": 550,
        "max_idle_rounds": 10,
        "max_no_block_rounds": 45,
        "max_text_per_block": 8000,
    }
    payload_json = json.dumps(payload, ensure_ascii=False)
    script = r"""(async () => {
  const cfg = __PAYLOAD__;
  const norm = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const rightMin = () => Math.max(360, Math.round(window.innerWidth * 0.28));
  const chatLeftMin = () => Math.max(320, Math.round(window.innerWidth * 0.24));
  const timePattern = /\b\d{1,2}:\d{2}\b/;
  const explicitDateTimePattern = /^(?:\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?|\d{1,2}[-/月]\d{1,2}日?|今天|昨天|前天)\s*\d{1,2}:\d{2}\b/;
  const dateMarkerPattern = /^(?:\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?|\d{1,2}[-/月]\d{1,2}日?|今天|昨天|前天)(?:\s+(?:星期|周)[一二三四五六日天])?$/;
  const visible = (el) => {
    const style = window.getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity) === 0) return false;
    const rect = el.getBoundingClientRect();
    return rect.width >= 20 && rect.height >= 8 && rect.bottom >= 0 && rect.right >= 0 && rect.top <= window.innerHeight && rect.left <= window.innerWidth;
  };
  const createStatus = () => {
    const old = document.getElementById("weibo-chat-collector-status");
    if (old) old.remove();
    const box = document.createElement("div");
    box.id = "weibo-chat-collector-status";
    box.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:2147483647;background:#111827;color:#fff;padding:12px 14px;border-radius:8px;font:13px/1.5 system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:360px;box-shadow:0 8px 24px rgba(0,0,0,.22);white-space:pre-wrap;";
    document.body.appendChild(box);
    return (text) => { box.textContent = text; };
  };
  const setStatus = createStatus();
  window.__weiboChatCollectorStop = false;
  const parseRangeDay = (value) => {
    const match = String(value || "").match(/(\d{4})[-/](\d{1,2})[-/](\d{1,2})/);
    if (!match) return null;
    return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  };
  const parseRangeDateTime = (value) => {
    const match = String(value || "").match(/(\d{4})[-/](\d{1,2})[-/](\d{1,2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?/);
    if (!match) return null;
    return new Date(
      Number(match[1]),
      Number(match[2]) - 1,
      Number(match[3]),
      Number(match[4]),
      Number(match[5]),
      Number(match[6] || 0)
    );
  };
  const dayKey = (day) => day ? day.getFullYear() * 10000 + (day.getMonth() + 1) * 100 + day.getDate() : null;
  const dateTimeKey = (value) => value ? value.getTime() : null;
  const dayLabel = (day) => day ? `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}` : "";
  const dateTimeLabel = (value) => value
    ? `${dayLabel(value)} ${String(value.getHours()).padStart(2, "0")}:${String(value.getMinutes()).padStart(2, "0")}`
    : "";
  const rangeStartDay = parseRangeDay(cfg.range_start);
  const rangeStartDateTime = parseRangeDateTime(cfg.range_start);
  const rangeEndDay = parseRangeDay(cfg.range_end) || new Date();
  const rangeStartKey = dayKey(rangeStartDay);
  const rangeStartDateTimeKey = dateTimeKey(rangeStartDateTime);
  const makeDay = (year, month, day) => new Date(Number(year), Number(month) - 1, Number(day));
  const resolveMonthDay = (month, day) => {
    let candidate = makeDay(rangeEndDay.getFullYear(), month, day);
    if (dayKey(candidate) > dayKey(rangeEndDay) && rangeStartDay && rangeStartDay.getFullYear() < rangeEndDay.getFullYear()) {
      candidate = makeDay(rangeStartDay.getFullYear(), month, day);
    }
    return candidate;
  };
  const extractDays = (text) => {
    const value = norm(text);
    const days = [];
    for (const match of value.matchAll(/(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?/g)) {
      days.push(makeDay(match[1], match[2], match[3]));
    }
    for (const match of value.matchAll(/(?:^|[^\d])(\d{1,2})月(\d{1,2})日/g)) {
      days.push(resolveMonthDay(match[1], match[2]));
    }
    for (const match of value.matchAll(/(?:^|[^\d])(\d{1,2})[-/](\d{1,2})(?:$|[^\d])/g)) {
      days.push(resolveMonthDay(match[1], match[2]));
    }
    if (/(^|\s)今天(\s|$)/.test(value) || value === "今天") days.push(makeDay(rangeEndDay.getFullYear(), rangeEndDay.getMonth() + 1, rangeEndDay.getDate()));
    if (/(^|\s)昨天(\s|$)/.test(value) || value === "昨天") {
      const yesterday = new Date(rangeEndDay);
      yesterday.setDate(yesterday.getDate() - 1);
      days.push(yesterday);
    }
    if (/(^|\s)前天(\s|$)/.test(value) || value === "前天") {
      const beforeYesterday = new Date(rangeEndDay);
      beforeYesterday.setDate(beforeYesterday.getDate() - 2);
      days.push(beforeYesterday);
    }
    return days.filter((day) => !Number.isNaN(day.getTime()));
  };
  const extractPrefixDays = (text) => {
    const value = norm(text);
    const match = value.match(/^(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?|\d{1,2}[-/月]\d{1,2}日?|今天|昨天|前天)\s*\d{1,2}:\d{2}\b/);
    if (!match) return [];
    return extractDays(match[1]);
  };
  const extractPrefixDateTimes = (text) => {
    const value = norm(text);
    const match = value.match(/^(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?|\d{1,2}[-/月]\d{1,2}日?|今天|昨天|前天)\s*(\d{1,2}):(\d{2})\b/);
    if (!match) return [];
    return extractDays(match[1]).map((day) => new Date(
      day.getFullYear(),
      day.getMonth(),
      day.getDate(),
      Number(match[2]),
      Number(match[3])
    ));
  };
  const dateLike = (text) => dateMarkerPattern.test(norm(text));
  const explicitMessageDateLike = (text) => explicitDateTimePattern.test(norm(text));
  const messageLike = (text) => timePattern.test(text) && norm(text).length >= 8;
  const getScrollTop = (el) => el === document.scrollingElement ? window.scrollY : el.scrollTop;
  const setScrollTop = (el, value) => {
    if (el === document.scrollingElement) {
      window.scrollTo(0, value);
      return;
    }
    el.scrollTop = value;
    el.dispatchEvent(new Event("scroll", { bubbles: true }));
  };
  const getScrollHeight = (el) => el === document.scrollingElement ? document.documentElement.scrollHeight : el.scrollHeight;
  const getClientHeight = (el) => el === document.scrollingElement ? window.innerHeight : el.clientHeight;
  const looksLikeChatMessageElement = (el) => {
    if (!visible(el)) return false;
    const rect = el.getBoundingClientRect();
    if (rect.left < chatLeftMin() || rect.width < 80) return false;
    const text = norm(el.innerText || el.textContent);
    return text.length >= 8 && messageLike(text);
  };
  const isScrollable = (el) => {
    if (!el) return false;
    if (el === document.scrollingElement) return true;
    const style = window.getComputedStyle(el);
    const overflow = `${style.overflowY} ${style.overflow}`;
    const rect = el.getBoundingClientRect();
    return /(auto|scroll|overlay)/.test(overflow) && el.scrollHeight > el.clientHeight + 40 && rect.width >= 260 && rect.height >= 120;
  };
  const describeScroller = (el) => {
    const rect = el === document.scrollingElement
      ? { left: 0, top: 0, width: window.innerWidth, height: window.innerHeight }
      : el.getBoundingClientRect();
    const name = el === document.scrollingElement
      ? "document"
      : `${el.tagName.toLowerCase()}${el.id ? "#" + el.id : ""}${el.className ? "." + String(el.className).trim().replace(/\s+/g, ".").slice(0, 80) : ""}`;
    return `${name} left=${Math.round(rect.left)} top=${Math.round(rect.top)} h=${Math.round(rect.height)} scroll=${Math.round(getScrollTop(el))}/${Math.round(getScrollHeight(el))}`;
  };
  const findScroller = () => {
    const chatItems = Array.from(document.querySelectorAll("li,article,section,div,p"))
      .filter(looksLikeChatMessageElement);
    const candidateScores = new Map();
    const addCandidate = (el, score) => {
      if (!isScrollable(el)) return;
      const rect = el === document.scrollingElement
        ? { left: 0, top: 0, width: window.innerWidth, height: window.innerHeight }
        : el.getBoundingClientRect();
      if (rect.right < chatLeftMin() || rect.height < 120) return;
      candidateScores.set(el, (candidateScores.get(el) || 0) + score + Math.min(getScrollHeight(el) / 10, 500));
    };
    for (const item of chatItems) {
      let current = item.parentElement;
      let depth = 0;
      while (current && depth < 12) {
        addCandidate(current, 3000 - depth * 120);
        current = current.parentElement;
        depth += 1;
      }
    }
    for (const el of Array.from(document.querySelectorAll("div,section,main,ul"))) {
      if (!isScrollable(el)) continue;
      const rect = el.getBoundingClientRect();
      const containedMessages = Array.from(el.querySelectorAll("li,article,section,div,p")).filter(looksLikeChatMessageElement).length;
      if (containedMessages > 0) {
        addCandidate(el, containedMessages * 1000 + (rect.left >= chatLeftMin() ? 500 : 0));
      }
    }
    addCandidate(document.scrollingElement, 50);
    let best = null;
    let bestScore = -1;
    for (const [el, score] of candidateScores.entries()) {
      if (score > bestScore) {
        best = el;
        bestScore = score;
      }
    }
    return best || document.scrollingElement;
  };
  const scroller = findScroller();
  const scrollerDescription = describeScroller(scroller);
  const scrollerRect = () => scroller === document.scrollingElement
    ? { top: 0, bottom: window.innerHeight, left: 0, right: window.innerWidth }
    : scroller.getBoundingClientRect();
  const insideScroller = (rect) => {
    const parent = scrollerRect();
    return rect.bottom >= parent.top && rect.top <= parent.bottom && rect.right >= parent.left && rect.left <= parent.right;
  };
  const seen = new Set();
  const blocks = [];
  const visibleMessageSignature = () => Array.from(document.querySelectorAll("li,article,section,div,p"))
    .filter(looksLikeChatMessageElement)
    .slice(0, 6)
    .map((el) => {
      const rect = el.getBoundingClientRect();
      return `${Math.round(rect.top)}:${norm(el.innerText || el.textContent).slice(0, 80)}`;
    })
    .join("|");
  const dispatchWheelAtChat = (deltaY) => {
    const x = Math.min(window.innerWidth - 80, Math.max(chatLeftMin() + 160, Math.round(window.innerWidth * 0.58)));
    const y = Math.max(120, Math.round(window.innerHeight * 0.45));
    const target = document.elementFromPoint(x, y) || scroller;
    const eventInit = { deltaY, deltaMode: 0, bubbles: true, cancelable: true, clientX: x, clientY: y };
    for (const targetEl of [target, scroller, document, window]) {
      try {
        targetEl.dispatchEvent(new WheelEvent("wheel", eventInit));
      } catch (_) {
        // ignore event targets that cannot dispatch wheel
      }
    }
  };
  const collectMoveCandidates = () => {
    const candidates = new Set([scroller, document.scrollingElement]);
    const chatItems = Array.from(document.querySelectorAll("li,article,section,div,p")).filter(looksLikeChatMessageElement);
    for (const item of chatItems.slice(0, 12)) {
      let current = item;
      let depth = 0;
      while (current && depth < 14) {
        const rect = current === document.scrollingElement
          ? { right: window.innerWidth, height: window.innerHeight }
          : current.getBoundingClientRect();
        if (rect.right >= chatLeftMin() && rect.height >= 80) {
          candidates.add(current);
        }
        current = current.parentElement;
        depth += 1;
      }
    }
    for (const el of Array.from(document.querySelectorAll("div,section,main,ul,ol"))) {
      const rect = el.getBoundingClientRect();
      if (rect.right >= chatLeftMin() && rect.height >= 120 && el.scrollHeight > el.clientHeight + 20) {
        candidates.add(el);
      }
    }
    return Array.from(candidates).filter(Boolean);
  };
  const visibleChatItems = () => Array.from(document.querySelectorAll("li,article,section,div,p"))
    .filter(looksLikeChatMessageElement)
    .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
  const revealPreviousChatItem = () => {
    const items = visibleChatItems();
    const first = items[0];
    if (!first) return false;
    let candidate = first.previousElementSibling;
    if (!candidate && first.parentElement) candidate = first.parentElement.previousElementSibling;
    if (!candidate && first.parentElement?.parentElement) candidate = first.parentElement.parentElement.previousElementSibling;
    if (!candidate) return false;
    try {
      candidate.scrollIntoView({ block: "center", inline: "nearest" });
      return true;
    } catch (_) {
      return false;
    }
  };
  const moveOlder = async (step) => {
    const beforeSignature = visibleMessageSignature();
    const beforeTops = new Map();
    const candidates = collectMoveCandidates();
    for (const candidate of candidates) {
      beforeTops.set(candidate, getScrollTop(candidate));
    }
    const attempts = [];
    const applyMove = (candidate, delta) => {
      if (!candidate) return;
      try {
        if (candidate === document.scrollingElement) {
          window.scrollBy(0, delta);
        } else {
          candidate.scrollTop = Math.max(0, candidate.scrollTop + delta);
          candidate.scrollBy?.({ top: delta, behavior: "auto" });
          candidate.dispatchEvent(new Event("scroll", { bubbles: true }));
        }
      } catch (_) {
        // ignore candidates that reject programmatic scrolling
      }
    };
    for (const candidate of candidates) {
      applyMove(candidate, -step);
      attempts.push(describeScroller(candidate));
    }
    dispatchWheelAtChat(-step);
    const revealed = revealPreviousChatItem();
    await sleep(Math.max(220, Math.round(cfg.scroll_delay_ms * 0.55)));
    const afterSignature = visibleMessageSignature();
    const movedCandidates = candidates
      .map((candidate) => {
        const beforeTop = beforeTops.get(candidate) || 0;
        const afterTop = getScrollTop(candidate);
        return { candidate, beforeTop, afterTop, delta: afterTop - beforeTop };
      })
      .filter((item) => Math.abs(item.delta) >= 1);
    const changed = afterSignature !== beforeSignature || movedCandidates.length > 0;
    return {
      changed,
      revealed,
      movedCount: movedCandidates.length,
      candidateCount: candidates.length,
      detail: movedCandidates.slice(0, 3).map((item) => `${Math.round(item.beforeTop)}->${Math.round(item.afterTop)}`).join(", ") || attempts.slice(0, 2).join(" | ")
    };
  };
  const captureVisible = (roundIndex) => {
    let added = 0;
    let oldestVisibleDay = null;
    let oldestVisibleDateTime = null;
    const elements = Array.from(document.querySelectorAll("li,article,section,div,p,span,time,a"));
    for (const el of elements) {
      if (el.id === "weibo-chat-collector-status" || el.closest("#weibo-chat-collector-status")) continue;
      if (!visible(el)) continue;
      const text = norm(el.innerText || el.textContent);
      if (!text || text.length > cfg.max_text_per_block) continue;
      const rect = el.getBoundingClientRect();
      if (!insideScroller(rect)) continue;
      const tag = el.tagName.toLowerCase();
      const inChatColumn = rect.left >= chatLeftMin();
      const looksLikeWholePage = rect.left <= 20 && rect.width >= window.innerWidth * 0.85 && text.length > 180;
      const looksLikeSidebarPreview = rect.left < rightMin() && text.includes("系统通知") && text.includes("钱包通知");
      if (looksLikeWholePage || looksLikeSidebarPreview) continue;
      const childTexts = Array.from(el.children || [])
        .map((child) => norm(child.innerText || child.textContent))
        .filter(Boolean);
      const hasSameTextChild = childTexts.some((childText) => childText === text || (text.length > 30 && childText.length / text.length > 0.88));
      if (hasSameTextChild && !["li", "article", "section"].includes(tag)) continue;
      let kind = null;
      if (inChatColumn && dateLike(text) && text.length <= 60) {
        kind = "date_marker";
      } else if (inChatColumn && ["li", "article", "section", "div"].includes(tag) && rect.width >= 80 && messageLike(text)) {
        kind = "message";
      } else if (inChatColumn && messageLike(text)) {
        kind = "message_candidate";
      } else if (rect.left >= rightMin() && text.length <= 500 && !/^(首页|发现|消息|设置|搜索|发送|取消)$/.test(text)) {
        kind = "raw_text";
      }
      if (!kind) continue;
      if (kind === "date_marker" || explicitMessageDateLike(text)) {
        const visibleDays = kind === "date_marker" ? extractDays(text) : extractPrefixDays(text);
        for (const day of visibleDays) {
          if (!oldestVisibleDay || dayKey(day) < dayKey(oldestVisibleDay)) oldestVisibleDay = day;
        }
        if (explicitMessageDateLike(text)) {
          const visibleDateTimes = extractPrefixDateTimes(text);
          for (const dateTime of visibleDateTimes) {
            if (!oldestVisibleDateTime || dateTimeKey(dateTime) < dateTimeKey(oldestVisibleDateTime)) {
              oldestVisibleDateTime = dateTime;
            }
          }
        }
      }
      const absoluteTop = Math.round(getScrollTop(scroller) + rect.top);
      const key = kind + "|" + text + "|" + Math.round(absoluteTop / 32);
      if (seen.has(key)) continue;
      seen.add(key);
      blocks.push({
        text,
        tag,
        kind,
        top: Math.round(rect.top),
        left: Math.round(rect.left),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
        scroll_top: Math.round(getScrollTop(scroller)),
        round_index: roundIndex
      });
      added += 1;
    }
    return { added, oldestVisibleDay, oldestVisibleDateTime };
  };
  setStatus(`正在定位聊天容器...\n脚本版本：${cfg.script_version}\n滚动容器：${scrollerDescription}\n目标开始日期：${dayLabel(rangeStartDay)}\n如需手动停止并保存，在控制台输入：window.__weiboChatCollectorStop = true`);
  await sleep(cfg.scroll_delay_ms);
  let idleRounds = 0;
  let noBlockRounds = 0;
  let previousTop = getScrollTop(scroller);
  let previousSignature = visibleMessageSignature();
  let lastMove = { changed: false, revealed: false, movedCount: 0, candidateCount: 0, detail: "not-started" };
  let stopReason = "达到最大滚动轮次";
  for (let round = 0; round < cfg.max_scroll_steps && blocks.length < cfg.max_blocks; round += 1) {
    if (window.__weiboChatCollectorStop === true) {
      stopReason = "手动停止";
      break;
    }
    const captureInfo = captureVisible(round);
    const added = captureInfo.added;
    const currentTop = getScrollTop(scroller);
    const messageCount = blocks.filter((item) => ["message", "message_candidate"].includes(item.kind)).length;
    const oldestLabel = captureInfo.oldestVisibleDay ? dayLabel(captureInfo.oldestVisibleDay) : "未识别";
    const oldestDateTimeLabel = captureInfo.oldestVisibleDateTime ? dateTimeLabel(captureInfo.oldestVisibleDateTime) : "未识别";
    const currentSignature = visibleMessageSignature();
    setStatus(`正在滚动采集微博群聊消息\n脚本版本：${cfg.script_version}\n滚动容器：${scrollerDescription}\n移动探测：${lastMove.changed ? "有效" : "未变化"} / 候选 ${lastMove.candidateCount} / 移动 ${lastMove.movedCount} / ${lastMove.detail}\n轮次：${round + 1} / ${cfg.max_scroll_steps}\n目标开始时间：${dateTimeLabel(rangeStartDateTime) || dayLabel(rangeStartDay)}\n当前可见最早时间：${oldestDateTimeLabel}\n当前可见最早日期：${oldestLabel}\n已捕获：${messageCount} 条消息候选 / ${blocks.length} 个文本块\n新增文本块：${added}\n当前位置：${Math.round(currentTop)}\n手动停止：window.__weiboChatCollectorStop = true`);
    if (rangeStartDateTimeKey && captureInfo.oldestVisibleDateTime && dateTimeKey(captureInfo.oldestVisibleDateTime) < rangeStartDateTimeKey) {
      stopReason = `已到达开始时间之前：${oldestDateTimeLabel}`;
      break;
    }
    if (rangeStartKey && captureInfo.oldestVisibleDay && dayKey(captureInfo.oldestVisibleDay) < rangeStartKey) {
      stopReason = `已到达开始日期之前：${oldestLabel}`;
      break;
    }
    if (added === 0) {
      noBlockRounds += 1;
    } else {
      noBlockRounds = 0;
    }
    if (noBlockRounds >= cfg.max_no_block_rounds) {
      stopReason = "连续多轮没有捕获到可见文本块";
      break;
    }
    if (currentTop <= 2 && added === 0 && currentSignature === previousSignature && !lastMove.changed) {
      idleRounds += 1;
    } else if (added === 0 && Math.abs(currentTop - previousTop) < 2 && currentSignature === previousSignature && !lastMove.changed) {
      idleRounds += 1;
    } else {
      idleRounds = 0;
    }
    if (idleRounds >= cfg.max_idle_rounds) {
      stopReason = "已到达当前可滚动区域顶部";
      break;
    }
    previousTop = currentTop;
    previousSignature = currentSignature;
    const step = Math.max(520, Math.round(getClientHeight(scroller) * 0.82));
    lastMove = await moveOlder(step);
    await sleep(Math.max(120, Math.round(cfg.scroll_delay_ms * 0.35)));
  }
  captureVisible(cfg.max_scroll_steps);
  blocks.sort((a, b) => ((a.scroll_top || 0) + a.top) - ((b.scroll_top || 0) + b.top) || (a.left - b.left));
  const selected = blocks.slice(0, cfg.max_blocks);
  const selectedMessageCount = selected.filter((item) => ["message", "message_candidate"].includes(item.kind)).length;
  setStatus(`正在保存快照...\n脚本版本：${cfg.script_version}\n滚动容器：${scrollerDescription}\n最后移动：${lastMove.changed ? "有效" : "未变化"} / 候选 ${lastMove.candidateCount} / 移动 ${lastMove.movedCount} / ${lastMove.detail}\n停止原因：${stopReason}\n消息候选：${selectedMessageCount} 条\n文本块：${selected.length} 条`);
  fetch(cfg.endpoint, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      account_id: cfg.account_id,
      group_id: cfg.group_id,
      range_start: cfg.range_start,
      range_end: cfg.range_end,
      page_url: location.href,
      page_title: document.title,
      captured_at: new Date().toISOString(),
      visible_text: selected.map((item) => item.text).join("\n"),
      blocks: selected,
      script_version: cfg.script_version
    })
  })
    .then(async (response) => {
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "capture failed");
      setStatus(`保存完成：快照 #${data.id}\n脚本版本：${cfg.script_version}\n滚动容器：${scrollerDescription}\n最后移动：${lastMove.changed ? "有效" : "未变化"} / 候选 ${lastMove.candidateCount} / 移动 ${lastMove.movedCount} / ${lastMove.detail}\n停止原因：${stopReason}\n文本块：${data.block_count} 条\n请回到采集页面刷新快照并解析预览。`);
      alert(`已保存滚动快照 #${data.id}，文本块 ${data.block_count} 条。停止原因：${stopReason}。最后移动：${lastMove.changed ? "有效" : "未变化"} / 候选 ${lastMove.candidateCount} / 移动 ${lastMove.movedCount}`);
    })
    .catch((error) => {
      setStatus(`保存失败：${error.message}`);
      alert(`页面滚动快照保存失败：${error.message}`);
    });
})();"""
    return script.replace("__PAYLOAD__", payload_json)


@router.get("/browser-capture/snippet")
def get_browser_capture_snippet(
    account_id: int,
    group_id: int,
    range_start: str,
    range_end: str,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict[str, Any]:
    ensure_account_and_group(connection, account_id, group_id)
    start, end = normalize_range(range_start, range_end)
    return {
        "script": build_scroll_capture_script(
            account_id=account_id,
            group_id=group_id,
            range_start=start,
            range_end=end,
        ),
        "script_version": SCRIPT_VERSION,
        "range_start": start,
        "range_end": end,
    }


@router.post("/browser-captures")
def create_browser_capture(
    request: BrowserCaptureRequest,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict[str, Any]:
    ensure_account_and_group(connection, request.account_id, request.group_id)
    start, end = normalize_range(request.range_start, request.range_end)
    captured_at = normalize_datetime(request.captured_at, "captured_at")
    blocks = request.blocks[:MAX_BLOCKS]
    visible_text = (request.visible_text or "")[:MAX_TEXT_LENGTH]

    cursor = connection.execute(
        """
        INSERT INTO browser_page_captures (
            account_id,
            group_id,
            range_start,
            range_end,
            page_url,
            page_title,
            captured_at,
            visible_text,
            blocks_json,
            script_version,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'captured')
        """,
        (
            request.account_id,
            request.group_id,
            start,
            end,
            request.page_url,
            request.page_title,
            captured_at,
            visible_text,
            json.dumps([block.model_dump() for block in blocks], ensure_ascii=False),
            request.script_version,
        ),
    )
    capture_id = int(cursor.lastrowid)
    return {
        "id": capture_id,
        "block_count": len(blocks),
        "text_length": len(visible_text),
        "status": "captured",
    }


@router.get("/browser-captures")
def list_browser_captures(
    account_id: int | None = None,
    group_id: int | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict[str, Any]:
    filters = []
    params: list[Any] = []
    if account_id is not None:
        filters.append("bpc.account_id = ?")
        params.append(account_id)
    if group_id is not None:
        filters.append("bpc.group_id = ?")
        params.append(group_id)
    where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""

    rows = connection.execute(
        f"""
        SELECT
            bpc.id,
            bpc.account_id,
            wa.display_name AS account_name,
            bpc.group_id,
            cg.name AS group_name,
            bpc.range_start,
            bpc.range_end,
            bpc.page_url,
            bpc.page_title,
            bpc.captured_at,
            LENGTH(bpc.visible_text) AS text_length,
            bpc.script_version,
            bpc.status,
            bpc.created_at
        FROM browser_page_captures bpc
        JOIN weibo_accounts wa ON wa.id = bpc.account_id
        JOIN chat_groups cg ON cg.id = bpc.group_id
        {where_sql}
        ORDER BY bpc.created_at DESC, bpc.id DESC
        LIMIT ?
        """,
        [*params, limit],
    ).fetchall()
    return {"items": [row_to_dict(row) for row in rows], "total": len(rows)}


@router.get("/browser-captures/{capture_id}/parse-preview")
def preview_browser_capture_parse(
    capture_id: int,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict[str, Any]:
    capture = load_browser_capture(capture_id, connection)
    ensure_account_and_group(connection, int(capture["account_id"]), int(capture["group_id"]))
    result = parse_capture_messages(capture, connection)
    result["items"] = [
        {key: value for key, value in item.items() if key != "raw_message"}
        for item in result["items"]
    ]
    return result


@router.post("/browser-captures/{capture_id}/import")
def import_browser_capture_messages(
    capture_id: int,
    request: BrowserCaptureImportRequest,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict[str, Any]:
    if not request.confirm:
        raise HTTPException(status_code=400, detail="confirm must be true")

    capture = load_browser_capture(capture_id, connection)
    ensure_account_and_group(connection, int(capture["account_id"]), int(capture["group_id"]))
    result = parse_capture_messages(capture, connection)

    job_cursor = connection.execute(
        """
        INSERT INTO collection_jobs (
            account_id,
            group_id,
            range_start,
            range_end,
            status,
            started_at,
            collector_type
        )
        VALUES (?, ?, ?, ?, 'running', CURRENT_TIMESTAMP, 'browser_capture')
        """,
        (
            capture["account_id"],
            capture["group_id"],
            capture["range_start"],
            capture["range_end"],
        ),
    )
    collection_job_id = int(job_cursor.lastrowid)

    inserted_count = 0
    skipped_count = 0
    red_packet_count = 0
    filtered_system_notice_count = 0
    duplicate_count = 0

    for item in result["items"]:
        message = dict(item["raw_message"])
        filter_kind = classify_import_filter(message)
        if filter_kind == "red_packet":
            red_packet_count += 1
            skipped_count += 1
            continue
        if filter_kind == "fansgroup_badge":
            filtered_system_notice_count += 1
            skipped_count += 1
            continue

        user_id = ensure_user(connection, message)
        ensure_group_member(connection, int(capture["group_id"]), user_id, message)
        content_hash = build_content_hash(message)
        if is_duplicate_message(
            connection,
            int(capture["account_id"]),
            int(capture["group_id"]),
            user_id,
            message,
            content_hash,
        ):
            duplicate_count += 1
            skipped_count += 1
            continue

        insert_message(
            connection,
            int(capture["account_id"]),
            int(capture["group_id"]),
            user_id,
            collection_job_id,
            message,
            content_hash,
        )
        inserted_count += 1

    connection.execute(
        """
        UPDATE collection_jobs
        SET
            status = 'completed',
            finished_at = CURRENT_TIMESTAMP,
            total_seen_count = ?,
            inserted_count = ?,
            skipped_count = ?,
            failed_count = 0,
            filtered_red_packet_count = ?,
            filtered_system_notice_count = ?,
            error_message = NULL
        WHERE id = ?
        """,
        (
            result["parsed_count"],
            inserted_count,
            skipped_count,
            red_packet_count,
            filtered_system_notice_count,
            collection_job_id,
        ),
    )
    connection.execute(
        """
        UPDATE browser_page_captures
        SET status = 'imported',
            notes = ?
        WHERE id = ?
        """,
        (
            f"Imported by {PARSER_VERSION}; inserted={inserted_count}; skipped={skipped_count}; duplicates={duplicate_count}",
            capture_id,
        ),
    )

    job = connection.execute(
        """
        SELECT
            cj.*,
            wa.display_name AS account_name,
            cg.name AS group_name
        FROM collection_jobs cj
        JOIN weibo_accounts wa ON wa.id = cj.account_id
        JOIN chat_groups cg ON cg.id = cj.group_id
        WHERE cj.id = ?
        """,
        (collection_job_id,),
    ).fetchone()

    return {
        "capture_id": capture_id,
        "collection_job": row_to_dict(job),
        "parsed_count": result["parsed_count"],
        "inserted_count": inserted_count,
        "skipped_count": skipped_count,
        "duplicate_count": duplicate_count,
        "red_packet_count": red_packet_count,
        "filtered_system_notice_count": filtered_system_notice_count,
    }
