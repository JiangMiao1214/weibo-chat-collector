import React from 'react';
import {act, fireEvent, render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {expect, it, vi} from 'vitest';
import {ArchivePanel} from './ArchivePanel';
import {RichLinks} from './RichLinks';
import {AttachmentList} from './AttachmentList';

const status = {account_id: 1, group_id: 11, enabled: true, paused: false, counts: {stored: 3, queued: 2}, errors: []};
const message = {account_id: 1, group_id: 11, is_deleted: 0};
const response = (body: unknown) => new Response(JSON.stringify(body));

it('显示三项实时下载和失败访问链接，拒绝危险地址', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => response({...status, counts: {downloading: 3},
    active_jobs: [1, 2, 3].map(id => ({id, message_id: id, kind: 'image', source_url: `https://fixture.invalid/active-${id}`})),
    errors: [{id: 9, message_id: 101, kind: 'link', status: 'failed', error: '失败', source_url: 'https://t.cn/fixture'},
      {id: 10, message_id: 102, kind: 'image', status: 'failed', error: '失败', source_url: 'javascript:alert(1)'}]})));
  render(<ArchivePanel accountId="1" groupId="11" disabled={false} />);
  await userEvent.click(screen.getByRole('button', {name: '本地归档'}));
  expect(await screen.findByText('正在下载 3 / 3')).toBeInTheDocument();
  expect(screen.getByRole('link', {name: 'https://t.cn/fixture'})).toHaveAttribute('href', 'https://t.cn/fixture');
  expect(screen.getAllByRole('link')).toHaveLength(4);
});

it('无权限链接可单项跳过，显示原因和数量且不自动恢复暂停', async () => {
  const fetcher = vi.fn(async (url: string) => response({...status, paused: true,
    counts: {skipped: url.endsWith('/skip') ? 1 : 0}, errors: [{id: 9, message_id: 101, kind: 'link',
      status: url.endsWith('/skip') ? 'skipped' : 'blocked', error: url.endsWith('/skip') ? '已跳过：用户确认无权限' : '无法读取原文'}]}));
  vi.stubGlobal('fetch', fetcher);
  render(<ArchivePanel accountId="1" groupId="11" disabled={false} />);
  await userEvent.click(screen.getByRole('button', {name: '本地归档'}));
  await userEvent.click(await screen.findByRole('button', {name: '跳过此链接'}));
  expect(await screen.findByText('已跳过 1')).toBeInTheDocument();
  expect(screen.getByText(/用户确认无权限/)).toBeInTheDocument();
  expect(screen.getByText('归档已暂停')).toBeInTheDocument();
  expect(fetcher.mock.calls.at(-1)?.[0]).toBe('/api/archives/9/skip');
});

it('跳过的链接保留远端入口并明确显示未保存', () => {
  render(<RichLinks urls={['https://weibo.com/123/Abc']} message={{...message, archives: [
    {id: 9, message_id: 1, source_url: 'https://weibo.com/123/Abc', kind: 'link', status: 'skipped'}]}} />);
  expect(screen.getByText('已跳过 · 未保存本地')).toBeInTheDocument();
  expect(screen.getByRole('link')).toHaveAttribute('href', 'https://weibo.com/123/Abc');
});

it('归档状态展示并支持暂停恢复，关闭时取消请求并恢复焦点', async () => {
  const fetcher = vi.fn(async (_url: string, init?: RequestInit) => response({...status,
    paused: init?.body ? JSON.parse(String(init.body)).action === 'pause' : false}));
  vi.stubGlobal('fetch', fetcher);
  render(<ArchivePanel accountId="1" groupId="11" disabled={false} />);
  const user = userEvent.setup();
  const trigger = screen.getByRole('button', {name: '本地归档'});
  await user.click(trigger);
  expect(await screen.findByText('文件已保存 3')).toBeInTheDocument();
  await user.click(screen.getByRole('button', {name: '暂停归档'}));
  expect(await screen.findByText('归档已暂停')).toBeInTheDocument();
  await user.click(screen.getByRole('button', {name: '恢复归档并重试失败项'}));
  expect(await screen.findByText('归档已开启 · 自动检查新消息')).toBeInTheDocument();
  await user.click(screen.getByRole('button', {name: '关闭归档'}));
  expect(trigger).toHaveFocus();
  expect(fetcher.mock.calls.at(-1)?.[1]?.signal?.aborted).toBe(true);
});

