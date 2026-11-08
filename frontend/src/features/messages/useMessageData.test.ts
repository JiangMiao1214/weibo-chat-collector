import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useMessageData } from './useMessageData';
import { emptyFilters } from './model';
import { useReadRequest } from './useReadRequest';

describe('消息请求生命周期', () => {
  it('刷新同一消息时先清空选中项，确保完成后重新加载详情', async () => {
    const payload = {items: [{id: 7}], total: 1, has_more: false, next_cursor: null};
    const fetcher = vi.fn(async () => new Response(JSON.stringify(payload)));
    vi.stubGlobal('fetch', fetcher);
    const {result} = renderHook(() => useMessageData(emptyFilters, 'active', vi.fn()));
    await act(async () => result.current.loadMessages());
    expect(result.current.selectedMessageId).toBe(7);
    let finish!: (value: Response) => void;
    fetcher.mockImplementationOnce(() => new Promise(resolve => {finish = resolve;}));
    let request!: Promise<void>;
    act(() => {request = result.current.loadMessages();});
    expect(result.current.selectedMessageId).toBeNull();
    await act(async () => {finish(new Response(JSON.stringify(payload))); await request;});
    expect(result.current.selectedMessageId).toBe(7);
  });

  it('快速切换详情时忽略旧响应', async () => {
    let finish!: (value: Response) => void;
    vi.stubGlobal('fetch', vi.fn((url: string) => url.endsWith('/7')
      ? new Promise(resolve => {finish = resolve;}) : Promise.resolve(new Response(JSON.stringify({id: 8})))));
    const {result} = renderHook(() => useMessageData(emptyFilters, 'active', vi.fn()));
    let old!: Promise<void>;
    act(() => {old = result.current.loadDetail(7);});
    await act(async () => result.current.loadDetail(8));
    await act(async () => {finish(new Response(JSON.stringify({id: 7}))); await old;});
    expect(result.current.detail?.id).toBe(8);
  });

  it('阅读查询超时主动报错并屏蔽不响应 abort 的迟到结果', async () => {
    vi.useFakeTimers();
    let finish!: (value: Response) => void;
    vi.stubGlobal('fetch', vi.fn(() => new Promise(resolve => {finish = resolve;})));
    const {result} = renderHook(() => useReadRequest('/api/message-view/facets', '{}'));
    await act(async () => {});
    await act(async () => vi.advanceTimersByTimeAsync(15000));
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toContain('超时');
    await act(async () => finish(new Response(JSON.stringify({members: []}))));
    expect(result.current.data).toBeNull();
  });
});
