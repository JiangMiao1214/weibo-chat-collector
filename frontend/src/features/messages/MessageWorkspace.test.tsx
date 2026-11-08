import React from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Message } from "../../shared/contracts";
import { emptyFilters } from "./model";
import { MessageWorkspace } from "./MessageWorkspace";

const message = (id: number, extra: Partial<Message> = {}): Message => ({
  id, account_id: 1, account_name: "账号甲", group_id: 11, group_name: "甲群",
  user_id: 7, sender_name: "小林", source_user_id: "user-7", source_message_id: String(id),
  sent_at: "2026-08-23 10:00:00", message_type: "text", content_text: "测试消息",
  is_deleted: 0, deleted_at: null, attachment_count: 0, ...extra,
});

function setup(items: Message[], extra: Partial<React.ComponentProps<typeof MessageWorkspace>> = {}) {
  const props: React.ComponentProps<typeof MessageWorkspace> = {
    filters: { ...emptyFilters }, options: { accounts: [], groups: [], users: [], message_types: ["text", "image", "link"] },
    updateFilter: vi.fn(), applySearch: vi.fn(), resetSearch: vi.fn(), viewMode: "active",
    deleting: false, previewAction: vi.fn(), actionPreview: null, executeSoftDelete: vi.fn(),
    executeRestore: vi.fn(), executeHardDelete: vi.fn(), loading: false, messages: items,
    total: items.length, attachmentSummary: { message_count: 1, total_count: 2, by_type: { image: 2 } },
    selectedMessageId: null, setSelectedMessageId: vi.fn(), loadMore: vi.fn(), hasMore: true,
    loadingMore: false, detailLoading: false, detail: null, ...extra,
  };
  return { props, user: userEvent.setup(), ...render(<MessageWorkspace {...props} />) };
}

