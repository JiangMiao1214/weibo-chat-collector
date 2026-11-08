import {act, renderHook} from '@testing-library/react';
import {expect, it, vi} from 'vitest';
import type {Filters, ViewMode} from '../../shared/contracts';
import {emptyFilters} from './model';
import {useBatchActions} from './useBatchActions';

function setup(view: ViewMode = 'active') {
  const requests: Array<{path: string; init?: RequestInit; resolve: (response: Response) => void}> = [];
  vi.stubGlobal('fetch', vi.fn((path, init) => new Promise(resolve => requests.push({path, init, resolve}))));
  const error = vi.fn(); const refreshed = vi.fn(async () => {});
  const hook = renderHook(({filters, mode}: {filters: Filters; mode: ViewMode}) =>
    useBatchActions(filters, mode, error, refreshed), {initialProps: {filters: {...emptyFilters, accountId: '1'}, mode: view}});
  const finish = async (index: number, body: object, status = 200) => {
    await act(async () => requests[index].resolve(new Response(JSON.stringify(body), {status})));
  };
  return {...hook, requests, error, refreshed, finish};
}

it('预览与执行操作类型必须一致，失效前保存的执行回调也不能写入', async () => {
  const {result, rerender, requests, finish} = setup('deleted');
  act(() => {void result.current.previewAction('restore');});
  await finish(0, {preview_count: 3});
  const oldExecute = result.current.executeRestore;
  await act(async () => {await result.current.executeHardDelete(); await result.current.executeSoftDelete();});
  expect(requests).toHaveLength(1);
  rerender({filters: {...emptyFilters, accountId: '2'}, mode: 'deleted'});
  await act(async () => {await oldExecute();});
  expect(requests).toHaveLength(1);
  expect(result.current.actionPreview).toBeNull();
});

it('确认期间范围失效时再次检查，禁止向旧范围或新范围发出写请求', async () => {
  const {result, requests, finish} = setup();
  act(() => {void result.current.previewAction('soft-delete');});
  await finish(0, {preview_count: 1});
  vi.spyOn(window, 'confirm').mockImplementation(() => {result.current.invalidatePreview(); return true;});
  await act(async () => {await result.current.executeSoftDelete();});
  expect(requests).toHaveLength(1);
});

it('同范围的新操作预览替换旧操作，乱序响应不能改变确认类型或数量', async () => {
  const {result, requests, finish} = setup('deleted');
  act(() => {void result.current.previewAction('restore');});
  act(() => {void result.current.previewAction('hard-delete');});
  expect(requests[0].init?.signal?.aborted).toBe(true);
  await finish(1, {preview_count: 4});
  await finish(0, {preview_count: 99});
  expect(result.current.actionPreview).toMatchObject({action: 'hard-delete', preview_count: 4});
});

it('执行失败消耗预览且不自动重试，必须重新预览才能再次操作', async () => {
  const {result, requests, error, refreshed, finish} = setup();
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  act(() => {void result.current.previewAction('soft-delete');});
  await finish(0, {preview_count: 1});
  act(() => {void result.current.executeSoftDelete();});
  await finish(1, {detail: 'synthetic failure'}, 500);
  expect(error).toHaveBeenLastCalledWith('软删除失败，请重新预览后再操作');
  expect(result.current.actionPreview).toBeNull();
  await act(async () => {await result.current.executeSoftDelete();});
  expect(requests).toHaveLength(2); expect(refreshed).not.toHaveBeenCalled();
});

it('执行开始后切换账号，不重复提交，也不让旧结果刷新新账号列表', async () => {
  const {result, requests, rerender, refreshed, finish} = setup();
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  act(() => {void result.current.previewAction('soft-delete');});
  await finish(0, {preview_count: 1});
  act(() => {void result.current.executeSoftDelete();});
  rerender({filters: {...emptyFilters, accountId: '2'}, mode: 'active'});
  expect(result.current.deleting).toBe(true);
  await act(async () => {await result.current.previewAction('soft-delete'); await result.current.executeSoftDelete();});
  expect(requests).toHaveLength(2);
  expect(JSON.parse(String(requests[1].init?.body)).filters.account_id).toBe(1);
  await finish(1, {deleted_count: 1});
  expect(result.current.deleteResult).toBeNull(); expect(result.current.deleting).toBe(false);
  expect(refreshed).not.toHaveBeenCalled();
});

it('卸载取消预览；忽略取消的请求后来失败也不回写错误', async () => {
  const {result, requests, unmount, error, finish} = setup();
  act(() => {void result.current.previewAction('soft-delete');});
  error.mockClear(); unmount();
  expect(requests[0].init?.signal?.aborted).toBe(true);
  await finish(0, {}, 500);
  expect(error).not.toHaveBeenCalled();
});
