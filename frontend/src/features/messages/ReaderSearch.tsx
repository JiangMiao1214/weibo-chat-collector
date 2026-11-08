import React, { useEffect, useRef, useState } from 'react';
import type { Filters, ViewMode } from '../../shared/contracts';
import { buildFilterPayload } from './model';
import { useReadRequest } from './useReadRequest';
import { RequestNotice, type MessageRef } from './ReaderNavigation';

function matchesScope(target: MessageRef, filters: Filters, viewMode: ViewMode) {
  return (!filters.accountId || String(target.account_id) === filters.accountId) &&
    (!filters.groupId || String(target.group_id) === filters.groupId) &&
    !!target.is_deleted === (viewMode === 'deleted');
}

export function ReaderSearch({filters, viewMode, query, onQuery, jump}: {filters: Filters; viewMode: ViewMode;
  query: string; onQuery: (query: string) => void; jump: (source: MessageRef) => void}) {
  const [debounced, setDebounced] = useState('');
  const [index, setIndex] = useState(-1);
  const [scopeError, setScopeError] = useState<string | null>(null);
  const pendingJump = useRef(false);
  useEffect(() => { const timer = setTimeout(() => {setDebounced(query.trim()); setIndex(-1); pendingJump.current = false;}, 200);
    return () => clearTimeout(timer); }, [query]);
  const body = JSON.stringify({filters: buildFilterPayload(filters), deleted_only: viewMode === 'deleted',
    query: debounced, offset: Math.max(0, index), limit: 1});
  const ready = debounced && debounced === query.trim();
  const state = useReadRequest<{total: number; items: MessageRef[]}>(ready ? '/api/message-view/matches' : null, body);
  const lastTotal = useRef(0);
  if (state.data) lastTotal.current = state.data.total;
  function deliver(target: MessageRef) {
    if (!matchesScope(target, filters, viewMode)) { setScopeError('搜索结果范围不匹配，已拒绝跳转'); return; }
    setScopeError(null); jump(target);
  }
  useEffect(() => {
    const target = state.data?.items[0];
    if (pendingJump.current && target) {
      pendingJump.current = false;
      deliver(target);
    }
  }, [state.data, filters.accountId, filters.groupId, viewMode, jump]);
  function navigate(direction: number) {
    const total = lastTotal.current;
    if (!total) return;
    const next = index < 0 ? (direction > 0 ? 0 : total - 1) : (index + direction + total) % total;
    pendingJump.current = true;
    if (next === Math.max(0, index) && state.data?.items[0]) {
      pendingJump.current = false; deliver(state.data.items[0]);
    }
    setIndex(next);
  }
  return <section className="reader-search" aria-label="正文查找">
    <input aria-label="正文查找（保留上下文）" placeholder="查找正文，不隐藏周围消息…" maxLength={300}
      value={query} onChange={event => onQuery(event.target.value)} />
    <span aria-label="匹配消息进度">{state.data ? `${state.data.total} 条匹配消息 · ${index < 0 ? 0 : index + 1}` : '输入文字查找'}</span>
    <button type="button" disabled={!state.data?.total || state.loading} onClick={() => navigate(-1)}>上一个匹配</button>
    <button type="button" disabled={!state.data?.total || state.loading} onClick={() => navigate(1)}>下一个匹配</button>
    <button type="button" onClick={() => onQuery('')}>清除查找</button>
    <RequestNotice {...state} />
    {scopeError && <p role="alert">{scopeError}</p>}
  </section>;
}

export function ExportResults({filters, viewMode, disabled}: {filters: Filters; viewMode: ViewMode; disabled: boolean}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  async function download() {
    setBusy(true); setError(null);
    const current = new AbortController(); controller.current = current;
    const timer = setTimeout(() => current.abort(), 60000);
    try {
      const response = await fetch('/api/message-view/export', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, signal: current.signal,
        body: JSON.stringify({filters: buildFilterPayload(filters), deleted_only: viewMode === 'deleted'})});
      if (!response.ok) throw new Error(`导出失败（${response.status}）`);
      const blob = await response.blob();
      if (current.signal.aborted) return;
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'weibo-chat-filtered.txt';
      anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (failure) { setError(failure instanceof Error ? failure.message : '导出失败'); }
    finally { clearTimeout(timer); setBusy(false); }
  }
  return <div className="export-results"><button type="button" disabled={disabled || busy} onClick={() => void download()}>
    {busy ? '正在导出…' : '导出当前筛选结果'}</button><small>全部分页 · TXT · 查找高亮不缩小导出范围</small>
    {error && <span role="alert">{error}</span>}</div>;
}
