"""Reuse verified local content without crossing account, group or deletion scope."""
import hashlib
import json
from urllib.parse import urlsplit

from .archive_urls import normalize_source
from .archive_storage import ArchiveError, stored_path

FIELDS = ('local_path', 'file_size', 'content_hash', 'mime_type')


def resource_key(job):
    try:
        source = urlsplit(normalize_source(job['source_url']))._replace(fragment='').geturl()
    except ArchiveError:
        source = job['source_url']
    return job['account_id'], job['group_id'], job['kind'], source


def url_hash(job):
    return hashlib.sha256(resource_key(job)[-1].encode('utf-8')).hexdigest()


def index_resources(connection):
    rows = connection.execute('''SELECT j.id,j.source_url,j.kind,m.account_id,m.group_id FROM archive_jobs j
        JOIN messages m ON m.id=j.message_id LEFT JOIN archive_resource_keys k ON k.job_id=j.id
        WHERE k.job_id IS NULL''').fetchall()
    connection.executemany('INSERT OR IGNORE INTO archive_resource_keys(job_id,url_hash) VALUES(?,?)',
                           ((row['id'], url_hash(row)) for row in rows))


def verified_file(root, job):
    try:
        path = stored_path(root, job['local_path'] or '', job['account_id'])
        with path.open('rb') as content:
            valid = path.stat().st_size == job['file_size'] and hashlib.file_digest(content, 'sha256').hexdigest() == job['content_hash']
        return path if valid else None
    except (ArchiveError, OSError):
        return None


def reusable(connection, job, root):
    candidates = connection.execute('''SELECT j.*,m.account_id,m.group_id FROM archive_jobs j
        JOIN messages m ON m.id=j.message_id JOIN archive_resource_keys k ON k.job_id=j.id
        WHERE k.url_hash=? AND m.account_id=? AND m.group_id=? AND m.is_deleted=0
        AND j.kind=? AND j.status='stored' AND j.id!=? ORDER BY j.id DESC''',
        (url_hash(job), job['account_id'], job['group_id'], job['kind'], job['id']))
    key = resource_key(job)
    for candidate in candidates:
        if resource_key(candidate) != key:
            continue
        path = verified_file(root, candidate)
        if path:
            resources = []
            if candidate['mime_type'] == 'application/vnd.weibo-archive+json':
                resources = json.loads(path.read_text(encoding='utf-8'))['resources']
            return {field: candidate[field] for field in FIELDS}, resources
    return None


def unique_batch(rows, limit):
    selected, seen = [], set()
    for row in rows:
        key = resource_key(row)
        if key not in seen:
            selected.append(row)
            seen.add(key)
        if len(selected) == limit:
            break
    return selected
