import React from 'react';
import {act, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {expect, it, vi} from 'vitest';
import {App} from '../../App';
import {fakeApi} from '../../test/fakeApi';

const scenarios = [
  {action: 'soft-delete', preview: 'delete-preview', view: '消息', previewLabel: '预览删除数量', confirmLabel: '确认软删除'},
  {action: 'restore', preview: 'restore-preview', view: '回收站', previewLabel: '预览恢复数量', confirmLabel: '确认恢复'},
  {action: 'hard-delete', preview: 'hard-delete-preview', view: '回收站', previewLabel: '预览彻底删除', confirmLabel: '确认彻底删除'},
];
type Pending = {filters: Record<string, unknown>; signal?: AbortSignal | null; resolve: (response: Response) => void};

async function setup(view: string) {
  const api = fakeApi();
  const original = api.fetchMock.getMockImplementation()!;
  const pending: Pending[] = [];
  const writes: Array<{path: string; body: Record<string, unknown>}> = [];
  api.fetchMock.mockImplementation(async (input, init) => {
    const path = String(input);
    if (/\/messages\/(delete|restore|hard-delete)-preview$/.test(path)) {
      return new Promise(resolve => pending.push({resolve, signal: init?.signal, filters: JSON.parse(String(init?.body)).filters}));
    }
    if (/\/messages\/(soft-delete|restore|hard-delete)$/.test(path)) {
      writes.push({path, body: JSON.parse(String(init?.body))});
      return new Response(JSON.stringify({deleted_count: 1, restored_count: 1}));
    }
    return original(input, init);
  });
  render(<App/>);
  await screen.findByRole('option', {name: '账号1'});
  fireEvent.click(screen.getByRole('button', {name: view}));
  fireEvent.change(screen.getByLabelText('账号'), {target: {value: '1'}});
  await waitFor(() => expect(api.calls.some(c => c.path.includes('account_id=1'))).toBe(true));
  return {pending, writes, api};
}

async function finish(request: Pending, count = 1) {
  await act(async () => request.resolve(new Response(JSON.stringify({
    preview_count: count, attachment_record_count: 1, filters: request.filters,
  }))));
}

function changeScope(change: string) {
  switch (change) {
    case '账号': fireEvent.change(screen.getByLabelText('账号'), {target: {value: '2'}}); break;
    case '群': fireEvent.change(screen.getByLabelText('群聊'), {target: {value: '12'}}); break;
    case '日期': fireEvent.change(screen.getByLabelText('阅读日期'), {target: {value: '2026-10-20'}}); break;
    case '媒体': fireEvent.click(screen.getByRole('button', {name: '媒体：图片'})); break;
    case '草稿': fireEvent.change(screen.getByLabelText('关键词'), {target: {value: '尚未应用'}}); break;
    case '往返账号':
      fireEvent.change(screen.getByLabelText('账号'), {target: {value: '2'}});
      fireEvent.change(screen.getByLabelText('账号'), {target: {value: '1'}}); break;
    case '视图':
      fireEvent.click(screen.getByRole('button', {name: '消息'}));
      fireEvent.click(screen.getByRole('button', {name: '回收站'})); break;
  }
}

for (const scenario of scenarios) {
  it.each(['账号', '群', '日期', '媒体', '草稿', '往返账号', '视图'])(`${scenario.action}：%s变化使迟到预览失效`, async change => {
    const {pending, writes} = await setup(scenario.view);
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    fireEvent.click(screen.getByRole('button', {name: scenario.previewLabel}));
    await waitFor(() => expect(pending).toHaveLength(1));
    if (change === '媒体') {
      expect(screen.getByRole('button', {name: '媒体：图片'})).toBeDisabled();
      await finish(pending[0]);
      expect(screen.getByRole('button', {name: scenario.confirmLabel})).toBeInTheDocument();
    }
    changeScope(change);
    expect(screen.queryByRole('button', {name: scenario.confirmLabel})).toBeNull();
    // Return before the old response arrives to test invalidation across views.
    if (change === '视图') fireEvent.click(screen.getByRole('button', {name: scenario.view}));
    if (change !== '媒体') await finish(pending[0]);
    expect(screen.queryByRole('button', {name: scenario.confirmLabel})).toBeNull();
    expect(writes).toEqual([]);
    expect(confirm).not.toHaveBeenCalled();
  });

  it(`${scenario.action}：有效预览与执行使用同一范围，取消不写入，重复点击只执行一次`, async () => {
    const {pending, writes} = await setup(scenario.view);
    fireEvent.change(screen.getByLabelText('群聊'), {target: {value: '11'}});
    fireEvent.change(screen.getByLabelText('阅读日期'), {target: {value: '2026-10-20'}});
    fireEvent.click(screen.getByRole('button', {name: scenario.previewLabel}));
    await waitFor(() => expect(pending).toHaveLength(1));
    await finish(pending[0]);
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValue(true);
    fireEvent.click(screen.getByRole('button', {name: scenario.confirmLabel}));
    expect(writes).toEqual([]);
    fireEvent.click(screen.getByRole('button', {name: scenario.confirmLabel}));
    const again = screen.queryByRole('button', {name: scenario.confirmLabel});
    if (again) fireEvent.click(again);
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0]).toEqual({path: '/api/messages/' + scenario.action, body: expect.objectContaining({filters: pending[0].filters, confirm: true})});
    expect(confirm).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(screen.queryByRole('button', {name: scenario.confirmLabel})).toBeNull());
  });
}

