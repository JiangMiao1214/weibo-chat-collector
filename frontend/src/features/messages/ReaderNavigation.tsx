import {ContinuousReader} from "./ContinuousReader";
import React, { useEffect, useRef, useState } from 'react';
import type { Message } from '../../shared/contracts';
import { useReadRequest } from './useReadRequest';
import { focusMessageInList } from './scrollMessage';

import type {MessageRef,ContextData} from "./contextTypes";
export type {MessageRef,ContextData} from "./contextTypes";

function useContext(source: MessageRef | null, direction: 'center' | 'older' | 'newer' = 'center') {
  const path = source ? `/api/message-view/${source.id}/context?account_id=${source.account_id}&group_id=${source.group_id}&deleted_only=${!!source.is_deleted}${direction === 'center' ? '' : '&direction=' + direction}` : null;
  const result = useReadRequest<ContextData>(path);
  const data = result.data;
  const valid = source && data && data.target_id === source.id && data.account_id === source.account_id &&
    data.group_id === source.group_id && data.deleted_only === !!source.is_deleted &&
    Array.isArray(data.items) && (direction !== 'center' || data.items.some(item => item.id === source.id)) && data.items.every(item => item.account_id === source.account_id &&
      item.group_id === source.group_id && !!item.is_deleted === !!source.is_deleted);
  return {...result, data: valid ? data : null, error: data && !valid ? '上下文范围不匹配，已拒绝展示' : result.error};
}

export function RequestNotice({loading, error, retry}: {loading: boolean; error: string | null; retry: () => void}) {
  if (error) return <p role="alert">{error} <button type="button" onClick={retry}>重试</button></p>;
  return loading ? <p role="status">读取中…</p> : null;
}

export function ContextPanel({source, close, jump}: {source: MessageRef; close: () => void; jump: (source: MessageRef) => void}) {
  const closeButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeButton.current?.focus({preventScroll: true});
  }, [source.id]);
  return <aside className="detail-panel reader-context" aria-label="消息上下文"
    onKeyDown={event => {if (event.key === 'Escape') {event.preventDefault(); close();}}}>
    <div className="panel-title"><h2>上下文</h2><button ref={closeButton} type="button" onClick={close}>关闭上下文</button></div>
    <p className="muted">同群上下文 · 不受列表筛选限制</p>
    <ContinuousReader source={source} jump={jump} />
  </aside>;
}

export function useReaderNavigation(messages: Message[], selected: number | null, select: (id: number) => void) {
  const [source, setSource] = useState<MessageRef | null>(null);
  const [location, setLocation] = useState<MessageRef | null>(null);
  const focused = useContext(location);
  const root = useRef<HTMLElement | null>(null);
  const snapshot = useRef<{scroll: number; selected: number | null; focus: HTMLElement | null} | null>(null);
  const returning = useRef(false);
  function position(id: number) {
    const button = root.current?.querySelector<HTMLButtonElement>(`button[aria-label="查看消息 #${id}"]`);
    focusMessageInList(button);
  }
  function jump(target: MessageRef) {
    setSource(null);
    if (!location && messages.some(item => item.id === target.id)) {
      select(target.id); position(target.id); return;
    }
    if (!snapshot.current) snapshot.current = {scroll: root.current?.querySelector('.message-list')?.scrollTop ?? 0,
      selected, focus: document.activeElement as HTMLElement};
    setLocation(target);
  }
  function restore() { returning.current = true; setLocation(null); setSource(null); }
  function closeContext() {
    const button = root.current?.querySelector<HTMLButtonElement>(`button[aria-label="上下文 #${source?.id}"]`);
    setSource(null); button?.focus({preventScroll: true});
  }
  useEffect(() => {
    if (focused.data && location) { select(location.id); position(location.id); }
    if (returning.current && snapshot.current) {
      const saved = snapshot.current;
      const list = root.current?.querySelector('.message-list');
      if (list) list.scrollTop = saved.scroll;
      if (saved.selected !== null) select(saved.selected);
      if (saved.focus?.isConnected) saved.focus.focus({preventScroll: true});
      else if (saved.selected !== null) position(saved.selected);
      returning.current = false; snapshot.current = null;
    }
  }, [focused.data, location, source]);
  return {root, source, setSource, closeContext, location, focused, jump, restore,
    visible: focused.data?.items ?? messages};
}
