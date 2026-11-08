"""Bounded unencrypted VOD HLS: concatenate TS or single-init fragmented MP4."""
import re
import time
from urllib.parse import urljoin
from .archive_network import response_chunks
from .archive_storage import ArchiveError, save_stream

HLS_MIMES = {'application/vnd.apple.mpegurl', 'application/x-mpegurl', 'audio/mpegurl', 'audio/x-mpegurl'}


def playlist_variant(lines, base):
    variants = []
    for index, line in enumerate(lines[:-1]):
        if line.startswith('#EXT-X-STREAM-INF:'):
            bandwidth = re.search(r'(?:[:,])BANDWIDTH=(\d+)', line)
            if not bandwidth or lines[index+1].startswith('#'):
                raise ArchiveError('HLS 清晰度列表不完整')
            variants.append((int(bandwidth[1]), urljoin(base, lines[index+1])))
    return min(variants)[1] if variants else None


def parse_playlist(body, base):
    lines = [line.strip() for line in body.decode('utf-8-sig').splitlines() if line.strip()]
    if not lines or lines[0] != '#EXTM3U':
        raise ArchiveError('HLS 播放列表无效')
    unsupported = ('#EXT-X-KEY:', '#EXT-X-SESSION-KEY:', '#EXT-X-BYTERANGE:', '#EXT-X-DISCONTINUITY', '#EXT-X-GAP', '#EXT-X-MEDIA:')
    if any(line.startswith(unsupported) for line in lines):
        raise ArchiveError('此 HLS 包含加密、分离音轨或不支持的分段结构，未保存')
    variant = playlist_variant(lines, base)
    if variant:
        return {'variant': variant}
    if '#EXT-X-ENDLIST' not in lines:
        raise ArchiveError('直播或未结束的 HLS 不支持完整归档')
    segments = [urljoin(base, line) for line in lines if not line.startswith('#')]
    durations = [line for line in lines if line.startswith('#EXTINF:')]
    maps = [line for line in lines if line.startswith('#EXT-X-MAP:')]
    if not segments or len(segments) > 100 or len(durations) != len(segments) or len(maps) > 1:
        raise ArchiveError('HLS 分段缺失或超过 100 段上限')
    if maps:
        match = re.fullmatch(r'#EXT-X-MAP:URI="([^"]+)"', maps[0])
        if not match:
            raise ArchiveError('HLS 初始化分段结构不支持')
        segments.insert(0, urljoin(base, match[1]))
    return {'segments': segments, 'mime': 'video/mp4' if maps else 'video/mp2t'}


def segment_bytes(url, job, settings, check, opener, limit, mime, index):
    with opener(url, job['account_id'], settings.weibo_api_auth_dir, check) as (response, _):
        # Each segment is bounded; aggregate size is checked by the caller as well.
        body = b''.join(response_chunks(response, limit, check))
    if mime == 'video/mp2t':
        valid = bool(body) and len(body) % 188 == 0 and all(body[pos] == 0x47 for pos in range(0, len(body), 188))
    else:
        valid = body[4:8] in ((b'ftyp',) if index == 0 else (b'styp', b'moof', b'sidx'))
    if not valid:
        raise ArchiveError('HLS 分段未返回有效媒体，未保存')
    return body


def save_hls(body, final_url, job, settings, check, opener, limit, depth=0):
    if depth > 2:
        raise ArchiveError('HLS 清晰度列表嵌套过深')
    playlist = parse_playlist(body, final_url)
    if 'variant' in playlist:
        with opener(playlist['variant'], job['account_id'], settings.weibo_api_auth_dir, check) as (response, url):
            variant = b''.join(response_chunks(response, 2 * 1024**2, check))
        return save_hls(variant, url, job, settings, check, opener, limit, depth+1)
    def chunks():
        total = 0
        for index, url in enumerate(playlist['segments']):
            check()
            if index:
                time.sleep(1)
                check()
            body = segment_bytes(url, job, settings, check, opener, limit-total, playlist['mime'], index)
            total += len(body)
            if total > limit:
                raise ArchiveError('视频超过大小上限')
            yield body
    return save_stream(settings.attachments_dir, job['account_id'], chunks(), playlist['mime'], check), []
