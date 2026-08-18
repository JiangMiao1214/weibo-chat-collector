from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass(slots=True)
class AttachmentCandidate:
    attachment_type: str
    source_url: str | None = None
    local_path: str | None = None
    file_name: str | None = None
    mime_type: str | None = None
    file_size: int | None = None
    title: str | None = None
    description: str | None = None


@dataclass(slots=True)
class CollectionCandidate:
    account_id: int
    group_id: int
    source_user_id: str | None
    sender_name: str
    sent_at: datetime
    message_type: str
    content_text: str | None
    source_message_id: str | None = None
    raw_payload: dict | list | None = None
    is_red_packet: bool = False
    is_system_message: bool = False
    attachments: list[AttachmentCandidate] = field(default_factory=list)
    sender_avatar_url: str | None = None

    @property
    def avatar_url(self) -> str | None:
        """Compatibility alias for callers that use the database column name."""
        return self.sender_avatar_url


class TimeRangeCollector(Protocol):
    collector_type: str

    def collect(
        self,
        account_id: int,
        group_id: int,
        range_start: datetime,
        range_end: datetime,
    ) -> list[CollectionCandidate]:
        """Collect messages for an authorized account and group time range."""
        ...
