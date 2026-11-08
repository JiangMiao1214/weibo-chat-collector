"""Single leased local OCR queue; enabled groups opt into historical and new images."""
import time
from .archive_queue import scope
from .target_management import write_lock,require_enabled
from .ocr_engine import ENGINE

JOINS='''FROM ocr_jobs o JOIN archive_jobs a ON a.id=o.archive_id JOIN messages m ON m.id=a.message_id'''
ACTIVE='''JOIN ocr_groups g ON g.group_id=m.group_id AND g.account_id=m.account_id
 JOIN weibo_accounts wa ON wa.id=m.account_id JOIN chat_groups cg ON cg.id=m.group_id
 WHERE g.paused=0 AND wa.is_active=1 AND cg.is_active=1 AND m.is_deleted=0 AND a.status='stored' '''


def discover(connection):
    connection.execute(f'''INSERT OR IGNORE INTO ocr_jobs(archive_id,content_hash)
        SELECT a.id,a.content_hash FROM archive_jobs a JOIN messages m ON m.id=a.message_id {ACTIVE}
        AND a.kind='image' AND a.local_path IS NOT NULL AND a.content_hash IS NOT NULL
        AND NOT EXISTS(SELECT 1 FROM ocr_jobs o WHERE o.archive_id=a.id) ORDER BY a.id LIMIT 100''')
    connection.execute('''UPDATE ocr_jobs SET status='queued',result_id=NULL,error_code=NULL,
        content_hash=(SELECT content_hash FROM archive_jobs WHERE id=archive_id)
        WHERE archive_id IN (SELECT id FROM archive_jobs WHERE status='stored' AND content_hash IS NOT NULL)
        AND content_hash<>(SELECT content_hash FROM archive_jobs WHERE id=archive_id)''')


def control(connection,account,group,action):
    write_lock(connection);scope(connection,account,group)
    if action in ('start','resume'):
        require_enabled(connection,account,group)
        connection.execute('''INSERT INTO ocr_groups(account_id,group_id) VALUES (?,?)
            ON CONFLICT(group_id) DO UPDATE SET paused=0''',(account,group))
        discover(connection)
    elif action=='pause':
        connection.execute('UPDATE ocr_groups SET paused=1 WHERE account_id=? AND group_id=?',(account,group))
    elif action=='retry':
        connection.execute(f'''UPDATE ocr_jobs SET status='queued',error_code=NULL WHERE status='failed' AND archive_id IN
            (SELECT o.archive_id {JOINS} WHERE m.account_id=? AND m.group_id=? AND m.is_deleted=0)''',(account,group))


def status(connection,account,group):
    scope(connection,account,group)
    config=connection.execute('SELECT * FROM ocr_groups WHERE account_id=? AND group_id=?',(account,group)).fetchone()
    where='m.account_id=? AND m.group_id=? AND m.is_deleted=0 AND a.status=\'stored\''
    counts=dict(connection.execute(f'SELECT o.status,COUNT(*) {JOINS} WHERE {where} GROUP BY o.status',(account,group)))
    eligible=connection.execute('''SELECT COUNT(*) FROM archive_jobs a JOIN messages m ON m.id=a.message_id
        WHERE m.account_id=? AND m.group_id=? AND m.is_deleted=0 AND a.status='stored' AND a.kind='image' ''',(account,group)).fetchone()[0]
    errors=[dict(r) for r in connection.execute(f'''SELECT o.archive_id,m.id AS message_id,o.error_code {JOINS}
        WHERE {where} AND o.status='failed' ORDER BY o.archive_id DESC LIMIT 20''',(account,group))]
    return {'enabled':bool(config),'paused':bool(config and config['paused']),'counts':counts,'eligible':eligible,
            'undiscovered':max(0,eligible-sum(counts.values())),'errors':errors,'concurrency':1}


def claim(connection,owner):
    write_lock(connection)
    now=time.time()
    runtime=connection.execute('SELECT * FROM ocr_runtime WHERE id=1').fetchone()
    if runtime['expires_at']>now:
        return None
    connection.execute("UPDATE ocr_jobs SET status='queued' WHERE status='processing'")
    discover(connection)
    row=connection.execute(f'''SELECT o.*,a.local_path,m.account_id,m.group_id,m.id AS message_id
        {JOINS} {ACTIVE} AND o.status='queued' ORDER BY o.archive_id LIMIT 1''').fetchone()
    if not row:
        return None
    connection.execute('UPDATE ocr_runtime SET owner=?,expires_at=? WHERE id=1',(owner,now+240))
    connection.execute("UPDATE ocr_jobs SET status='processing',updated_at=CURRENT_TIMESTAMP WHERE archive_id=?",(row['archive_id'],))
    return dict(row)


def cached(connection,job):
    return connection.execute('SELECT id,text FROM ocr_results WHERE account_id=? AND content_hash=? AND engine=?',
                              (job['account_id'],job['content_hash'],ENGINE)).fetchone()


def publish(connection,owner,job,text,error=None):
    write_lock(connection)
    owned=connection.execute('SELECT 1 FROM ocr_runtime WHERE owner=? AND expires_at>?',(owner,time.time())).fetchone()
    if not owned:
        return
    active=connection.execute(f'''SELECT 1 {JOINS} {ACTIVE} AND o.archive_id=? AND a.content_hash=?
        AND o.status='processing' ''',(job['archive_id'],job['content_hash'])).fetchone()
    if not active:
        connection.execute("UPDATE ocr_jobs SET status='queued' WHERE archive_id=?",(job['archive_id'],))
    elif error:
        connection.execute("UPDATE ocr_jobs SET status='failed',error_code=?,updated_at=CURRENT_TIMESTAMP WHERE archive_id=?",(error,job['archive_id']))
    else:
        connection.execute('INSERT OR IGNORE INTO ocr_results(account_id,content_hash,engine,text) VALUES(?,?,?,?)',
                           (job['account_id'],job['content_hash'],ENGINE,text))
        result=cached(connection,job)
        connection.execute("UPDATE ocr_jobs SET status='done',result_id=?,error_code=NULL,updated_at=CURRENT_TIMESTAMP WHERE archive_id=?",(result['id'],job['archive_id']))
    connection.execute('UPDATE ocr_runtime SET owner=NULL,expires_at=0 WHERE owner=?',(owner,))
