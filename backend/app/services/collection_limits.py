"""Per-attempt recovery caps preserve the normal worker's stop/resume protections."""
from copy import copy


def job_settings(settings, job):
    cap = job.get('recovery_max_pages') or settings.weibo_api_max_pages
    result = copy(settings)
    result.weibo_api_max_pages = min(cap, settings.weibo_api_max_pages)
    return result


def require_account_cooldown_clear(connection, account_id):
    from fastapi import HTTPException
    active=connection.execute('''SELECT resume_not_before FROM collection_jobs
        WHERE account_id=? AND julianday(resume_not_before)>julianday('now') LIMIT 1''',(account_id,)).fetchone()
    if active:
        raise HTTPException(409,f'此账号仍处于风险冷却期，最早恢复时间：{active[0]} UTC')


def validate_recovery_start(connection,job):
    if job.get('recovery_max_pages'):
        require_account_cooldown_clear(connection,job['account_id'])
