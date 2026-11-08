"""Validator-bound byte resume for direct media; corrupt/changed sources restart."""
import json
from pathlib import Path
import re
import shutil

from .archive_network import response_chunks
from .archive_storage import ArchiveError, save_stream


def resume_state(job, root):
    if not job.get('id'):
        return None
    directory = Path(root) / 'archives' / str(job['account_id']) / f".resume-{job['id']}"
    directory.mkdir(parents=True, exist_ok=True)
    state = {'directory': directory, 'data': directory / 'body', 'url': job['source_url'], 'offset': 0, 'validator': None}
    try:
        saved = json.loads((directory / 'meta.json').read_text())
        if saved['url'] == job['source_url'] and saved['validator']:
            state.update(saved, offset=state['data'].stat().st_size)
    except (OSError, ValueError, KeyError):
        pass
    return state


def range_headers(state):
    if state and state['offset'] and state['validator']:
        return {'extra_headers': {'Range': f"bytes={state['offset']}-", 'If-Range': state['validator']}}
    return {}


def response_validator(response):
    etag = response.headers.get('ETag', '')
    return etag if etag and not etag.startswith('W/') else response.headers.get('Last-Modified')


def resume_offset(response, state, final_url, limit):
    if getattr(response, 'status', 200) != 206:
        return 0
    match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
    if not state or not state['offset'] or not match:
        raise ArchiveError('续传响应无效，将重新下载', retryable=True)
    start, end, total = map(int, match.groups())
    validator = response_validator(response)
    if (start != state['offset'] or end != total - 1 or total > limit or start >= total
            or state.get('final_url') != final_url or (validator and validator != state['validator'])):
        raise ArchiveError('续传源文件已变化或范围无效，将重新下载', retryable=True)
    return start


def file_chunks(path):
    with path.open('rb') as source:
        yield from iter(lambda: source.read(256 * 1024), b'')


def receive_binary(response, final_url, state, job, root, mime, limit, check, validate):
    if state is None:
        chunks = response_chunks(response, limit, check)
        first = next(chunks, b'')
        validate(mime, job['kind'], first)
        return save_stream(root, job['account_id'], joined(first, chunks), mime, check)
    keep = False
    try:
        offset = resume_offset(response, state, final_url, limit)
        validator = state['validator'] if offset else response_validator(response)
        # Persist metadata before writing so abrupt process exit is recoverable.
        state['validator'] = validator
        metadata = {'url': job['source_url'], 'validator': validator, 'final_url': final_url}
        (state['directory'] / 'meta.json').write_text(json.dumps(metadata))
        with state['data'].open('ab' if offset else 'wb') as output:
            chunks = response_chunks(response, limit - offset, check)
            first = next(chunks, b'')
            prefix = first
            if offset:
                with state['data'].open('rb') as source:
                    prefix = source.read(64)
            validate(mime, job['kind'], prefix)
            keep = bool(validator)
            for chunk in joined(first, chunks):
                check()
                if shutil.disk_usage(root).free < len(chunk) + 256 * 1024**2:
                    raise ArchiveError('磁盘剩余空间不足，归档已暂停', blocked=True)
                output.write(chunk)
                output.flush()
        if getattr(response, 'status', 200) == 206:
            total = int(response.headers['Content-Range'].split('/')[-1])
            if state['data'].stat().st_size != total:
                raise ArchiveError('续传长度不完整', retryable=True)
        result = save_stream(root, job['account_id'], file_chunks(state['data']), mime, check)
        keep = False
        return result
    except ArchiveError as error:
        keep = keep and error.retryable
        raise
    finally:
        if not keep:
            shutil.rmtree(state['directory'], ignore_errors=True)


def joined(first, rest):
    yield first
    yield from rest
