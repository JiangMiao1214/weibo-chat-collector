"""Local-only aggregates; absent dates are unknown, never filled with invented zeroes."""
from datetime import date
from fastapi import HTTPException
from .archive_storage import stored_path, ArchiveError


def scope_conditions(connection, account_id=None, group_id=None, date_from=None, date_to=None,
                     deleted_only=False, alias='m', time_column='sent_at'):
    if account_id and group_id and not connection.execute(
        'SELECT 1 FROM chat_groups WHERE id=? AND account_id=?', (group_id,account_id)).fetchone():
        raise HTTPException(404, '群不属于所选账号')
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, '开始日期不能晚于结束日期')
    conditions, params = [], []
    if deleted_only is not None:
        conditions.append(f'{alias}.is_deleted=?'); params.append(int(deleted_only))
    for field, value in [('account_id',account_id),('group_id',group_id)]:
        if value is not None:
            conditions.append(f'{alias}.{field}=?'); params.append(value)
    for value, operator, suffix in [(date_from,'>=',' 00:00:00'),(date_to,'<=',' 23:59:59.999999')]:
        if value:
            conditions.append(f'{alias}.{time_column}{operator}?'); params.append(str(value)+suffix)
    return ' AND '.join(conditions) or '1=1', params


def storage_usage(connection, where, params, root):
    files, missing, kinds = {}, set(), {}
    for row in connection.execute(f'''SELECT DISTINCT m.account_id,a.local_path,a.kind FROM archive_jobs a
        JOIN messages m ON m.id=a.message_id WHERE {where} AND a.status='stored' AND a.local_path IS NOT NULL''',params):
        try:
            path=stored_path(root,row['local_path'],row['account_id'])
            if path not in files:
                files[path]=path.stat().st_size
            kinds.setdefault(row['kind'],set()).add(path)
        except (ArchiveError,OSError):
            missing.add(row['local_path'])
    return {'files':len(files),'bytes':sum(files.values()),'missing_paths':len(missing),
            'by_kind':{kind:{'files':len(paths),'bytes':sum(files[path] for path in paths)} for kind,paths in kinds.items()}}


def dashboard(connection, where, params, period, root):
    bucket={'day':'date(m.sent_at)','week':"date(m.sent_at, '-6 days', 'weekday 1')",'month':"strftime('%Y-%m-01',m.sent_at)"}[period]
    query=f'FROM messages m WHERE {where}'
    count=connection.execute(f'SELECT COUNT(*) {query}',params).fetchone()[0]
    trend=[dict(r) for r in connection.execute(f'''SELECT {bucket} AS date, COUNT(*) AS count,
        COUNT(DISTINCT user_id) AS members {query} GROUP BY 1 ORDER BY 1''',params)]
    types=[dict(r) for r in connection.execute(f'SELECT message_type AS name,COUNT(*) AS count {query} GROUP BY 1 ORDER BY count DESC,name',params)]
    members=[dict(r) for r in connection.execute(f'''SELECT m.user_id,cu.display_name,COUNT(*) AS count FROM messages m
        JOIN chat_users cu ON cu.id=m.user_id WHERE {where} GROUP BY m.user_id ORDER BY count DESC,m.user_id LIMIT 50''',params)]
    archive_query=f'FROM archive_jobs a JOIN messages m ON m.id=a.message_id WHERE {where}'
    archive={r[0]:r[1] for r in connection.execute(f'SELECT a.status,COUNT(*) {archive_query} GROUP BY a.status',params)}
    pages=connection.execute(f'''SELECT COUNT(*),coalesce(SUM(NOT EXISTS(SELECT 1 FROM archive_resources ar
        JOIN archive_jobs child ON child.id=ar.job_id WHERE ar.parent_id=a.id AND child.status<>'stored')),0)
        {archive_query} AND a.kind='link' AND a.status='stored' ''',params).fetchone()
    return {'message_count':count,'trend':trend,'types':types,'members':members,'archives':archive,
            'pages':{'text_stored':pages[0],'media_complete':pages[1]},
            'storage':storage_usage(connection,where,params,root),
            'coverage_note':'仅统计本地已入库消息；缺少日期表示未知，不代表该日没有发言。媒体完整只覆盖已识别的关联资源。'}
