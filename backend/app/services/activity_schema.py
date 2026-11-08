"""Transactional state events contain machine metadata only, never exception/Cookie text."""
SCHEMA = '''
CREATE TABLE IF NOT EXISTS activity_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 account_id INTEGER NOT NULL, group_id INTEGER NOT NULL, category TEXT NOT NULL,
 task_id INTEGER, message_id INTEGER, action TEXT NOT NULL, result TEXT NOT NULL, error_code TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_activity_scope ON activity_events(account_id,group_id,id);
CREATE INDEX IF NOT EXISTS idx_activity_time ON activity_events(occurred_at,id);
CREATE TRIGGER IF NOT EXISTS activity_retention AFTER INSERT ON activity_events BEGIN
 DELETE FROM activity_events WHERE id <= NEW.id - 10000;
 DELETE FROM activity_events WHERE occurred_at < datetime('now','-90 days');
END;
CREATE TRIGGER IF NOT EXISTS activity_collection_created AFTER INSERT ON collection_jobs BEGIN
 INSERT INTO activity_events(account_id,group_id,category,task_id,action,result)
 VALUES(NEW.account_id,NEW.group_id,'collection',NEW.id,'created',NEW.status);
END;
CREATE TRIGGER IF NOT EXISTS activity_collection_state AFTER UPDATE OF status ON collection_jobs
WHEN OLD.status <> NEW.status BEGIN
 INSERT INTO activity_events(account_id,group_id,category,task_id,action,result,error_code)
 VALUES(NEW.account_id,NEW.group_id,'collection',NEW.id,
 CASE WHEN NEW.status='running' THEN 'start' WHEN NEW.status='queued' THEN 'resume'
 WHEN NEW.status='stopped' THEN 'stop' WHEN NEW.status='completed' THEN 'finish' ELSE 'state' END,NEW.status,
 CASE WHEN NEW.stop_code NOT GLOB '*[^a-z0-9_-]*' THEN coalesce(NEW.stop_code,'') ELSE 'error' END);
END;
CREATE TRIGGER IF NOT EXISTS activity_collection_stop_request AFTER UPDATE OF stop_requested_at ON collection_jobs
WHEN OLD.stop_requested_at IS NULL AND NEW.stop_requested_at IS NOT NULL BEGIN
 INSERT INTO activity_events(account_id,group_id,category,task_id,action,result)
 VALUES(NEW.account_id,NEW.group_id,'collection',NEW.id,'stop_requested',NEW.status);
END;
CREATE TRIGGER IF NOT EXISTS activity_archive_created AFTER INSERT ON archive_jobs BEGIN
 INSERT INTO activity_events(account_id,group_id,category,task_id,message_id,action,result)
 SELECT account_id,group_id,'archive',NEW.id,id,'created',NEW.status FROM messages WHERE id=NEW.message_id;
END;
CREATE TRIGGER IF NOT EXISTS activity_archive_state AFTER UPDATE OF status ON archive_jobs
WHEN OLD.status <> NEW.status BEGIN
 INSERT INTO activity_events(account_id,group_id,category,task_id,message_id,action,result,error_code)
 SELECT account_id,group_id,'archive',NEW.id,id,
 CASE NEW.status WHEN 'downloading' THEN 'start' WHEN 'queued' THEN 'retry' WHEN 'skipped' THEN 'skip'
 WHEN 'stored' THEN 'finish' ELSE 'stop' END,NEW.status,
 CASE WHEN NEW.status='blocked' THEN 'account_protection' WHEN NEW.status='failed' THEN
 CASE WHEN NEW.error LIKE '%Fake-IP%' THEN 'dns_fake_ip'
 WHEN NEW.error LIKE '%HTTP 400%' THEN 'http_400' WHEN NEW.error LIKE '%HTTP 403%' THEN 'http_403'
 WHEN NEW.error LIKE '%HTTP 404%' THEN 'http_404' WHEN NEW.error LIKE '%HTTP 412%' THEN 'http_412'
 WHEN NEW.error LIKE '%正文%' THEN 'page_content' WHEN NEW.error LIKE '%图片%' THEN 'image_content'
 ELSE 'resource_failure' END ELSE '' END
 FROM messages WHERE id=NEW.message_id;
END;
CREATE TRIGGER IF NOT EXISTS activity_archive_control AFTER UPDATE OF paused ON archive_groups
WHEN OLD.paused <> NEW.paused BEGIN
 INSERT INTO activity_events(account_id,group_id,category,action,result,error_code)
 VALUES(NEW.account_id,NEW.group_id,'archive',CASE NEW.paused WHEN 1 THEN 'pause' ELSE 'resume' END,
 CASE NEW.paused WHEN 1 THEN 'paused' ELSE 'running' END,
 CASE WHEN NEW.paused=1 AND NEW.reason LIKE '%磁盘%' THEN 'disk_space' WHEN NEW.paused=1 AND NEW.reason NOT LIKE '%手动%' THEN 'account_protection' ELSE '' END);
END;
CREATE TRIGGER IF NOT EXISTS activity_collection_page AFTER INSERT ON collection_job_pages BEGIN
 INSERT INTO activity_events(account_id,group_id,category,task_id,action,result)
 SELECT account_id,group_id,'collection',id,'page','committed' FROM collection_jobs WHERE id=NEW.job_id;
END;
CREATE TRIGGER IF NOT EXISTS activity_ocr_state AFTER UPDATE OF status ON ocr_jobs
WHEN OLD.status <> NEW.status BEGIN
 INSERT INTO activity_events(account_id,group_id,category,task_id,message_id,action,result,error_code)
 SELECT m.account_id,m.group_id,'ocr',NEW.archive_id,m.id,
 CASE NEW.status WHEN 'processing' THEN 'start' WHEN 'done' THEN 'finish' WHEN 'queued' THEN 'retry' ELSE 'stop' END,
 NEW.status,coalesce(NEW.error_code,'') FROM messages m JOIN archive_jobs a ON a.message_id=m.id WHERE a.id=NEW.archive_id;
END;
CREATE TRIGGER IF NOT EXISTS activity_ocr_created AFTER INSERT ON ocr_groups BEGIN
 INSERT INTO activity_events(account_id,group_id,category,action,result)
 VALUES(NEW.account_id,NEW.group_id,'ocr','start','running');
END;
CREATE TRIGGER IF NOT EXISTS activity_ocr_control AFTER UPDATE OF paused ON ocr_groups
WHEN OLD.paused<>NEW.paused BEGIN
 INSERT INTO activity_events(account_id,group_id,category,action,result)
 VALUES(NEW.account_id,NEW.group_id,'ocr',CASE NEW.paused WHEN 1 THEN 'pause' ELSE 'resume' END,
 CASE NEW.paused WHEN 1 THEN 'paused' ELSE 'running' END);
END;
CREATE TABLE IF NOT EXISTS activity_migrations (version INTEGER PRIMARY KEY);
'''


def install_activity_schema(connection):
    connection.executescript(SCHEMA)
    if connection.execute('SELECT 1 FROM activity_migrations WHERE version=1').fetchone():
        return
    # Only a current-state baseline can be reconstructed for old archives; never invent past transitions.
    connection.execute('''INSERT INTO activity_events(account_id,group_id,category,task_id,action,result)
        SELECT account_id,group_id,'collection',id,'baseline',status FROM collection_jobs ORDER BY id DESC LIMIT 2000''')
    connection.execute('''INSERT INTO activity_events(account_id,group_id,category,task_id,message_id,action,result)
        SELECT m.account_id,m.group_id,'archive',a.id,m.id,'baseline',a.status FROM archive_jobs a
        JOIN messages m ON m.id=a.message_id ORDER BY a.id DESC LIMIT 2000''')
    connection.execute('INSERT INTO activity_migrations VALUES(1)')
