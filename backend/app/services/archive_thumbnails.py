"""Derived previews only; originals are never resized or overwritten."""
import os
from pathlib import Path
import tempfile
import warnings
from PIL import Image, ImageOps, UnidentifiedImageError
from fastapi import HTTPException
from fastapi.responses import FileResponse
from .archive_storage import stored_path, ArchiveError


def thumbnail(job, root):
    if job['status'] != 'stored' or job['kind'] != 'image':
        raise HTTPException(409, '图片尚未保存本地')
    try:
        source = stored_path(root, job['local_path'] or '', job['account_id'])
        name = source.name
        if len(name) != 64 or any(ch not in '0123456789abcdef' for ch in name):
            raise ArchiveError('本地图片路径无效')
        cache = Path(root) / 'thumbnails' / str(job['account_id'])
        cache.mkdir(parents=True, exist_ok=True)
        target = cache / (name + '.jpg')
        if not target.exists():
            create_thumbnail(source, target)
        return FileResponse(target, media_type='image/jpeg', headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})
    except (ArchiveError, OSError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(422, '无法生成缩略图，可尝试打开原图') from None


def create_thumbnail(source, target):
    fd, temporary = tempfile.mkstemp(prefix='.thumb-', dir=target.parent)
    os.close(fd)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(source) as original:
                image = ImageOps.exif_transpose(original)
                image.thumbnail((320, 320))
                if image.mode != 'RGB':
                    image = image.convert('RGB')
                image.save(temporary, format='JPEG', quality=80, optimize=True)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
