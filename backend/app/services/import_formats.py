"""Normalized CSV input; raw fields remain preserved for provenance."""
import csv
import json
from pathlib import Path
from typing import Any


def read_csv_file(source_path: Path) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    account = None
    group = None

    with source_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            account = account or row.get("account")
            group = group or row.get("group")
            attachments = parse_attachments_json(row.get("attachments_json"))
            messages.append(
                {
                    "source_message_id": row.get("source_message_id") or None,
                    "reply_to_source_message_id": row.get("reply_to_source_message_id") or None,
                    "sender_name": row.get("sender_name") or row.get("user_name"),
                    "source_user_id": row.get("source_user_id") or None,
                    "sent_at": row.get("sent_at"),
                    "message_type": row.get("message_type") or "text",
                    "content_text": row.get("content_text") or "",
                    "attachments": attachments,
                    "raw_payload": row,
                }
            )

    return {"account": account, "group": group, "messages": messages}


def parse_attachments_json(value: str | None) -> list[dict[str, Any]]:
    if not value:
        return []
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        raise ValueError("attachments_json must be a JSON array.")
    return parsed


