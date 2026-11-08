"""Bounded OS lock stripes prevent expired lease owners sharing resume files."""
from contextlib import contextmanager
import os
from pathlib import Path
from .archive_storage import ArchiveError


def lock_file(file, unlock=False):
    if os.name == 'nt':
        import msvcrt
        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK if unlock else msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(file.fileno(), fcntl.LOCK_UN if unlock else fcntl.LOCK_EX | fcntl.LOCK_NB)


@contextmanager
def job_lock(root, job):
    if not job.get('id'):
        yield
        return
    directory = Path(root) / 'archives' / str(job['account_id']) / '.resume-locks'
    directory.mkdir(parents=True, exist_ok=True)
    # Stable files are never removed while workers may still hold OS handles.
    with (directory / f"{job['id'] % 256:02x}").open('a+b') as file:
        if file.tell() == 0:
            file.write(b'0')
            file.flush()
        try:
            lock_file(file)
        except OSError:
            raise ArchiveError('下载临时文件正被其他进程占用，稍后重试', retryable=True) from None
        try:
            yield
        finally:
            lock_file(file, unlock=True)
