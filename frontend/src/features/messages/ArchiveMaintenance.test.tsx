import React from 'react';
import {render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {expect, it, vi} from 'vitest';
import {ArchivePanel} from './ArchivePanel';

it('清理先预览再携带令牌确认，并可创建完整备份', async () => {
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/backup')) return new Response(JSON.stringify({path: '/fake/backups/b.zip', files: 2}));
    if (url.endsWith('/cleanup')) return new Response(JSON.stringify({count: 2, bytes: 2000, token: 'preview-token', removed: init?.body?.toString().includes('preview-token') ? 2 : 0}));
    return new Response(JSON.stringify({account_id: 1, group_id: 11, enabled: true, paused: true, counts: {}, errors: []}));
  });
  vi.stubGlobal('fetch', fetcher);
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  render(<ArchivePanel accountId="1" groupId="11" disabled={false} />);
  await userEvent.click(screen.getByRole('button', {name: '本地归档'}));
  await userEvent.click(await screen.findByText('备份、恢复与磁盘维护（全部账号）'));
  await userEvent.click(screen.getByRole('button', {name: '预览可清理文件'}));
  await userEvent.click(await screen.findByRole('button', {name: '确认清理 2 个文件'}));
  expect(fetcher.mock.calls.at(-1)?.[1]?.body).toContain('preview-token');
  await userEvent.click(screen.getByRole('button', {name: '创建完整备份'}));
  expect(await screen.findByText(/\/fake\/backups\/b.zip/)).toBeInTheDocument();
});
