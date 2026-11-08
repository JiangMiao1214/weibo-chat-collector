"""One image at a time, no remote requests; pause/delete are rechecked before publish."""
import hashlib
import logging
import threading
from uuid import uuid4
from ..database import connection_context
from ..settings import get_settings
from .archive_storage import stored_path,ArchiveError
from .ocr_engine import recognize,OcrError
from .ocr_queue import claim,cached,publish


class OcrWorker:
    def __init__(self,connection_factory=connection_context,settings_provider=get_settings,recognizer=recognize):
        self.connections=connection_factory;self.settings=settings_provider;self.recognizer=recognizer
        self.owner=uuid4().hex;self.stop_event=threading.Event();self.thread=None
        self.lock=threading.Lock()

    def start(self):
        if not self.thread or not self.thread.is_alive():
            self.stop_event.clear()
            self.thread=threading.Thread(target=self.loop,name='local-ocr-worker',daemon=True)
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)

    def loop(self):
        while not self.stop_event.is_set():
            try:
                self.run_once()
            except Exception:
                logging.getLogger(__name__).error('本地 OCR 队列处理失败；不输出图片或凭据')
            self.stop_event.wait(3)

    def run_once(self):
        if not self.lock.acquire(blocking=False):
            return False
        try:
            with self.connections() as connection:
                job=claim(connection,self.owner)
            if not job:
                return False
            text,error='',None
            try:
                settings=self.settings()
                path=stored_path(settings.attachments_dir,job['local_path'],job['account_id'])
                with path.open('rb') as image:
                    if hashlib.file_digest(image,'sha256').hexdigest()!=job['content_hash']:
                        raise OcrError('hash_mismatch')
                with self.connections() as connection:
                    result=cached(connection,job)
                text=result['text'] if result else self.recognizer(path,settings)
            except OcrError as failure:
                error=str(failure) if str(failure) in {'timeout','output_limit','engine_failed','invalid_image','pixel_limit','invalid_output','hash_mismatch'} else 'local_error'
            except (ArchiveError,OSError):
                error='missing_file'
            except Exception:
                error='local_error'
            with self.connections() as connection:
                if self.stop_event.is_set():
                    connection.execute('UPDATE ocr_runtime SET expires_at=0 WHERE owner=?',(self.owner,))
                else:
                    publish(connection,self.owner,job,text,error)
            return True
        finally:
            self.lock.release()


_worker=None

def get_ocr_worker():
    global _worker
    if _worker is None:
        _worker=OcrWorker()
    return _worker
