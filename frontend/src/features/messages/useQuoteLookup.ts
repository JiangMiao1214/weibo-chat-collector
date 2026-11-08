import { useEffect, useState } from "react";
import type { Attachment, Message } from "../../shared/contracts";

export type QuoteResult = {
  source_message_id: number;
  account_id: number;
  group_id: number;
  deleted_only: boolean;
  match_method: "full_text" | "source_id" | "manual";
  status: "matched" | "ambiguous" | "not_found" | "not_quote";
  target: (Message & { attachments: Attachment[] }) | null;
  items: Message[];
  has_older: boolean;
  has_newer: boolean;
};

function validateScope(result: QuoteResult, source: Message) {
  const sameScope = (item: Message) => item.account_id === source.account_id &&
    item.group_id === source.group_id && Boolean(item.is_deleted) === Boolean(source.is_deleted);
  if (result.source_message_id !== source.id || result.account_id !== source.account_id ||
      result.group_id !== source.group_id || result.deleted_only !== Boolean(source.is_deleted) ||
      !result.items.every(sameScope) || (result.target && !sameScope(result.target))) {
    throw new Error("定位响应范围不一致，请刷新后重试");
  }
  if (result.status === "matched" && (!result.target || !result.items.some((m) => m.id === result.target?.id))) {
    throw new Error("定位结果不完整，请重试");
  }
}

export function useQuoteLookup(source: Message, attempt: number) {
  const requestKey = JSON.stringify([source.id, source.account_id, source.group_id, source.is_deleted, source.content_text, attempt]);
  const [state, setState] = useState({ key: "", data: null as QuoteResult | null, error: "" });
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    setState({ key: requestKey, data: null, error: "" });
    const timeout = window.setTimeout(() => {
      setState({ key: requestKey, data: null, error: "定位超时，请稍后重试" });
      active = false;
      controller.abort();
    }, 15000);
    const params = new URLSearchParams({ account_id: String(source.account_id), group_id: String(source.group_id),
      deleted_only: String(Boolean(source.is_deleted)) });
    async function lookup() {
      try {
        const response = await fetch(`/api/messages/${source.id}/quote-context?${params}`, {
          signal: controller.signal, cache: "no-store", credentials: "same-origin",
        });
        if (!response.ok) throw new Error(response.status === 404
          ? "原引用消息不存在或范围已变化（HTTP 404），请刷新列表" : `定位失败：HTTP ${response.status}`);
        const result: QuoteResult = await response.json();
        validateScope(result, source);
        if (active) setState({ key: requestKey, data: result, error: "" });
      } catch (reason) {
        if (active) setState({ key: requestKey, data: null, error: reason instanceof Error ? reason.message : "定位失败，请重试" });
      } finally {
        window.clearTimeout(timeout);
      }
    }
    // Let immediate cleanup (including StrictMode's probe mount) cancel before dispatch.
    void Promise.resolve().then(() => { if (active) return lookup(); });
    return () => { active = false; window.clearTimeout(timeout); controller.abort(); };
  }, [source.id, source.account_id, source.group_id, source.is_deleted, source.content_text, attempt, requestKey]);
  return state.key === requestKey ? state : { data: null, error: "" };
}
