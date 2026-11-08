"""Native macOS Vision with an optional local Tesseract fallback. No image uploads."""
import hashlib
import json
import platform
from pathlib import Path
import shutil
import subprocess
import threading
import tempfile
from PIL import Image, UnidentifiedImageError
from ..settings import PROJECT_ROOT

ENGINE='vision-zh-v1' if platform.system()=='Darwin' else 'tesseract-chi-sim-v1'
BUILD_LOCK=threading.Lock()


class OcrError(Exception):
    pass


def run_command(command,timeout=60):
    try:
        completed=subprocess.run(command,capture_output=True,timeout=timeout,check=True)
        if len(completed.stdout)>1024*1024:
            raise OcrError('output_limit')
        return completed.stdout.decode('utf-8')
    except subprocess.TimeoutExpired:
        raise OcrError('timeout') from None
    except (OSError,subprocess.CalledProcessError,UnicodeError):
        raise OcrError('engine_failed') from None


def vision_binary(settings):
    source=PROJECT_ROOT/'scripts'/'ocr_vision.m'
    digest=hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    folder=settings.database_path.parent/'ocr-runtime'
    binary=folder/f'vision-{digest}'
    with BUILD_LOCK:
        if not binary.is_file():
            folder.mkdir(parents=True,exist_ok=True)
            with tempfile.NamedTemporaryFile(prefix='.build-',dir=folder,delete=False) as staging:
                temporary=Path(staging.name)
            try:
                run_command(['/usr/bin/clang','-fobjc-arc','-framework','Foundation','-framework','Vision',str(source),'-o',str(temporary)],120)
                temporary.replace(binary)
            finally:
                temporary.unlink(missing_ok=True)
    return binary


def prepare_engine(settings):
    if platform.system()=='Darwin':
        binary=vision_binary(settings)
        run_command([str(binary),'--check'])
        return ENGINE
    executable=shutil.which('tesseract')
    if not executable or 'chi_sim' not in run_command([executable,'--list-langs']):
        raise OcrError('engine_unavailable')
    return ENGINE


def engine_status(settings):
    if platform.system()=='Darwin':
        available=Path('/usr/bin/clang').is_file()
        return {'engine':ENGINE,'available':available,'setup':'使用 macOS 本地 Vision；首次开启会编译本地助手，需要 Xcode Command Line Tools。'}
    return {'engine':ENGINE,'available':bool(shutil.which('tesseract')),
            'setup':'需安装本地 Tesseract 及 chi_sim、eng 语言包；启用时校验。'}


def recognize(path,settings):
    try:
        with Image.open(path) as image:
            if image.width*image.height>40_000_000:
                raise OcrError('pixel_limit')
            image.verify()
    except (OSError,UnidentifiedImageError,Image.DecompressionBombError):
        raise OcrError('invalid_image') from None
    if platform.system()=='Darwin':
        raw=run_command([str(vision_binary(settings)),str(path)])
        try:
            return str(json.loads(raw)['text'])[:100000]
        except (ValueError,KeyError):
            raise OcrError('invalid_output') from None
    return run_command(['tesseract',str(path),'stdout','-l','chi_sim+eng','--psm','3'])[:100000]
