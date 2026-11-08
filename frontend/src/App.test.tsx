import React from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { fakeApi } from "./test/fakeApi";

describe("用户关键回归", () => {
  it("采集日期、小时和分钟可独立修改并保留选择", async () => {
    const user = userEvent.setup();
    fakeApi();
    render(<App />);
    await screen.findByRole("option", { name: "账号1" });
    for (const label of ["开始时间", "结束时间"]) {
      await user.click(screen.getByRole("button", {name: `${label}小时选择`}));
      expect(screen.getByLabelText(`${label}小时`)).toHaveAttribute("size", "12");
      await user.selectOptions(screen.getByLabelText(`${label}小时`), "20");
      await user.click(screen.getByRole("button", {name: `${label}分钟选择`}));
      expect(screen.getByLabelText(`${label}分钟`)).toHaveAttribute("size", "12");
      await user.selectOptions(screen.getByLabelText(`${label}分钟`), "16");
      fireEvent.change(screen.getByLabelText(`${label}日期`), {target: {value: "2026-10-16"}});
      await user.click(screen.getByRole("button", {name: `${label}小时选择`}));
      await user.selectOptions(screen.getByLabelText(`${label}小时`), "09");
      await user.click(screen.getByRole("button", {name: `${label}分钟选择`}));
      await user.selectOptions(screen.getByLabelText(`${label}分钟`), "35");
      fireEvent.change(screen.getByLabelText(`${label}日期`), {target: {value: "2026-10-17"}});
      expect(screen.getByRole("button", {name: `${label}小时选择`})).toHaveTextContent("09 时");
      expect(screen.getByRole("button", {name: `${label}分钟选择`})).toHaveTextContent("35 分");
      expect(screen.getByLabelText(`${label}日期`)).toHaveValue("2026-10-17");
    }
  });
  it('较早搜索的迟到响应不能覆盖新筛选的列表和附件总数', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    const original = api.fetchMock.getMockImplementation()!;
    let resolveOld!: (value: Response) => void;
    const result = (count: number) => new Response(JSON.stringify({items: [], total: count,
      attachment_summary: {total_count: count, message_count: count, by_type: {}}, has_more: false, next_cursor: null}));
    api.fetchMock.mockImplementation(async (input, init) => {
      const url = new URL(String(input), 'http://test');
      if (url.pathname === '/api/messages' && url.searchParams.get('keyword') === '旧') return new Promise(resolve => {resolveOld = resolve;});
      if (url.pathname === '/api/messages' && url.searchParams.get('keyword') === '新') return result(202);
      return original(input, init);
    });
    render(<App />);
    await screen.findByRole('option', {name: '账号1'});
    await user.click(screen.getByRole('button', {name: '消息'}));
    await user.type(screen.getByLabelText('关键词'), '旧');
    await user.click(screen.getByRole('button', {name: '搜索'}));
    await waitFor(() => expect(resolveOld).toBeTypeOf('function'));
    await user.clear(screen.getByLabelText('关键词'));
    await user.type(screen.getByLabelText('关键词'), '新');
    await user.click(screen.getByRole('button', {name: '搜索'}));
    await screen.findByText('附件总数 202');
    await act(async () => resolveOld(result(101)));
    expect(screen.getByText('附件总数 202')).toBeInTheDocument();
    expect(screen.queryByText('附件总数 101')).toBeNull();
  });
  it('快捷媒体条件真正更新列表请求与删除预览，账号改变清空成员选择', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    render(<App />);
    await screen.findByRole('option', {name: '账号1'});
    await user.click(screen.getByRole('button', {name: '消息'}));
    await user.click(screen.getByRole('button', {name: '媒体：链接'}));
    await waitFor(() => expect(api.calls.some(call => {
      if (!call.path.startsWith('/api/messages?')) return false;
      return new URL(call.path, 'http://test').searchParams.get('viewer')?.includes('links');
    })).toBe(true));
    await user.click(await screen.findByRole('button', {name: '筛选成员 测试用户 #7，10 条'}));
    await user.click(screen.getByRole('button', {name: '预览删除数量'}));
    await waitFor(() => expect(api.calls.some(call => call.path === '/api/messages/delete-preview')).toBe(true));
    const deletion = JSON.parse(api.calls.filter(call => call.path === '/api/messages/delete-preview').at(-1)!.body!);
    expect(JSON.parse(deletion.filters.viewer)).toMatchObject({media: 'links', members: [7]});
    await user.selectOptions(screen.getByLabelText('账号'), '2');
    await user.click(screen.getByRole('button', {name: '搜索'}));
    await waitFor(() => expect(api.calls.some(call => call.path.includes('account_id=2'))).toBe(true));
    const query = new URL(api.calls.filter(call => call.path.startsWith('/api/messages?')).at(-1)!.path, 'http://test');
    expect(query.searchParams.has('viewer')).toBe(false);
  });
  it("消息页显示全筛选附件总数，并将链接筛选发到接口", async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    render(<App />);
    await screen.findByRole("option", { name: "账号1" });
    await user.click(screen.getByRole("button", { name: "消息" }));
    const summary = await screen.findByLabelText("当前筛选结果附件统计");
    expect(within(summary).getByText("附件总数 15")).toBeInTheDocument();
    expect(within(summary).getByText("含附件消息 10")).toBeInTheDocument();
    const linksButton = screen.getByRole("button", { name: "媒体：链接" });
    expect(linksButton).toHaveAttribute("aria-pressed", "false");
    await user.click(linksButton);
    await waitFor(() => expect(api.calls.some((c) => c.path.includes("message_type=link"))).toBe(true));
  });

  it("切换消息账号清空旧群，不保留另一账号的群选项", async () => {
    const user = userEvent.setup();
    fakeApi();
    render(<App />);
    await screen.findByRole("option", { name: "账号1" });
    await user.click(screen.getByRole("button", { name: "消息" }));
    await user.selectOptions(screen.getByLabelText("账号"), "1");
    await user.selectOptions(screen.getByLabelText("群聊"), "11");
    await user.selectOptions(screen.getByLabelText("账号"), "2");
    expect(screen.getByLabelText("群聊")).toHaveValue("");
    expect(within(screen.getByLabelText("群聊")).queryByRole("option", { name: "甲群" })).toBeNull();
    expect(within(screen.getByLabelText("群聊")).getByRole("option", { name: "乙群" })).toBeInTheDocument();
  });

  it("同一登录状态下 Cookie 从未就绪变为就绪，只刷新一次账号资料", async () => {
    vi.useFakeTimers();
    const api = fakeApi();
    api.state.session = { session_id: "session-1", account_id: 1, group_id: 11,
      status: "awaiting_group_selection", cookie_ready: false, error_message: null };
    await act(async () => { render(<App />); });
    expect(screen.getByText("Cookie 未就绪")).toBeInTheDocument();
    api.state.ready = true;
    api.state.session = { ...api.state.session, cookie_ready: true };
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(screen.getByText("Cookie 已就绪")).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(api.calls.filter((c) => c.path === "/api/filter-options")).toHaveLength(2);
  });

  it("登录终态停止轮询，卸载页面清理定时器", async () => {
    vi.useFakeTimers();
    const api = fakeApi();
    api.state.session = { session_id: "session-1", account_id: 1, group_id: 11,
      status: "awaiting_scan", cookie_ready: false, error_message: null };
    let view: ReturnType<typeof render>;
    await act(async () => { view = render(<App />); });
    api.state.session = { ...api.state.session, status: "cancelled" };
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    const calls = api.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(4500); });
    expect(api.calls).toHaveLength(calls);
    view!.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});
