import { describe, expect, it } from "vitest";
import type { Message } from "../../shared/contracts";
import { bodyLinks, indexQuoteCandidates, isContinuation, resolveQuote, safeHttpUrl, splitQuote } from "./presentation";

const message = (extra: Partial<Message> = {}): Message => ({
  id: 1, account_id: 1, account_name: "甲", group_id: 11, group_name: "甲群", user_id: 7,
  sender_name: "同名", source_user_id: null, source_message_id: null,
  sent_at: "2026-08-23 10:00:00", message_type: "text", content_text: "完整原文", is_deleted: 0,
  deleted_at: null, attachment_count: 0, ...extra,
});

describe("聊天展示边界", () => {
  it("连续发言包含五分钟边界，但不跨日、不容忍缺失身份和无效时间", () => {
    expect(isContinuation(message(), message({ sent_at: "2026-08-23 10:05:00" }))).toBe(true);
    expect(isContinuation(message(), message({ sent_at: "2026-08-23 10:05:01" }))).toBe(false);
    expect(isContinuation(message({ sent_at: "2026-08-22 23:59:59" }), message({ sent_at: "2026-08-23 00:00:00" }))).toBe(false);
    expect(isContinuation(message(), message({ group_id: 12 }))).toBe(false);
    expect(isContinuation(message({ user_id: 0 }), message({ user_id: 0 }))).toBe(false);
    expect(isContinuation(undefined, message())).toBe(false);
    expect(isContinuation(message(), message({ sent_at: "invalid" }))).toBe(false);
  });

  it("支持嵌套引号与多连字符，不完整或不明确格式保持原文", () => {
    expect(splitQuote("「「内部」 - - - 引用的回复」 - - - - - - 外部回复")).toEqual({ quote: "「内部」 - - - 引用的回复", reply: "外部回复" });
    expect(splitQuote("「原文」\n- - -\n")).toEqual({ quote: "原文", reply: "" });
    for (const text of ["普通文本", "「缺少闭合 - - - 回复", "「没有分隔符」正文", "「」 - - - 回复", "文本 - - - 文本"]) {
      expect(splitQuote(text)).toBeNull();
    }
  });

  it("只匹配完整原文，拒绝同秒、未来、已删除和其他范围候选", () => {
    const source = message();
    const index = indexQuoteCandidates([source, message({ id: 2, sent_at: "2026-08-23 10:02:00" }),
      message({ id: 3, account_id: 2 }), message({ id: 4, group_id: 12 }), message({ id: 5, is_deleted: 1 })]);
    const reply = message({ id: 6, sent_at: "2026-08-23 10:01:00" });
    expect(resolveQuote(reply, "完整原文", index)).toEqual([source]);
    expect(resolveQuote(reply, "完整原", index)).toEqual([]);
    expect(resolveQuote(reply, "完整 原文", index)).toEqual([]);
    expect(resolveQuote(source, "完整原文", index)).toEqual([]);
  });

  it("跨已加载分页可以匹配，新增重复候选后撤销唯一定位", () => {
    const reply = message({ id: 3, sent_at: "2026-08-24 10:00:00", content_text: "「完整原文」 - - - 回复" });
    expect(resolveQuote(reply, "完整原文", indexQuoteCandidates([reply]))).toHaveLength(0);
    expect(resolveQuote(reply, "完整原文", indexQuoteCandidates([reply, message()]))).toHaveLength(1);
    expect(resolveQuote(reply, "完整原文", indexQuoteCandidates([reply, message(), message({ id: 2 })]))).toHaveLength(2);
  });

  it("HTTP 链接去重且保留查询，拒绝脚本、数据、相对地址和内嵌凭据", () => {
    expect(bodyLinks("https://example.com/a?q=1&b=2。\nhttps://example.com/a?q=1&b=2\nhttp://example.org/x")).toEqual([
      "https://example.com/a?q=1&b=2", "http://example.org/x",
    ]);
    for (const url of [null, "javascript:alert(1)", "data:image/png;base64,test", "file:///tmp/a", "/local", "//example.com", "https://u:p@example.com"]) {
      expect(safeHttpUrl(url)).toBeNull();
    }
  });
});
