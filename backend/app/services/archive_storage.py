"""Atomic content-addressed files, scoped to the owning account."""
import hashlib
import os
from pathlib import Path
import shutil
import tempfile


class ArchiveError(Exception):
    def __init__(self, message, *, blocked=False, retryable=False):
        super().__init__(message)
        self.blocked = blocked
        self.retryable = retryable


def stored_path(root, relative, account_id=None):
    base = (Path(root) / 'archives' / str(account_id)).resolve() if account_id is not None else (Path(root) / 'archives').resolve()
    path = (Path(root) / relative).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise ArchiveError('本地文件缺失，请恢复备份或重新下载')
    return path


def save_stream(root, account_id, chunks, mime, check=lambda: None):
    base = Path(root) / 'archives' / str(account_id)
    base.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    fd, temporary = tempfile.mkstemp(prefix='.part-', dir=base)
    try:
        with os.fdopen(fd, 'wb') as output:
            for chunk in chunks:
                check()
                if shutil.disk_usage(base).free < len(chunk) + 256 * 1024 * 1024:
                    raise ArchiveError('磁盘剩余空间不足，归档已暂停', blocked=True)
                output.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            output.flush()
            os.fsync(output.fileno())
        if not size:
            raise ArchiveError('源站返回空文件')
        content_hash = digest.hexdigest()
        target = base / content_hash[:2] / content_hash
        target.parent.mkdir(exist_ok=True)
        if target.exists():
            # Verify before reusing, so corrupt earlier content is repaired by this download.
            with target.open('rb') as existing:
                valid = hashlib.file_digest(existing, 'sha256').hexdigest() == content_hash
            if not valid:
                os.replace(temporary, target)
        else:
            os.replace(temporary, target)
        return {'local_path': str(target.relative_to(root)), 'file_size': size,
                'content_hash': content_hash, 'mime_type': mime}
    finally:
        Path(temporary).unlink(missing_ok=True)
