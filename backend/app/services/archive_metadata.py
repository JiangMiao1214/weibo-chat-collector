"""Read-only archive metadata for message lists and details."""
from .message_references import enrich_references


def enrich_archives(connection, items):
    if not items:
        return items
    ids = [item['id'] for item in items]
    marks = ','.join('?' for _ in ids)
    rows = connection.execute(f'''SELECT id,message_id,source_url,status,kind,
        (SELECT COUNT(*) FROM archive_resources r JOIN archive_jobs child ON child.id=r.job_id
            WHERE r.parent_id=archive_jobs.id AND child.status!='stored') AS pending_resources
        FROM archive_jobs WHERE parent_id IS NULL AND message_id IN ({marks})''', ids)
    mapped = {}
    for row in rows:
        mapped.setdefault(row['message_id'], []).append(dict(row))
    for item in items:
        item['archives'] = mapped.get(item['id'], [])
    return enrich_references(connection, items)
