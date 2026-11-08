"""Serialize local browser starts against target lifecycle changes in this process."""
import threading

# A plain Lock may be released by FastAPI's dependency cleanup worker thread.
_lifecycle_lock = threading.Lock()


def lifecycle_guard():
    with _lifecycle_lock:
        yield
