"""Publish a completed local archive and its associated media transactionally."""
from .archive_queue import enqueue


def publish(connection, job, result, resources):
    connection.execute('DELETE FROM archive_failures WHERE job_id=?', (job['id'],))
    fields = ('local_path', 'file_size', 'content_hash', 'mime_type')
    connection.execute('''UPDATE archive_jobs SET status='stored', error=NULL, stored_at=CURRENT_TIMESTAMP,
        local_path=?,file_size=?,content_hash=?,mime_type=? WHERE id=?''',
        (*[result[key] for key in fields], job['id']))
    connection.execute('''UPDATE attachments SET download_status='stored',downloaded_at=CURRENT_TIMESTAMP,
        local_path=?,file_size=?,content_hash=?,mime_type=? WHERE message_id=? AND source_url=? AND attachment_type=?''',
        (*[result[key] for key in fields], job['message_id'], job['source_url'], job['kind']))
    for resource in resources:
        enqueue(connection, job['message_id'], resource['url'], resource['kind'], job['id'])
