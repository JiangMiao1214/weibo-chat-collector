"""Read-time viewer filters shared by listing, export and destructive previews."""
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ViewerSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    members: list[Annotated[int, Field(strict=True, ge=1)]] = Field(default_factory=list, max_length=500)
    hours: list[Annotated[int, Field(strict=True, ge=0, le=23)]] = Field(default_factory=list, max_length=24)
    media: Literal['', 'photos', 'video', 'links'] = ''
    hide_noise: bool = False
    hide_join_notices: bool = False


def parse_viewer(encoded: str | None) -> ViewerSelection:
    try:
        return ViewerSelection.model_validate_json(encoded) if encoded else ViewerSelection()
    except ValidationError as error:
        raise HTTPException(422, '无效的成员、小时、媒体或噪声条件') from error


# Reversible display-only rules. Ingestion's high-confidence filter is untouched.
# Exact greetings never hide substantive sentences containing a greeting.
# Match a whole single-line join notice, not a mention followed by conversation.
JOIN_NOTICE_TEXT = "rtrim(trim(m.content_text, ' ' || char(9) || char(10) || char(13)), '。！!')"
JOIN_NOTICE_SQL = f"""(({JOIN_NOTICE_TEXT} GLOB '?*加入了群' OR {JOIN_NOTICE_TEXT} GLOB '?*加入了群聊')
    AND instr({JOIN_NOTICE_TEXT}, char(10))=0 AND instr({JOIN_NOTICE_TEXT}, char(13))=0)"""
NOISE_SQL = f"""(m.message_type='system' OR m.is_system_message=1 OR m.is_red_packet=1
    OR trim(m.content_text) IN ('早', '早安', '早上好', '晚安', '大家早', '大家早上好')
    OR {JOIN_NOTICE_SQL})"""
IMAGE_SQL = """(m.message_type='image' OR EXISTS
    (SELECT 1 FROM attachments v WHERE v.message_id=m.id AND v.attachment_type='image'))"""
VIDEO_ATTACHMENT_SQL = """(m.message_type='video' OR EXISTS
    (SELECT 1 FROM attachments v WHERE v.message_id=m.id AND v.attachment_type='video'))"""
VIDEO_SQL = f"({VIDEO_ATTACHMENT_SQL} OR m.content_text IN ('分享视频', 'Video'))"
LINK_SQL = f"""(m.message_type='link' OR {VIDEO_ATTACHMENT_SQL} OR instr(lower(m.content_text), 'https://')>0
    OR instr(lower(m.content_text), 'http://')>0 OR EXISTS
    (SELECT 1 FROM attachments v WHERE v.message_id=m.id AND v.attachment_type='link'))"""


def viewer_conditions(encoded: str | None):
    selection = parse_viewer(encoded)
    filters, params = [], []
    for expression, values in [('m.user_id', selection.members),
                               ("CAST(strftime('%H', m.sent_at) AS INTEGER)", selection.hours)]:
        if values:
            filters.append(expression + ' IN (' + ','.join('?' for _ in values) + ')')
            params.extend(values)
    media_sql = {'photos': IMAGE_SQL, 'video': VIDEO_SQL, 'links': LINK_SQL}
    if selection.media:
        filters.append(media_sql[selection.media])
    if selection.hide_noise:
        filters.append('NOT ' + NOISE_SQL)
    if selection.hide_join_notices:
        filters.append('NOT ' + JOIN_NOTICE_SQL)
    return filters, params


def use_standard_type(message_type: str | None, encoded: str | None) -> bool:
    """Quick media and its canonical label are one condition, not intersecting filters."""
    media = parse_viewer(encoded).media
    if not media:
        return bool(message_type)
    expected = {'photos': 'image', 'video': 'video', 'links': 'link'}[media]
    if message_type and message_type != expected:
        raise HTTPException(422, '消息类型与媒体条件冲突，请只使用一组类型筛选')
    return False
