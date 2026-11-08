import {useCallback, useLayoutEffect, useRef, useState} from 'react';
import type {ActionPreview, DeletePreview, Filters, PreviewAction, ViewMode} from '../../shared/contracts';
import {API_BASE_URL} from '../../shared/http';
import {buildFilterPayload} from './model';

type Snapshot = {scope: string; revision: number; filters: ReturnType<typeof buildFilterPayload>; preview: ActionPreview};
const operations = {
  'soft-delete': {preview: 'delete-preview', label: '软删除', result: 'deleted_count'},
  restore: {preview: 'restore-preview', label: '恢复', result: 'restored_count'},
  'hard-delete': {preview: 'hard-delete-preview', label: '彻底删除', result: 'deleted_count'},
} as const;

async function readPreview(path: string, filters: Snapshot['filters'], controller: AbortController) {
  let timer: ReturnType<typeof setTimeout>;
  try {
    const deadline = new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error('操作预览超时，请重新预览')), 15000);
      controller.signal.addEventListener('abort', () => reject(new Error('预览已取消')), {once: true});
    });
    return await Promise.race([deadline, (async () => {
      const response = await fetch(API_BASE_URL + path, {method: 'POST', signal: controller.signal,
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({filters})});
      if (!response.ok) throw new Error('操作预览失败');
      return await response.json() as DeletePreview;
    })()]);
  } finally { clearTimeout(timer!); controller.abort(); }
}

/** Preview identity covers operation, normalized scope, view and each invalidation. */
export function useBatchActions(filters: Filters, view: ViewMode, setError: (error: string | null) => void,
                                onSuccess: () => Promise<void>) {
  const scope = JSON.stringify([view, buildFilterPayload(filters)]);
  const latestScope = useRef(scope);
  latestScope.current = scope;
  const revision = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const accepted = useRef<Snapshot | null>(null);
  const writing = useRef(false);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [pending, setPending] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [deleteResult, setDeleteResult] = useState<string | null>(null);

  const invalidatePreview = useCallback(() => {
    revision.current += 1;
    controller.current?.abort();
    accepted.current = null;
    setSnapshot(null); setPending(false); setDeleteResult(null);
  }, []);
  useLayoutEffect(() => {
    invalidatePreview();
    return () => { revision.current += 1; controller.current?.abort(); };
  }, [scope, invalidatePreview]);

  function current(candidate: Pick<Snapshot, 'scope' | 'revision'>) {
    return candidate.scope === latestScope.current && candidate.revision === revision.current;
  }
  function allowed(action: PreviewAction) {
    return action === 'soft-delete' ? view === 'active' : view === 'deleted';
  }

  async function previewAction(action: PreviewAction) {
    if (writing.current || !allowed(action)) return;
    invalidatePreview(); setError(null); setPending(true);
    const request = {scope, revision: revision.current, filters: buildFilterPayload(filters)};
    const abort = new AbortController(); controller.current = abort;
    try {
      const data = await readPreview(`/api/messages/${operations[action].preview}`, request.filters, abort);
      if (!current(request)) return;
      const next = {...request, preview: {...data, action}};
      accepted.current = next; setSnapshot(next);
    } catch (error) {
      if (current(request)) setError(error instanceof Error ? error.message : '操作预览失败');
    } finally {
      if (current(request)) setPending(false);
    }
  }

  async function execute(action: PreviewAction) {
    const candidate = accepted.current;
    if (writing.current || !candidate || !current(candidate) || !allowed(action)
        || candidate.preview.action !== action || candidate.preview.preview_count <= 0) return;
    const operation = operations[action];
    const warning = action === 'hard-delete' ? '条已删除消息？此操作不可恢复。' : '条消息？';
    if (!window.confirm(`确认${operation.label}当前搜索条件下的 ${candidate.preview.preview_count} ${warning}`)) return;
    // Recheck after confirmation, then consume the preview before sending a write.
    if (!current(candidate) || accepted.current !== candidate) return;
    writing.current = true; setExecuting(true); setError(null);
    accepted.current = null; setSnapshot(null);
    try {
      const response = await fetch(`${API_BASE_URL}/api/messages/${action}`, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({filters: candidate.filters, confirm: true, created_by: 'local_user',
          ...(action === 'soft-delete' ? {delete_attachments: false} : {}),
          ...(action === 'hard-delete' ? {delete_attachment_files: false} : {})}),
      });
      if (!response.ok) throw new Error(`${operation.label}失败，请重新预览后再操作`);
      const result = await response.json() as Record<string, number>;
      if (!current(candidate)) return;
      setDeleteResult(`已${operation.label} ${result[operation.result]} 条消息${action === 'hard-delete' ? '记录' : ''}`);
      await onSuccess();
    } catch (error) {
      if (current(candidate)) setError(error instanceof Error ? error.message : '请求失败，请重新预览');
    } finally { writing.current = false; setExecuting(false); }
  }

  return {actionPreview: snapshot && current(snapshot) ? snapshot.preview : null,
    deleting: pending || executing, deleteResult, invalidatePreview, previewAction,
    executeSoftDelete: () => execute('soft-delete'), executeRestore: () => execute('restore'),
    executeHardDelete: () => execute('hard-delete')};
}
