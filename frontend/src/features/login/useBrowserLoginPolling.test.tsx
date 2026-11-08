import { useState } from "react";
import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { BrowserLoginSession } from "../../shared/contracts";
import { fetchJson } from "../../shared/http";
import { useBrowserLoginPolling } from "./useBrowserLoginPolling";

vi.mock("../../shared/http", () => ({ fetchJson: vi.fn() }));
const waiting: BrowserLoginSession = {
  session_id: "old", account_id: 1, group_id: 11, status: "awaiting_scan",
  created_at: "", expires_at: "", cookie_ready: false, candidate_source_group_id: null,
  candidate_captured_at: null, error_code: null, error_message: null,
};

describe("登录轮询隔离", () => {
  it("卸载后忽略未完成请求的错误", async () => {
    vi.useFakeTimers();
    let reject!: (error: Error) => void;
    vi.mocked(fetchJson).mockImplementationOnce(() => new Promise((_, fail) => { reject = fail; }));
    const onError = vi.fn(), onSession = vi.fn(), onCookieReady = vi.fn();
    const { unmount } = renderHook(() => useBrowserLoginPolling({ session: waiting, onError, onSession, onCookieReady }));
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    unmount();
    await act(async () => { reject(new Error("late error")); });
    expect(onError).not.toHaveBeenCalled();
    expect(onSession).not.toHaveBeenCalled();
  });

  it("会话已结束但账号资料刷新失败时，仍明确显示错误", async () => {
    vi.useFakeTimers();
    vi.mocked(fetchJson).mockResolvedValue({ ...waiting, status: "completed", cookie_ready: true });
    let reject!: (error: Error) => void;
    const onCookieReady = vi.fn(() => new Promise((_, fail) => { reject = fail; }));
    const onError = vi.fn();
    renderHook(() => {
      const [session, onSession] = useState(waiting);
      useBrowserLoginPolling({ session, onSession, onCookieReady, onError });
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    await act(async () => { reject(new Error("账号资料未刷新，请点击刷新")); });
    expect(onError).toHaveBeenCalledWith("账号资料未刷新，请点击刷新");
    expect(vi.getTimerCount()).toBe(0);
  });

  it("慢请求不重叠，旧会话返回不能覆盖新会话", async () => {
    vi.useFakeTimers();
    let resolve!: (session: BrowserLoginSession) => void;
    vi.mocked(fetchJson).mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
    const onSession = vi.fn(), onCookieReady = vi.fn(), onError = vi.fn();
    const { rerender, unmount } = renderHook(({ session }) => useBrowserLoginPolling({
      session, onSession, onCookieReady, onError,
    }), { initialProps: { session: waiting } });
    await act(async () => { await vi.advanceTimersByTimeAsync(4500); });
    expect(fetchJson).toHaveBeenCalledTimes(1);
    rerender({ session: { ...waiting, session_id: "new" } });
    await act(async () => { resolve({ ...waiting, cookie_ready: true }); });
    expect(onSession).not.toHaveBeenCalled();
    expect(onCookieReady).not.toHaveBeenCalled();
    vi.mocked(fetchJson).mockResolvedValueOnce({ ...waiting, session_id: "new" });
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(onSession).toHaveBeenCalledWith(expect.objectContaining({ session_id: "new" }));
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("Cookie 资料刷新失败不误记成功，下轮可以重新刷新", async () => {
    vi.useFakeTimers();
    vi.mocked(fetchJson).mockResolvedValue({ ...waiting, cookie_ready: true });
    const onCookieReady = vi.fn().mockRejectedValueOnce(new Error("temporary failure")).mockResolvedValue(undefined);
    const onError = vi.fn();
    renderHook(() => {
      const [session, onSession] = useState(waiting);
      useBrowserLoginPolling({ session, onSession, onCookieReady, onError });
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(onError).toHaveBeenCalledWith("temporary failure");
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(onCookieReady).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(onCookieReady).toHaveBeenCalledTimes(2);
  });
});
