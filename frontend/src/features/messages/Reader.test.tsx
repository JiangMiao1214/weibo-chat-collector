import React from 'react';
import { render, screen, within, waitFor, fireEvent, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';
import { MessageWorkspace } from './MessageWorkspace';
import { emptyFilters } from './model';
import type { Message } from '../../shared/contracts';

const msg = (id: number, content_text = '搜索目标'): Message => ({id, account_id: 1, account_name: '账号甲',
  group_id: 11, group_name: '甲群', user_id: 7, sender_name: '同名', source_user_id: 'u7',
  source_message_id: String(id), sent_at: '2026-09-12 12:00:00', message_type: 'text',
  content_text, is_deleted: 0, deleted_at: null, attachment_count: 0});
const filters = {...emptyFilters, accountId: '1', groupId: '11'};
const facets = {members: [{id: 7, display_name: '同名', avatar_url: null, count: 100},
  {id: 8, display_name: '同名', avatar_url: 'https://fixture.invalid/avatar', count: 2}],
  hours: Array.from({length: 24}, (_, i) => i === 12 ? 102 : 0), hidden_noise: 2};
const context = {target_id: 99, account_id: 1, group_id: 11, deleted_only: false,
  items: [msg(99, '未加载的搜索目标'), msg(7)], has_older: false, has_newer: false};

function setup() {
  const fetcher = vi.fn(async (url: string) => {
    if (url.endsWith('/facets')) return new Response(JSON.stringify(facets));
    if (url.includes('/context')) return new Response(JSON.stringify({...context, target_id: Number(url.match(/\/(\d+)\/context/)?.[1])}));
    if (url.endsWith('/matches')) return new Response(JSON.stringify({total: 1, items: [msg(99)]}));
    return new Response('export');
  });
  vi.stubGlobal('fetch', fetcher);
  const props = {filters, appliedFilters: filters, options: {accounts: [], groups: [], users: [], message_types: ['text', 'image', 'link']},
    updateFilter: vi.fn(), applySearch: vi.fn(), resetSearch: vi.fn(), viewMode: 'active' as const,
    deleting: false, previewAction: vi.fn(), actionPreview: null, executeSoftDelete: vi.fn(),
    executeRestore: vi.fn(), executeHardDelete: vi.fn(), loading: false, messages: [msg(7)], total: 102,
    attachmentSummary: {message_count: 0, total_count: 0, by_type: {}}, selectedMessageId: 7,
    setSelectedMessageId: vi.fn(), loadMore: vi.fn(), hasMore: true, loadingMore: false, detailLoading: false, detail: null};
  return {props, fetcher, user: userEvent.setup(), ...render(<MessageWorkspace {...props} />)};
}

describe('明亮消息阅读器', () => {
  it('独立隐藏入群通知只修改内容筛选，不屏蔽发送者，也不打开广泛噪声过滤', async()=>{
    const {user,props,rerender} = setup();
    expect(screen.getByRole('button',{name:'隐藏入群通知'})).toHaveAttribute('aria-pressed','false');
    await user.click(screen.getByRole('button',{name:'隐藏入群通知'}));
    const selected = props.applySearch.mock.calls.at(-1)![0];
    expect(JSON.parse(selected.viewer)).toMatchObject({hide_join_notices:true,hide_noise:false,members:[]});
    expect(selected.user).toBe('');
    rerender(<MessageWorkspace {...props} filters={selected} appliedFilters={selected}/>);
    expect(screen.getByRole('button',{name:'隐藏入群通知'})).toHaveAttribute('aria-pressed','true');
    expect(screen.getByRole('button',{name:'隐藏噪声'})).toHaveAttribute('aria-pressed','false');
    expect(screen.getByText('入群通知：已隐藏（按内容）')).toBeInTheDocument();
    await user.click(screen.getByRole('button',{name:'隐藏入群通知'}));
    expect(JSON.parse(props.applySearch.mock.calls.at(-1)![0].viewer).hide_join_notices).toBe(false);
    rerender(<MessageWorkspace {...props} loading/>);
    expect(screen.getByRole('button',{name:'隐藏入群通知'})).toBeDisabled();
  });
  it('切换账号时一并清除旧群、成员小时和媒体类型，避免残留隐藏条件', () => {
    const {props, rerender} = setup();
    const selected = {...filters, messageType: 'link', viewer: JSON.stringify({media: 'links', members: [7]})};
    rerender(<MessageWorkspace {...props} filters={selected} appliedFilters={selected}
      options={{...props.options, accounts: [1, 2].map(id => ({id, display_name: `账号${id}`,
        auth_type: 'cookie', login_profile_name: null, is_active: 1}))}} />);
    fireEvent.change(screen.getByLabelText('账号'), {target: {value: '2'}});
    expect(props.applySearch).toHaveBeenCalledWith(expect.objectContaining({accountId: '2', groupId: '', viewer: '', messageType: ''}));
  });
  it('更多类型与全部互斥，加载时不能提交类型变更', async () => {
    const {user, props, rerender} = setup();
    await user.selectOptions(screen.getByLabelText('更多消息类型'), 'file');
    const selected = props.applySearch.mock.calls.at(-1)![0];
    expect(selected.messageType).toBe('file');
    expect(JSON.parse(selected.viewer).media).toBe('');
    rerender(<MessageWorkspace {...props} filters={selected} appliedFilters={selected} />);
    expect(screen.getByRole('button', {name: '媒体：全部'})).toHaveAttribute('aria-pressed', 'false');
    rerender(<MessageWorkspace {...props} loading />);
    expect(screen.getByLabelText('更多消息类型')).toBeDisabled();
  });
  it('更早上下文在右栏翻页，不关闭侧栏、不跳转主列表', async () => {
    const {user, fetcher, props} = setup();
    fetcher.mockImplementation(async (url: string) => {
      if (url.endsWith('/facets')) return new Response(JSON.stringify(facets));
      const older = url.includes('direction=older');
      return new Response(JSON.stringify({...context, target_id: 7, direction: older ? 'older' : 'center',
        items: older ? [msg(6, '真正更早的内容')] : [msg(7)], has_older: !older, has_newer: older}));
    });
    await user.click(screen.getByRole('button', {name: '上下文 #7'}));
    await user.click(await screen.findByRole('button', {name: '查看更早上下文'}));
    expect(await screen.findByText('真正更早的内容')).toBeInTheDocument();
    expect(screen.getByRole('complementary', {name: '消息上下文'})).toBeInTheDocument();
    expect(props.setSelectedMessageId).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', {name: '返回筛选列表'})).toBeNull();
  });

  it('顶部日期立即应用单日范围，默认不占用右侧详情栏', async () => {
    const {props} = setup();
    const date = screen.getByLabelText('阅读日期');
    fireEvent.change(date, {target: {value: '2026-09-12'}});
    expect(props.applySearch).toHaveBeenCalledWith(expect.objectContaining({dateFrom: '2026-09-12', dateTo: '2026-09-12'}));
    expect(screen.queryByRole('heading', {name: '消息详情'})).toBeNull();
    expect(screen.getByRole('complementary', {name: '成员侧栏'}).querySelector('select')).toBeNull();
  });
  it('搜索响应错账号时拒绝跳转，导出完整应用条件而不混入查找词', async () => {
    const {user, fetcher, props} = setup();
    fetcher.mockImplementation(async (url: string) => new Response(JSON.stringify(
      url.endsWith('/matches') ? {total: 1, items: [{...msg(99), account_id: 2}]} : facets)));
    await user.type(screen.getByRole('textbox', {name: '正文查找（保留上下文）'}), '搜索目标');
    await waitFor(() => expect(screen.getByLabelText('匹配消息进度')).toHaveTextContent('1 条匹配消息'));
    await user.click(screen.getByRole('button', {name: '下一个匹配'}));
    expect(fetcher.mock.calls.some(([url]) => url.includes('/99/context'))).toBe(false);
    expect(props.setSelectedMessageId).not.toHaveBeenCalled();
    const objectUrl = vi.fn(() => 'blob:fixture');
    Object.defineProperty(URL, 'createObjectURL', {configurable: true, value: objectUrl});
    Object.defineProperty(URL, 'revokeObjectURL', {configurable: true, value: vi.fn()});
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    await user.click(screen.getByRole('button', {name: '导出当前筛选结果'}));
    await waitFor(() => expect(objectUrl).toHaveBeenCalled());
    const request = (fetcher.mock.calls as unknown as [string, RequestInit][]).find(([url]) => url.endsWith('/export'))!;
    const body = JSON.parse(String(request[1].body));
    expect(body).toMatchObject({filters: {account_id: 1, group_id: 11, keyword: null}, deleted_only: false});
    expect(body).not.toHaveProperty('query');
    click.mockRestore();
  });
  it('查找到已加载且已选中的消息仍会滚动并聚焦，不打开临时窗口', async () => {
    const {user, fetcher} = setup();
    fetcher.mockImplementation(async (url: string) => new Response(JSON.stringify(
      url.endsWith('/matches') ? {total: 1, items: [msg(7)]} : facets)));
    await user.type(screen.getByRole('textbox', {name: '正文查找（保留上下文）'}), '搜索目标');
    await waitFor(() => expect(screen.getByLabelText('匹配消息进度')).toHaveTextContent('1 条匹配消息'));
    await user.click(screen.getByRole('button', {name: '下一个匹配'}));
    expect(screen.getByRole('button', {name: '查看消息 #7'})).toHaveFocus();
    expect(screen.queryByRole('button', {name: '返回筛选列表'})).toBeNull();
  });
  it('成员使用稳定 ID 多选、清除；小时条及媒体按钮立即应用同一筛选', async () => {
    const {user, props, rerender} = setup();
    const first = await screen.findByRole('button', {name: '筛选成员 同名 #7，100 条'});
    await user.click(first);
    expect(props.applySearch).toHaveBeenLastCalledWith(expect.objectContaining({viewer: expect.any(String)}));
    const selected = props.applySearch.mock.calls.at(-1)![0];
    expect(JSON.parse(selected.viewer).members).toEqual([7]);
    rerender(<MessageWorkspace {...props} filters={selected} appliedFilters={selected} />);
    expect(await screen.findByRole('button', {name: '筛选成员 同名 #7，100 条'})).toHaveAttribute('aria-pressed', 'true');
    await user.click(screen.getByRole('button', {name: '筛选成员 同名 #8，2 条'}));
    expect(JSON.parse(props.applySearch.mock.calls.at(-1)![0].viewer).members).toEqual([7, 8]);
    await user.click(screen.getByRole('button', {name: '清除成员'}));
    expect(JSON.parse(props.applySearch.mock.calls.at(-1)![0].viewer).members).toEqual([]);
    const dated = {...selected, dateFrom: '2026-09-12', dateTo: '2026-09-12'};
    rerender(<MessageWorkspace {...props} filters={dated} appliedFilters={dated} />);
    await screen.findByRole('button', {name: '12 时，102 条'});
    await user.click(screen.getByRole('button', {name: '12 时，102 条'}));
    expect(JSON.parse(props.applySearch.mock.calls.at(-1)![0].viewer).hours).toEqual([12]);
    await user.click(screen.getByRole('button', {name: '媒体：链接'}));
    expect(JSON.parse(props.applySearch.mock.calls.at(-1)![0].viewer).media).toBe('links');
    expect(screen.getByLabelText('已应用筛选')).toHaveTextContent('同名 #7');
  });

  it('任意消息打开右侧上下文，未加载目标跳入主列表，返回保留原范围', async () => {
    const {user, props} = setup();
    await user.click(screen.getByRole('button', {name: '上下文 #7'}));
    const panel = await screen.findByRole('complementary', {name: '消息上下文'});
    expect(within(panel).getByRole('button', {name: '关闭上下文'})).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('complementary', {name: '消息上下文'})).toBeNull();
    expect(screen.getByRole('button', {name: '上下文 #7'})).toHaveFocus();
    await user.click(screen.getByRole('button', {name: '上下文 #7'}));
    const reopened = await screen.findByRole('complementary', {name: '消息上下文'});
    await user.click(await within(reopened).findByRole('button', {name: '跳到列表 #99'}));
    expect(await screen.findByRole('article', {name: '消息 #99'})).toBeInTheDocument();
    expect(screen.getByRole('button', {name: '查看消息 #99'})).toHaveFocus();
    expect(screen.getByRole('button', {name: '预览删除数量'})).toBeDisabled();
    await user.click(screen.getByRole('button', {name: '返回筛选列表'}));
    expect(screen.queryByRole('article', {name: '消息 #99'})).toBeNull();
    expect(props.applySearch).not.toHaveBeenCalled();
    expect(screen.getByRole('button', {name: '预览删除数量'})).toBeEnabled();
  });

  it('正文查找不改变筛选，高亮文本并可导航到未加载的匹配消息', async () => {
    const {user, props, container} = setup();
    await user.type(screen.getByRole('textbox', {name: '正文查找（保留上下文）'}), '搜索目标');
    await waitFor(() => expect(screen.getByLabelText('匹配消息进度')).toHaveTextContent('1 条匹配消息'));
    expect(container.querySelector('mark')).toHaveTextContent('搜索目标');
    await user.click(screen.getByRole('button', {name: '下一个匹配'}));
    expect(await screen.findByRole('article', {name: '消息 #99'})).toBeInTheDocument();
    expect(props.applySearch).not.toHaveBeenCalled();
  });

  it('按新需求取消丰富预览，只保留去重安全链接，原数据不执行', () => {
    const {props, rerender} = setup();
    const message = {...msg(7), link_previews: [{url: 'https://weibo.com/123/Abc', author: '原作者',
      title: null, text: '<script>只作为文本</script>', reposts: 3, comments: 4, likes: 5}]};
    rerender(<MessageWorkspace {...props} messages={[message]} />);
    expect(screen.getByRole('link', {name: 'https://weibo.com/123/Abc'})).toHaveAttribute('href', 'https://weibo.com/123/Abc');
    expect(screen.queryByText('原作者')).toBeNull();
    expect(screen.queryByText('<script>只作为文本</script>')).toBeNull();
    expect(screen.queryByText('转发 3 · 评论 4 · 赞 5')).toBeNull();
    expect(document.querySelector('.rich-link-card script')).toBeNull();
  });
});

it('侧栏时间升序，翻早页仍使用最早消息的游标', async () => {
  const {user, fetcher} = setup();
  fetcher.mockImplementation(async (url: string) => new Response(JSON.stringify(url.endsWith('/facets') ? facets :
    {...context, target_id: 7, items: [msg(9), msg(7), msg(6)], has_older: true, has_newer: true})));
  await user.click(screen.getByRole('button', {name: '上下文 #7'}));
  await screen.findByRole('button', {name: '跳到列表 #6'});
  const panel = screen.getByRole('complementary', {name: '消息上下文'});
  expect(within(panel).getAllByRole('button', {name: /跳到列表/}).map(node => node.getAttribute('aria-label')))
    .toEqual(['跳到列表 #6', '跳到列表 #7', '跳到列表 #9']);
  await user.click(within(panel).getByRole('button', {name: '查看更早上下文'}));
  await waitFor(() => expect(fetcher.mock.calls.some(([url]) => url.includes('/6/context') && url.includes('direction=older'))).toBe(true));
});