it('新预览先到、旧预览后到：旧数量与旧错误均不能覆盖新预览', async () => {
  const {pending} = await setup('消息');
  fireEvent.click(screen.getByRole('button', {name: '预览删除数量'}));
  changeScope('账号');
  // A changed scope must immediately release the pending preview button.
  expect(screen.getByRole('button', {name: '预览删除数量'})).toBeEnabled();
  fireEvent.click(screen.getByRole('button', {name: '预览删除数量'}));
  await waitFor(() => expect(pending).toHaveLength(2));
  await finish(pending[1], 7);
  await finish(pending[0], 99);
  expect(document.querySelector('.delete-preview strong')?.textContent).toBe('7');
  fireEvent.click(screen.getByRole('button', {name: '预览删除数量'}));
  changeScope('往返账号');
  fireEvent.click(screen.getByRole('button', {name: '预览删除数量'}));
  await waitFor(() => expect(pending).toHaveLength(4));
  await finish(pending[3], 8);
  await act(async () => pending[2].resolve(new Response('synthetic failure', {status: 500})));
  expect(document.querySelector('.delete-preview strong')?.textContent).toBe('8');
  expect(screen.queryByText('操作预览失败')).toBeNull();
});

it('预览超时后要求重试，失败和迟到成功都不能恢复旧确认', async () => {
  const {pending, writes} = await setup('消息');
  vi.useFakeTimers();
  fireEvent.click(screen.getByRole('button', {name: '预览删除数量'}));
  await act(async () => { await vi.advanceTimersByTimeAsync(15001); });
  expect(screen.getByText(/预览超时/)).toBeInTheDocument();
  expect(screen.getByRole('button', {name: '预览删除数量'})).toBeEnabled();
  await finish(pending[0]);
  expect(screen.queryByRole('button', {name: '确认软删除'})).toBeNull();
  fireEvent.click(screen.getByRole('button', {name: '预览删除数量'}));
  await finish(pending[1]);
  fireEvent.click(screen.getByRole('button', {name: '预览删除数量'}));
  await act(async () => pending[2].resolve(new Response('synthetic failure', {status: 500})));
  expect(screen.queryByRole('button', {name: '确认软删除'})).toBeNull();
  expect(screen.getByText('操作预览失败')).toBeInTheDocument();
  expect(writes).toEqual([]);
});
