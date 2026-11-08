import type { Filters, AttachmentSummary, Cursor, ViewMode, Message } from "../../shared/contracts";
import {normalizeDateHours} from './viewerModel';

export const PAGE_SIZE = 100;

export const emptyFilters: Filters = {
  accountId: "",
  groupId: "",
  user: "",
  keyword: "",
  dateFrom: "",
  dateTo: "",
  messageType: "",
  hasAttachment: "",
};

export const emptyAttachmentSummary: AttachmentSummary = {
  total_count: 0,
  message_count: 0,
  by_type: {},
};

export function buildMessageQuery(
  filters: Filters,
  cursor: Cursor | null = null,
  viewMode: ViewMode = "active",
): string {
  filters = normalizeDateHours(filters);
  const params = new URLSearchParams();
  if (filters.viewer) params.set('viewer', filters.viewer);

  if (filters.accountId) params.set("account_id", filters.accountId);
  if (filters.groupId) params.set("group_id", filters.groupId);
  if (filters.user.trim()) params.set("user", filters.user.trim());
  if (filters.keyword.trim()) params.set("keyword", filters.keyword.trim());
  if (filters.dateFrom) params.set("date_from", `${filters.dateFrom} 00:00:00`);
  if (filters.dateTo) params.set("date_to", `${filters.dateTo} 23:59:59`);
  if (filters.messageType) params.set("message_type", filters.messageType);
  if (filters.hasAttachment) params.set("has_attachment", filters.hasAttachment);
  if (viewMode === "deleted") {
    params.set("include_deleted", "true");
    params.set("deleted_only", "true");
  }
  if (cursor) {
    params.set("before_sent_at", cursor.before_sent_at);
    params.set("before_id", String(cursor.before_id));
  }
  params.set("limit", String(PAGE_SIZE));

  return params.toString();
}

export function buildFilterPayload(filters: Filters) {
  filters = normalizeDateHours(filters);
  return {
    viewer: filters.viewer || null,
    account_id: filters.accountId ? Number(filters.accountId) : null,
    group_id: filters.groupId ? Number(filters.groupId) : null,
    user: filters.user.trim() || null,
    keyword: filters.keyword.trim() || null,
    date_from: filters.dateFrom ? `${filters.dateFrom} 00:00:00` : null,
    date_to: filters.dateTo ? `${filters.dateTo} 23:59:59` : null,
    message_type: filters.messageType || null,
    has_attachment:
      filters.hasAttachment === "" ? null : filters.hasAttachment === "true",
  };
}

export function formatMessageType(type: string): string {
  const labels: Record<string, string> = {
    text: "文本",
    image: "图片",
    file: "文件",
    link: "链接",
    video: "视频",
    system: "系统",
  };
  return labels[type] ?? type;
}

export function groupMessagesByDate(messages: Message[]) {
  return messages.reduce<Array<{ date: string; items: Message[] }>>((groups, message) => {
    const date = message.sent_at.slice(0, 10) || "未知日期";
    const last = groups[groups.length - 1];
    if (last && last.date === date) {
      last.items.push(message);
    } else {
      groups.push({ date, items: [message] });
    }
    return groups;
  }, []);
}