describe("聊天式消息列表", () => {
  it("用可访问的独立卡片选中消息，保留附件汇总和详情入口", async () => {
    const { user, props, container } = setup([message(1, { attachment_count: 2, message_type: "image" })]);
    const card = screen.getByRole("article", { name: "消息 #1" });
    expect(within(card).getByLabelText("小林的文字头像")).toHaveTextContent("小");
    await user.click(within(card).getByRole("button", { name: "查看消息 #1" }));
    expect(props.setSelectedMessageId).toHaveBeenCalledWith(1);
    await user.click(within(card).getByRole("button", { name: "查看 2 个附件" }));
    expect(props.setSelectedMessageId).toHaveBeenCalledTimes(2);
    expect(screen.getByText("附件总数 2")).toBeInTheDocument();
    expect(container.querySelector("button button, button a")).toBeNull();
  });

  it("只将同账号、同群、同用户且五分钟内的相邻发言分组，按时间升序展示", () => {
    setup([message(4, { sent_at: "2026-08-23 10:04:00" }), message(3),
      message(2, { user_id: 8 }), message(1, { user_id: 8, account_id: 2, group_id: 21 })]);
    const cards = screen.getAllByRole("article");
    expect(cards.map((card) => card.getAttribute("aria-label"))).toEqual(["消息 #1", "消息 #2", "消息 #3", "消息 #4"]);
    expect(cards.map((card) => card.getAttribute("data-continuation"))).toEqual(["false", "false", "false", "true"]);
  });

  it("引用块与回复分开，唯一文字匹配项可以定位并选中", async () => {
    const { user, props } = setup([message(2, { sent_at: "2026-08-23 10:01:00", content_text: "「原消息内容」 - - - 收到" }),
      message(1, { content_text: "原消息内容" })]);
    const reply = screen.getByRole("article", { name: "消息 #2" });
    expect(reply.querySelector("blockquote")).toHaveTextContent("原消息内容");
    expect(within(reply).queryByText("在已入库消息中定位引用")).toBeNull();
    expect(within(reply).getByText("收到")).toBeInTheDocument();
    expect(within(reply).getByText("文字匹配 · 仅当前列表")).toBeInTheDocument();
    await user.click(within(reply).getByRole("button", { name: "查看匹配消息 #1" }));
    expect(props.setSelectedMessageId).toHaveBeenCalledWith(1);
    expect(screen.getByRole("button", { name: "查看消息 #1" })).toHaveFocus();
  });

  it("重复文本不猜测目标，也不能跨账号或群跳转", () => {
    const reply = message(5, { sent_at: "2026-08-23 10:01:00", content_text: "「原文」 - - - 好" });
    const view = setup([reply, message(1, { content_text: "原文" }), message(2, { content_text: "原文" })]);
    expect(screen.getByText("有多个文字匹配项，无法确定原消息")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /查看匹配消息/ })).toBeNull();
    view.rerender(<MessageWorkspace {...view.props} messages={[reply,
      message(1, { content_text: "原文", account_id: 2 }), message(2, { content_text: "原文", group_id: 21 })]} />);
    expect(screen.getByText("当前列表未找到匹配项，可加载更早消息或调整筛选")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /查看匹配消息/ })).toBeNull();
  });

  it("正文链接显示为安全卡片，点击链接不误选消息，HTML 不执行", async () => {
    const { user, props, container } = setup([message(1, {
      content_text: '<img src=x onerror=alert(1)>\nhttps://example.com/article\njavascript:alert(1)', message_type: "link",
    })]);
    const link = screen.getByRole("link", { name: /example.com/ });
    expect(link).toHaveAttribute("href", "https://example.com/article");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveAttribute("target", "_blank");
    await user.click(link);
    expect(props.setSelectedMessageId).not.toHaveBeenCalled();
    expect(container.querySelector("img, script")).toBeNull();
    expect(screen.getAllByRole("link")).toHaveLength(1);
  });

  it("保留分页与软删除预览，不因选中一张卡片改变删除范围", async () => {
    const { user, props } = setup([message(1)]);
    await user.click(screen.getByRole("button", { name: "查看消息 #1" }));
    await user.click(screen.getByRole("button", { name: "加载更早消息" }));
    await user.click(screen.getByRole("button", { name: /预览删除数量/ }));
    expect(props.loadMore).toHaveBeenCalledTimes(1);
    expect(props.previewAction).toHaveBeenCalledExactlyOnceWith("soft-delete");
    expect(props.executeSoftDelete).not.toHaveBeenCalled();
    expect(props.updateFilter).not.toHaveBeenCalled();
  });

  it("仍可点击消息正文打开详情，引用按钮不触发第二次选择", async () => {
    const { user, props } = setup([message(2, { sent_at: "2026-08-23 10:01:00", content_text: "「原文」 - - - 回复正文" }),
      message(1, { content_text: "原文" })]);
    await user.click(screen.getByText("回复正文"));
    expect(props.setSelectedMessageId).toHaveBeenCalledExactlyOnceWith(2);
    vi.mocked(props.setSelectedMessageId).mockClear();
    await user.click(screen.getByRole("button", { name: "查看匹配消息 #1" }));
    expect(props.setSelectedMessageId).toHaveBeenCalledExactlyOnceWith(1);
  });

  it("回收站只出现恢复与彻底删除预览，加载中不能重复分页", async () => {
    const { user, props } = setup([message(1, { is_deleted: 1 })], { viewMode: "deleted", loadingMore: true });
    expect(screen.queryByRole("button", { name: "预览删除数量" })).toBeNull();
    await user.click(screen.getByRole("button", { name: /预览恢复数量/ }));
    await user.click(screen.getByRole("button", { name: "预览彻底删除" }));
    expect(props.previewAction).toHaveBeenNthCalledWith(1, "restore");
    expect(props.previewAction).toHaveBeenNthCalledWith(2, "hard-delete");
    expect(screen.getByRole("button", { name: "加载中" })).toBeDisabled();
    expect(props.executeHardDelete).not.toHaveBeenCalled();
  });
});

 it("跨日和追加早页保持旧到新，分页入口位于消息上方", () => {
    const newest = message(3, {sent_at: '2026-08-24 10:00:00'});
    const view = setup([newest, message(2)]);
    view.rerender(<MessageWorkspace {...view.props} messages={[newest, message(2), message(1, {sent_at: '2026-08-22 10:00:00'})]} />);
    expect(screen.getAllByRole('article').map(node => node.getAttribute('aria-label'))).toEqual(['消息 #1', '消息 #2', '消息 #3']);
    expect(view.container.querySelector('.message-list')?.firstElementChild).toHaveClass('load-more-row');
  });

 it("附件相同地址只展示一次，网址标题不重复，图片走账号群范围内的读取入口", async () => {
    const url = 'https://upload.api.weibo.com/2/mss/msget?fid=fake';
    const attachment = {id: 1, attachment_type: 'image', source_url: url, title: url, local_path: null,
      file_name: null, mime_type: null, file_size: null, description: null, download_status: 'pending'};
    const item = message(1);
    const {user, container} = setup([item], {detail: {...item, raw_payload: null, normalized_text: null,
      attachments: [attachment, {...attachment, id: 2}]}});
    await user.click(screen.getByRole('button', {name: '查看消息 #1'}));
    expect(container.querySelectorAll('.attachment-item')).toHaveLength(1);
    const link = within(container.querySelector('.attachment-item') as HTMLElement).getByRole('link');
    expect(link).toHaveAttribute('href', '/api/attachments/1/image?account_id=1&group_id=11&deleted_only=false');
    expect(screen.queryAllByText(url)).toHaveLength(0);
  });

