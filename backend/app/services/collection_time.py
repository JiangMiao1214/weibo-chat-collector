"""Shared collection range parsing, preserving existing request validation."""
from datetime import datetime, timezone
from fastapi import HTTPException


def parse_datetime(value: str, field_name: str) -> datetime:
    normalized = value.replace("T", " ")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} must be an ISO-like datetime string.",
        ) from error


def normalize_datetime(value: str, field_name: str) -> str:
    return parse_datetime(value, field_name).strftime("%Y-%m-%d %H:%M:%S")


UTC_FORMAT = "%Y-%m-%d %H:%M:%S.%f"


def utc_now_text(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    return value.astimezone(timezone.utc).replace(tzinfo=None).strftime(UTC_FORMAT)


def parse_utc_text(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

