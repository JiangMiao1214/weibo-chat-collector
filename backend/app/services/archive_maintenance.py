"""Quiet-window maintenance with preview tokens; never delete referenced originals."""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time

from .archive_bundles import write_bundle, verify_bundle, restore
from .archive_reuse import verified_file


def quiet_lock(connection):
    connection.execute('UPDATE archive_runtime SET next_at=next_at WHERE id=1')
    enabled = connection.execute('SELECT 1 FROM archive_groups WHERE paused=0 LIMIT 1').fetchone()
    worker = connection.execute('SELECT 1 FROM archive_runtime WHERE owner IS NOT NULL AND expires_at>?', (time.time(),)).fetchone()
    collecting = connection.execute("SELECT 1 FROM collection_jobs WHERE status IN ('queued','running','awaiting_confirmation') LIMIT 1").fetchone()
    if enabled or worker or collecting:
        raise ValueError('请先暂停全部归档并停止采集，等待当前下载结束后重试')


def backup(connection, root, output):
    quiet_lock(connection)
    output = Path(output)
    if output.exists() or output.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError('备份文件已存在或位于附件目录内')
    for row in connection.execute("SELECT j.*,m.account_id FROM archive_jobs j JOIN messages m ON m.id=j.message_id WHERE j.status='stored'"):
        if not verified_file(root, row):
            raise ValueError('存在缺失或损坏的已归档文件，请先修复再备份')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.backup-', dir=output.parent) as temp:
        database = Path(temp) / 'database.sqlite3'
        source_path = connection.execute('PRAGMA database_list').fetchone()[2]
        if source_path:
            with sqlite3.connect(f'file:{Path(source_path).as_posix()}?mode=ro', uri=True) as source, sqlite3.connect(database) as target:
                source.backup(target)
        else:
            database.write_bytes(connection.serialize())
        staged = Path(temp) / 'backup.zip'
        write_bundle(database, root, staged)
        result = verify_bundle(staged)
        # Link publishes without overwriting an existing destination.
        os.link(staged, output)
        return {**result, 'path': str(output)}


def cleanup_candidates(connection, root):
    root = Path(root)
    if (root / 'archives').is_symlink():
        raise ValueError('归档根目录不能是符号链接')
    referenced = {row[0] for row in connection.execute('''SELECT local_path FROM archive_jobs WHERE local_path IS NOT NULL
        UNION SELECT local_path FROM attachments WHERE local_path IS NOT NULL''')}
    referenced = {(root / name).resolve() for name in referenced}
    candidates = []
    for path in (root / 'archives').rglob('*'):
        if path.is_symlink() or not path.is_file() or path.resolve() in referenced:
            continue
        relative = path.relative_to(root).as_posix()
        original = re.fullmatch(r'archives/\d+/[0-9a-f]{2}/[0-9a-f]{64}', relative)
        temporary = path.name.startswith('.part-') or re.fullmatch(r'archives/\d+/\.resume-\d+/(body|meta.json)', relative)
        stale = temporary and time.time() - path.stat().st_mtime >= 86400
        if original or stale:
            candidates.append((relative, path.stat().st_size, path.stat().st_mtime_ns))
    return sorted(candidates)


def cleanup(connection, root, confirm=None):
    quiet_lock(connection)
    candidates = cleanup_candidates(connection, root)
    token = hashlib.sha256(json.dumps(candidates).encode()).hexdigest()
    if confirm is not None and confirm != token:
        raise ValueError('文件状态已变化，请重新预览清理范围')
    removed = 0
    if confirm is not None:
        for relative, _, _ in candidates:
            path = Path(root) / relative
            path.unlink()
            if path.parent.name.startswith('.resume-') and not any(path.parent.iterdir()):
                path.parent.rmdir()
            removed += 1
    return {'count': len(candidates), 'bytes': sum(item[1] for item in candidates), 'token': token, 'removed': removed}
