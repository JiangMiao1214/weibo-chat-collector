"""Old backups may have no OCR tables; new copies must never auto-start recognition."""
def pause_restored_ocr(connection):
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ocr_groups'").fetchone():
        return
    connection.execute('UPDATE ocr_groups SET paused=1')
    connection.execute("UPDATE ocr_jobs SET status='queued' WHERE status='processing'")
    connection.execute('UPDATE ocr_runtime SET owner=NULL,expires_at=0')
