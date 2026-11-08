import React from "react";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Message } from "../../shared/contracts";
import { MessageTimeline } from "./MessageTimeline";

const source: Message = { id: 10, account_id: 1, account_name: "甲", group_id: 11, group_name: "甲群",
  user_id: 1, sender_name: "用户", source_user_id: null, source_message_id: null,
  sent_at: "2026-08-23 10:00:00", message_type: "text", content_text: "「更早原文」 - - - 回复",
  is_deleted: 0, deleted_at: null, attachment_count: 0 };
const target = { ...source, id: 1, sent_at: "2026-08-01 10:00:00", content_text: "更早原文", attachments: [] };
const result = (status = "matched") => ({ source_message_id: 10, account_id: 1, group_id: 11,
  deleted_only: false, match_method: "full_text", status, target: status === "matched" ? target : null,
  items: status === "matched" ? [target] : [], has_older: false, has_newer: false });
const response = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
function setup(message = source) {
  const onSelect = vi.fn();
  return { onSelect, user: userEvent.setup(), ...render(<MessageTimeline messages={[message]} selectedMessageId={10} onSelect={onSelect} />) };
}

describe("后端引用定位", () => {
  it("开发模式重复挂载不会重复发送定位请求", async () => {
    const fetcher = vi.fn().mockImplementation(() => Promise.resolve(response(result())));
    vi.stubGlobal("fetch", fetcher);
    render(<React.StrictMode><MessageTimeline messages={[source]} selectedMessageId={10} onSelect={vi.fn()} /></React.StrictMode>);
    await userEvent.setup().click(screen.getByRole("button", { name: "查看引用原文" }));
    expect(await screen.findByLabelText("匹配消息 #1")).toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it("仅显式点击后请求全群定位，展示未加载消息，不替换原列表或选择", async () => {
    const fetcher = vi.fn().mockResolvedValue(response(result()));
    vi.stubGlobal("fetch", fetcher);
    const { user, onSelect } = setup();
    expect(fetcher).not.toHaveBeenCalled();
    const trigger = screen.getByRole("button", { name: "查看引用原文" });
    await user.click(trigger);
    const dialog = await screen.findByRole("dialog", { name: "引用消息上下文" });
    expect(await within(dialog).findByText("更早原文")).toBeInTheDocument();
    const url = new URL(fetcher.mock.calls[0][0], "http://localhost");
    expect(url.pathname).toBe("/api/messages/10/quote-context");
    expect(Object.fromEntries(url.searchParams)).toEqual({ account_id: "1", group_id: "11", deleted_only: "false" });
    expect(within(dialog).getByText(/忽略日期、关键词、用户和类型筛选/)).toBeInTheDocument();
    await user.click(within(dialog).getByText("更早原文"));
    expect(screen.getByRole("button", { name: "查看消息 #10" })).toHaveAttribute("aria-pressed", "true");
    expect(onSelect).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: "返回原列表" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(trigger).toHaveFocus();
  });

  it.each([
    ["ambiguous", "存在多个匹配项，无法确定原消息"],
    ["not_found", "未找到匹配项：原消息可能尚未采集、已被删除或文本不一致"],
    ["not_quote", "此消息没有可识别的引用格式"],
  ])("%s 不猜测目标", async (status, text) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(result(status))));
    const { user } = setup();
    await user.click(screen.getByRole("button", { name: "查看引用原文" }));
    expect(await screen.findByText(text)).toBeInTheDocument();
    expect(screen.queryByLabelText("匹配消息 #1")).toBeNull();
  });

  it("请求失败显式提示，只在用户重试后再请求", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(new Response("", { status: 503 })).mockResolvedValueOnce(response(result()));
    vi.stubGlobal("fetch", fetcher);
    const { user } = setup();
    await user.click(screen.getByRole("button", { name: "查看引用原文" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("HTTP 503");
    expect(fetcher).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "重试定位" }));
    expect(await screen.findByLabelText("匹配消息 #1")).toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("关闭会中止请求，迟到的响应不能重新打开或改变页面", async () => {
    let finish!: (value: Response) => void;
    const fetcher = vi.fn().mockImplementation(() => new Promise<Response>((resolve) => { finish = resolve; }));
    vi.stubGlobal("fetch", fetcher);
    const { user, onSelect } = setup();
    await user.click(screen.getByRole("button", { name: "查看引用原文" }));
    await user.click(screen.getByRole("button", { name: "返回原列表" }));
    expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true);
    await act(async () => finish(response(result())));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("回收站请求删除态，拒绝返回其他账号/群的意外响应", async () => {
    const fetcher = vi.fn().mockResolvedValue(response({ ...result(), account_id: 2 }));
    vi.stubGlobal("fetch", fetcher);
    const { user } = setup({ ...source, is_deleted: 1 });
    await user.click(screen.getByRole("button", { name: "查看引用原文" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("范围不一致");
    expect(fetcher.mock.calls[0][0]).toContain("deleted_only=true");
    expect(screen.queryByLabelText("匹配消息 #1")).toBeNull();
  });

  it("即使网络不响应中止，15 秒超时也结束加载并忽略迟到响应", async () => {
    vi.useFakeTimers();
    let finish!: (value: Response) => void;
    const fetcher = vi.fn().mockImplementation(() => new Promise<Response>((resolve) => { finish = resolve; }));
    vi.stubGlobal("fetch", fetcher);
    setup();
    fireEvent.click(screen.getByRole("button", { name: "查看引用原文" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
    expect(screen.getByRole("alert")).toHaveTextContent("定位超时");
    expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true);
    await act(async () => finish(response(result())));
    expect(screen.queryByLabelText("匹配消息 #1")).toBeNull();
  });
});