it('从当前账号群打开本地归档，明确启动历史补下载和新消息归档', async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({account_id: 1, group_id: 11, enabled: false, paused: false, counts: {}, errors: []})));
  vi.stubGlobal('fetch', fetcher);
  const {user} = setup([message(1)], {filters: {...emptyFilters, accountId: '1', groupId: '11'}});
  await user.click(screen.getByRole('button', {name: '本地归档'}));
  await user.click(await screen.findByRole('button', {name: '开启本群归档并补下载历史'}));
  expect(fetcher).toHaveBeenCalledWith('/api/archives/control', expect.objectContaining({method: 'POST',
    body: JSON.stringify({account_id: 1, group_id: 11, action: 'start'})}));
});

it('已存图片和文件优先打开本地归档，显示中文保存状态', async () => {
  const item = message(1);
  const attachment = {id: 1, attachment_type: 'image', source_url: 'https://fixture.invalid/original.png', title: null,
    file_name: null, local_path: 'archives/1/hash', mime_type: 'image/png', file_size: 10, description: null, download_status: 'stored'};
  const {user, container} = setup([item], {detail: {...item, raw_payload: null, normalized_text: null, attachments: [attachment],
    archives: [{id: 9, message_id: 1, source_url: attachment.source_url, kind: 'image', status: 'stored'}]}});
  await user.click(screen.getByRole('button', {name: '查看消息 #1'}));
  expect(within(container.querySelector('.attachment-item') as HTMLElement).getByRole('link')).toHaveAttribute('href',
    '/api/archives/9/content?account_id=1&group_id=11&deleted_only=false');
  expect(screen.getByText('已保存本地')).toBeInTheDocument();
});

function scrollFixture() {
  const items = [message(10)];
  const view = setup(items);
  const box = view.container.querySelector('.message-list') as HTMLDivElement;
  const card = screen.getByRole('article', {name:'消息 #10'});
  let top = 180;
  vi.spyOn(box,'getBoundingClientRect').mockImplementation(()=>({top:100,bottom:600}) as DOMRect);
  vi.spyOn(card,'getBoundingClientRect').mockImplementation(()=>({top,bottom:top+200}) as DOMRect);
  return {...view,box,items,setTop:(value:number)=>{top=value;}};
}

it('加载更早消息前插后保持原可见消息位置，不按旧 scrollTop 跳到最早项', async()=>{
  const view = scrollFixture();
  await view.user.click(screen.getByRole('button',{name:'加载更早消息'}));
  view.rerender(<MessageWorkspace {...view.props} loadingMore />);
  view.setTop(680);
  view.rerender(<MessageWorkspace {...view.props} messages={[...view.items,message(9)]} />);
  expect(view.box.scrollTop).toBe(500);
  expect(view.props.setSelectedMessageId).not.toHaveBeenCalled();
});

it('等待加载时手动滚动，按最新阅读位置补偿而非拉回点击时的位置', async()=>{
  const view = scrollFixture();
  await view.user.click(screen.getByRole('button',{name:'加载更早消息'}));
  view.rerender(<MessageWorkspace {...view.props} loadingMore />);
  view.box.scrollTop=100;view.setTop(80);fireEvent.scroll(view.box);
  view.setTop(580);
  view.rerender(<MessageWorkspace {...view.props} messages={[...view.items,message(9)]} />);
  expect(view.box.scrollTop).toBe(600);
});

it.each(['失败','换筛选','刷新替换'])('分页%s后丢弃旧位置，不影响下一次列表更新', async reason=>{
  const view = scrollFixture();
  await view.user.click(screen.getByRole('button',{name:'加载更早消息'}));
  view.rerender(<MessageWorkspace {...view.props} loadingMore />);
  const props = reason==='换筛选' ? {...view.props,filters:{...emptyFilters,accountId:'2'}} : view.props;
  view.rerender(<MessageWorkspace {...props} loading={reason==='刷新替换'} />);
  view.setTop(680);
  view.rerender(<MessageWorkspace {...props} messages={[...view.items,message(9)]} />);
  expect(view.box.scrollTop).toBe(0);
});
