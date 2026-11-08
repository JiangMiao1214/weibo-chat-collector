"""Portable, checksummed backups. Restore publishes only into a new directory."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tempfile
import zipfile
from .archive_reuse import verified_file

MAX_EXPANDED = 100 * 1024**3


def file_hash(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def write_bundle(database, root, output):
    files = {'database.sqlite3': database}
    root = Path(root)
    for path in root.rglob('*'):
        if path.is_symlink():
            raise ValueError('附件目录存在符号链接，请先检查')
        if path.is_file() and not any(part.startswith(('.part-', '.resume-')) for part in path.relative_to(root).parts):
            files['attachments/' + path.relative_to(root).as_posix()] = path
    manifest = {'version': 1, 'files': {}}
    with zipfile.ZipFile(output, 'w', allowZip64=True) as bundle:
        for name, path in files.items():
            if shutil.disk_usage(output.parent).free < path.stat().st_size + 256 * 1024**2:
                raise ValueError('备份所需磁盘空间不足')
            manifest['files'][name] = {'size': path.stat().st_size, 'sha256': file_hash(path)}
            compression = zipfile.ZIP_DEFLATED if name == 'database.sqlite3' else zipfile.ZIP_STORED
            bundle.write(path, name, compress_type=compression)
        bundle.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False))


def validate_entry(item):
    path = PurePosixPath(item.filename)
    if (path.is_absolute() or '..' in path.parts or '\\' in item.filename or ':' in item.filename
            or item.is_dir() or item.flag_bits & 1 or (item.external_attr >> 16) & 0o170000 == 0o120000):
        raise ValueError('备份包含不安全路径或条目')


def bundle_manifest(bundle):
    infos = bundle.infolist()
    names = [item.filename for item in infos]
    if len(names) > 200000 or len(names) != len(set(names)):
        raise ValueError('备份条目过多或重复')
    for item in infos:
        validate_entry(item)
    if sum(item.file_size for item in infos) > MAX_EXPANDED:
        raise ValueError('备份解压大小超过 100 GiB 上限')
    if bundle.getinfo('manifest.json').file_size > 32 * 1024**2:
        raise ValueError('备份清单过大')
    manifest = json.loads(bundle.read('manifest.json'))
    files = manifest.get('files', {})
    if manifest.get('version') != 1 or set(names) != set(files) | {'manifest.json'} or 'database.sqlite3' not in files:
        raise ValueError('备份清单无效')
    if any(name != 'database.sqlite3' and not name.startswith('attachments/') for name in files):
        raise ValueError('备份包含非数据库或附件内容')
    return files


def unpack(bundle_path, destination=None):
    try:
        with zipfile.ZipFile(bundle_path) as bundle:
            files = bundle_manifest(bundle)
            for name, expected in files.items():
                digest, size = hashlib.sha256(), 0
                target = destination / name if destination else None
                if target:
                    target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(name) as source:
                    output = target.open('xb') if target else None
                    try:
                        for chunk in iter(lambda: source.read(1024 * 1024), b''):
                            size += len(chunk)
                            if size > expected['size']:
                                raise ValueError('备份文件大小不匹配')
                            digest.update(chunk)
                            if output:
                                if shutil.disk_usage(destination).free < len(chunk) + 256 * 1024**2:
                                    raise ValueError('恢复所需磁盘空间不足')
                                output.write(chunk)
                    finally:
                        if output:
                            output.close()
                if size != expected['size'] or digest.hexdigest() != expected['sha256']:
                    raise ValueError('备份文件校验失败')
            return {'files': len(files), 'bytes': sum(item['size'] for item in files.values())}
    except (KeyError, TypeError, zipfile.BadZipFile, json.JSONDecodeError) as error:
        raise ValueError('备份格式或清单损坏') from error


def verify_bundle(bundle_path):
    return unpack(bundle_path)


def prepare_database(path):
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA trusted_schema=OFF')
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('备份数据库完整性检查失败')
        for row in db.execute("SELECT j.*,m.account_id FROM archive_jobs j JOIN messages m ON m.id=j.message_id WHERE j.status='stored'"):
            if not verified_file(path.parent / 'attachments', row):
                raise ValueError('备份中的已归档文件缺失或损坏')
        from .ocr_restore import pause_restored_ocr
        pause_restored_ocr(db)
        db.execute("UPDATE archive_groups SET paused=1,reason='备份恢复后等待手动确认'")
        db.execute("UPDATE archive_jobs SET status='queued' WHERE status='downloading'")
        db.execute('UPDATE archive_runtime SET owner=NULL,expires_at=0,next_at=0')
        db.execute("UPDATE collection_jobs SET status='stopped',stop_code='backup_restored',stop_reason='从备份恢复，等待人工确认' WHERE status IN ('queued','running','awaiting_confirmation')")
        db.execute("UPDATE collection_job_attempts SET status='stopped',finished_at=COALESCE(finished_at,CURRENT_TIMESTAMP),stop_code='backup_restored',stop_reason='从备份恢复' WHERE status='running'")
        db.execute('UPDATE collector_runtime_state SET current_job_id=NULL,worker_id=NULL,lease_expires_at=NULL')


def restore(bundle_path, destination):
    destination = Path(destination)
    if destination.exists():
        raise ValueError('恢复目标已存在，请选择新目录')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.restore-', dir=destination.parent))
    try:
        result = unpack(bundle_path, temporary)
        prepare_database(temporary / 'database.sqlite3')
        (temporary / 'attachments').mkdir(exist_ok=True)
        # Empty reservation prevents overwriting an existing user directory.
        destination.mkdir()
        try:
            os.replace(temporary, destination)
        except BaseException:
            destination.rmdir()
            raise
        return {**result, 'destination': str(destination)}
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
