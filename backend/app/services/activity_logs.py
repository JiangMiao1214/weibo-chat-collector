"""Diagnostic records expose codes and sanitized URLs, never raw exception messages."""
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode


def diagnostic_url(value):
    if not value:
        return None
    try:
        parts=urlsplit(value)
        if parts.scheme not in ('http','https') or not parts.hostname:
            return None
        # All values may contain signatures; don't maintain a fragile allowlist.
        query=urlencode([(key,'[redacted]') for key,_ in parse_qsl(parts.query,keep_blank_values=True)])
        path=re.sub(r'(?i)(token|secret|signature|authorization)/[^/]+',r'\1/[redacted]',parts.path)
        return urlunsplit((parts.scheme,parts.hostname,path,query,''))
    except ValueError:
        return None


def log_rows(connection,where,params,limit,offset):
    total=connection.execute(f'SELECT COUNT(*) FROM activity_events e WHERE {where}',params).fetchone()[0]
    rows=connection.execute(f'''SELECT e.*,m.is_deleted,a.source_url,af.failure_url
        FROM activity_events e LEFT JOIN archive_jobs a ON e.category='archive' AND a.id=e.task_id
        LEFT JOIN archive_failures af ON af.job_id=a.id LEFT JOIN messages m ON m.id=e.message_id
        WHERE {where} ORDER BY e.id DESC LIMIT ? OFFSET ?''',[*params,limit,offset])
    items=[]
    for row in rows:
        item=dict(row)
        item['source_url']=diagnostic_url(item['source_url'])
        item['failure_url']=diagnostic_url(item['failure_url'])
        items.append(item)
    return {'items':items,'total':total,'has_more':offset+len(items)<total,'offset':offset,
            'retention':'保留最近 90 天且最多 10000 条。升级前归档仅提供当前状态基线；事件时间为 UTC。地址为当前资源的脱敏地址。'}
