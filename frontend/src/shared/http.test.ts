import { describe, expect, it, vi } from "vitest";
import { fetchJson } from "./http";

describe("HTTP 重试边界", () => {
  it("写请求失败不重试，防止重复创建或删除", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "state changed" }), { status: 409 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchJson("/api/action", "操作", { method: "POST" })).rejects.toThrow("409");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("读请求暂时失败后最多尝试两次", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response("busy", { status: 503 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const result = fetchJson("/api/state", "读取");
    await vi.advanceTimersByTimeAsync(300);
    expect(await result).toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
