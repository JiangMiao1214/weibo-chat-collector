"""Explicit account-scoped proxy trust, restricted to exact HTTPS DNS names."""
from contextlib import contextmanager
from contextvars import ContextVar
import ipaddress
import re
from urllib.parse import urlsplit
from fastapi import HTTPException

TRUSTED_HOSTS = ContextVar('archive_trusted_https_hosts', default=frozenset())


@contextmanager
def trust_hosts(hosts):
    token = TRUSTED_HOSTS.set(frozenset(hosts))
    try:
        yield
    finally:
        TRUSTED_HOSTS.reset(token)


def valid_host(host):
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        return bool(re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', host)) and not host.endswith(('.local', '.localhost', '.internal'))


def approve_host(connection, job_id, account_id, group_id):
    row = connection.execute('''SELECT j.error,f.failure_url FROM archive_jobs j JOIN messages m ON m.id=j.message_id
        JOIN archive_failures f ON f.job_id=j.id WHERE j.id=? AND m.account_id=? AND m.group_id=? AND m.is_deleted=0
        AND j.status IN ('failed','blocked')''', (job_id, account_id, group_id)).fetchone()
    if not row:
        raise HTTPException(404, '此范围内没有可设置的失败地址')
    parsed = urlsplit(row['failure_url'])
    host = (parsed.hostname or '').encode('idna').decode('ascii')
    if not valid_host(host) or 'Fake-IP' not in (row['error'] or '') or parsed.scheme not in ('http', 'https'):
        raise HTTPException(409, '仅能为 Fake-IP 失败的公网域名设置 HTTPS 兼容')
    connection.execute('INSERT OR IGNORE INTO archive_https_hosts(account_id,host) VALUES(?,?)', (account_id, host))
