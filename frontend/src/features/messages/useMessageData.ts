import { useEffect, useRef, useState } from 'react';
import type { Filters, ViewMode, Message, MessageDetail, AttachmentSummary, Cursor } from '../../shared/contracts';
import { API_BASE_URL } from '../../shared/http';
import { buildMessageQuery, emptyAttachmentSummary } from './model';

function useLatestRequest() {
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  return () => {
    controller.current?.abort();
    const next = new AbortController(); controller.current = next;
    return next;
  };
}

async function read<T>(path: string, controller: AbortController): Promise<T> {
  let timer: ReturnType<typeof setTimeout>;
  const deadline = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error('读取超时，请重试')), 15000);
  });
  try {
    return await Promise.race([deadline, (async () => {
      const response = await fetch(API_BASE_URL + path, {signal: controller.signal});
      if (!response.ok) throw new Error(`消息读取失败（${response.status}）`);
      return await response.json() as T;
    })()]);
  } finally { clearTimeout(timer!); }
}

export function useMessageData(appliedFilters: Filters, viewMode: ViewMode, setError: (value: string | null) => void) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [selectedMessageId, setSelectedMessageId] = useState<number | null>(null);
  const [detail, setDetail] = useState<MessageDetail | null>(null);
  const [total, setTotal] = useState(0);
  const [attachmentSummary, setAttachmentSummary] = useState<AttachmentSummary>(emptyAttachmentSummary);
  const [nextCursor, setNextCursor] = useState<Cursor | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const beginList = useLatestRequest();
  const beginDetail = useLatestRequest();
  async function loadMessages(nextFilters = appliedFilters, mode: 'replace' | 'append' = 'replace',
                              cursor: Cursor | null = null, nextViewMode = viewMode) {
    const current = beginList();
    const append = mode === 'append';
    if (append) setLoadingMore(true);
    else {
      setSelectedMessageId(null);
      beginDetail().abort(); setDetail(null); setDetailLoading(false);
      setLoading(true); setMessages([]); setTotal(0); setAttachmentSummary(emptyAttachmentSummary);
      setHasMore(false); setNextCursor(null);
    }
    setError(null);
    try {
      const data = await read<{items: Message[]; total: number; attachment_summary?: AttachmentSummary;
        has_more: boolean; next_cursor: Cursor | null}>(`/api/messages?${buildMessageQuery(nextFilters, cursor, nextViewMode)}`, current);
      if (current.signal.aborted) return;
      setMessages(existing => append ? [...existing, ...data.items] : data.items);
      setTotal(data.total); setAttachmentSummary(data.attachment_summary ?? emptyAttachmentSummary);
      setHasMore(data.has_more); setNextCursor(data.next_cursor);
      if (!append) setSelectedMessageId(data.items[0]?.id ?? null);
    } catch (error) {
      if (!current.signal.aborted) setError(error instanceof Error ? error.message : '请求失败');
    } finally {
      if (!current.signal.aborted) { setLoading(false); setLoadingMore(false); current.abort(); }
    }
  }
  async function loadDetail(id: number) {
    const current = beginDetail(); setDetailLoading(true); setDetail(null); setError(null);
    try {
      const query = viewMode === 'deleted' ? '?include_deleted=true' : '';
      const data = await read<MessageDetail>(`/api/messages/${id}${query}`, current);
      if (!current.signal.aborted) setDetail(data);
    } catch (error) {
      if (!current.signal.aborted) setError(error instanceof Error ? error.message : '请求失败');
    } finally {
      if (!current.signal.aborted) { setDetailLoading(false); current.abort(); }
    }
  }
  return {messages, selectedMessageId, setSelectedMessageId, detail, setDetail, total, attachmentSummary,
    nextCursor, hasMore, loading, setLoading, loadingMore, detailLoading, loadMessages, loadDetail};
}
