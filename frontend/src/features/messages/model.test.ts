import { describe, expect, it } from "vitest";
import { buildFilterPayload, buildMessageQuery, emptyFilters, formatMessageType } from "./model";

describe("消息筛选契约", () => {
  it("账号和群独立传参，不需要同时选择", () => {
    const account = new URLSearchParams(buildMessageQuery({ ...emptyFilters, accountId: "2" }));
    expect(account.get("account_id")).toBe("2");
    expect(account.has("group_id")).toBe(false);
    const group = new URLSearchParams(buildMessageQuery({ ...emptyFilters, groupId: "11" }));
    expect(group.get("group_id")).toBe("11");
    expect(group.has("account_id")).toBe(false);
  });

  it("搜索和删除采用一致的筛选与全天边界", () => {
    const filters = { ...emptyFilters, accountId: "1", groupId: "11", dateFrom: "2026-08-23",
      dateTo: "2026-08-23", keyword: "  词句  ", messageType: "link", hasAttachment: "false" };
    const query = new URLSearchParams(buildMessageQuery(filters));
    const payload = buildFilterPayload(filters);
    expect(payload).toMatchObject({ account_id: 1, group_id: 11, date_from: "2026-08-23 00:00:00",
      date_to: "2026-08-23 23:59:59", keyword: "词句", message_type: "link", has_attachment: false });
    for (const [key, value] of Object.entries(payload)) {
      if (value !== null) expect(query.get(key)).toBe(String(value));
    }
  });

  it("分页保留筛选且回收站显式请求已删除消息", () => {
    const query = new URLSearchParams(buildMessageQuery({ ...emptyFilters, accountId: "2" },
      { before_sent_at: "2026-08-23 10:00:00", before_id: 42 }, "deleted"));
    expect(Object.fromEntries(query)).toMatchObject({ account_id: "2", include_deleted: "true",
      deleted_only: "true", before_id: "42", before_sent_at: "2026-08-23 10:00:00" });
    expect(new URLSearchParams(buildMessageQuery(emptyFilters)).has("deleted_only")).toBe(false);
  });

  it("链接有中文标签，未知类型保留原值", () => {
    expect(formatMessageType("link")).toBe("链接");
    expect(formatMessageType("future-type")).toBe("future-type");
  });
});
