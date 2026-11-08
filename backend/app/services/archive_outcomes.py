"""Distinguish resource-specific denial from account-wide authentication failures."""
from .archive_storage import ArchiveError


class ArchiveSkipped(ArchiveError):
    """A known unavailable resource; continue the queue without saving it."""


def check_resource_denial(payload):
    if not isinstance(payload, dict):
        return
    if any(str(payload.get(key)) in ('21301', '401', '403', '429') for key in ('error_code', 'code')):
        return
    message = ' '.join(payload.get(key, '') for key in ('msg', 'message', 'error', 'error_description')
                       if isinstance(payload.get(key), str)).lower()
    # A login/rate-limit error must never become a permanently skipped resource.
    if any(word in message for word in ('登录', 'login', 'cookie', 'token', '频繁', '频次', '限流',
                                       '验证码', '安全验证', '请求过多', 'rate limit', 'too many')):
        return
    if any(word in message for word in ('暂无查看权限', '无权查看此微博', '没有权限查看此微博')):
        raise ArchiveSkipped('已跳过：此微博暂无查看权限，未保存本地')
    if any(word in message for word in ('微博已被删除', '微博已删除', '微博不存在')):
        raise ArchiveSkipped('已跳过：此微博已删除或不存在，未保存本地')