it('切换账号卸载旧窗口后迟到响应不覆盖新群状态，错误范围被拒绝', async () => {
  let finish!: (value: Response) => void;
  vi.stubGlobal('fetch', vi.fn().mockImplementationOnce(() => new Promise<Response>(resolve => {finish = resolve;}))
    .mockResolvedValue(response(status)));
  const view = render(<ArchivePanel key="1" accountId="1" groupId="11" disabled={false} />);
  fireEvent.click(screen.getByRole('button', {name: '本地归档'}));
  view.rerender(<ArchivePanel key="2" accountId="2" groupId="21" disabled={false} />);
  fireEvent.click(screen.getByRole('button', {name: '本地归档'}));
  await act(async () => {finish(response(status));});
  expect(await screen.findByRole('alert')).toHaveTextContent('范围不匹配');
  expect(screen.queryByText('文件已保存 3')).toBeNull();
});

it('不响应取消的请求超时后有明确提示，轮询不会无限延长等待', async () => {
  vi.useFakeTimers();
  vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
  const view = render(<ArchivePanel accountId="1" groupId="11" disabled={false} />);
  fireEvent.click(screen.getByRole('button', {name: '本地归档'}));
  await act(async () => {vi.advanceTimersByTime(15001);});
  expect(screen.getByRole('alert')).toHaveTextContent('超时');
  view.unmount();
  vi.useRealTimers();
});

it('正文链接显示本地入口和关联媒体未完成状态；文件和视频也可离线打开', () => {
  const archived = {...message, archives: [{id: 9, message_id: 1, source_url: 'https://weibo.com/123/Abc',
    status: 'stored', kind: 'link', pending_resources: 2}]};
  const view = render(<RichLinks urls={['https://weibo.com/123/Abc']} message={archived} />);
  expect(screen.getByRole('link')).toHaveAttribute('href', '/api/archives/9/content?account_id=1&group_id=11&deleted_only=false');
  expect(screen.getByText('正文已保存 · 2 个关联媒体未完成')).toBeInTheDocument();
  for (const kind of ['file', 'video']) {
    const source_url = 'https://fixture.invalid/' + kind;
    view.rerender(<AttachmentList message={{...message, is_deleted: 1, archives: [{id: 8, message_id: 1, source_url, kind, status: 'stored'}]}}
      attachments={[{id: 1, attachment_type: kind, source_url, title: '模拟附件', description: null,
        file_name: null, local_path: 'archives/1/fake', file_size: 20, mime_type: null, download_status: 'stored'}]} />);
    expect(screen.getByRole('link')).toHaveAttribute('href', '/api/archives/8/content?account_id=1&group_id=11&deleted_only=true');
  }
});

it('已跳过项可重新排队，并显示实际失败跳转地址和修复入口', async () => {
  const fetcher = vi.fn(async (_url: string) => response({...status, paused: true, errors: [{id: 9, message_id: 101,
    kind: 'link', status: 'skipped', error: '已跳过', failure_url: 'https://target.invalid/article'}]}));
  vi.stubGlobal('fetch', fetcher);
  render(<ArchivePanel accountId="1" groupId="11" disabled={false} />);
  await userEvent.click(screen.getByRole('button', {name: '本地归档'}));
  expect(await screen.findByRole('link', {name: 'https://target.invalid/article'})).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', {name: '重新下载此项'}));
  expect(fetcher.mock.calls.at(-1)?.[0]).toBe('/api/archives/9/retry');
  expect(screen.getByRole('button', {name: '检查并补下载缺失或损坏文件'})).toBeInTheDocument();
});

it('本地图片显示缩略图，灯箱支持前后切换和关闭返回焦点', async () => {
  const sources = ['https://fixture.invalid/a.png', 'https://fixture.invalid/b.png'];
  render(<AttachmentList message={{...message, archives: sources.map((source_url, index) => ({id: index+1,
    message_id: 1, source_url, kind: 'image', status: 'stored'}))}} attachments={sources.map((source_url, index) => ({
      id: index+1, source_url, attachment_type: 'image', title: null, description: null, file_name: null,
      local_path: null, file_size: null, mime_type: 'image/png', download_status: 'stored'}))} />);
  const trigger = screen.getAllByRole('button', {name: '放大图片'})[0];
  await userEvent.click(trigger);
  expect(screen.getByRole('dialog', {name: '本地图片浏览'})).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', {name: '下一张'}));
  expect(screen.getByText('2 / 2')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', {name: '关闭图片'}));
  expect(trigger).toHaveFocus();
});
