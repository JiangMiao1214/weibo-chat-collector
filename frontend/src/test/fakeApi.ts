import { vi } from "vitest";

export function fakeApi() {
  const accounts = [1, 2].map((id) => ({
    id, display_name: `账号${id}`, auth_type: "cookie", login_profile_name: null, is_active: 1,
  }));
  const groups = [
    { id: 11, account_id: 1, name: "甲群", source_group_id: "1001" },
    { id: 12, account_id: 1, name: "甲另一群", source_group_id: "1002" },
    { id: 21, account_id: 2, name: "乙群", source_group_id: "2001" },
  ];
  const state = {
    ready: false,
    session: null as null | {
      session_id: string; account_id: number; group_id: number; status: string;
      cookie_ready: boolean; error_message: string | null;
    },
  };
  const calls: Array<{ path: string; method: string; body?: string }> = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    calls.push({ path, method: init?.method ?? "GET", body: init?.body as string | undefined });
    const url = new URL(path, "http://test.local");
    let body: unknown;
    switch (url.pathname) {
      case "/api/filter-options":
        body = { accounts, groups, users: [], message_types: ["text", "image", "link", "file", "video", "system"] };
        break;
      case "/api/weibo-api/status":
        body = { accounts: accounts.map((a) => ({ ...a, cookie_profile: {
          account_id: a.id, exists: state.ready, authenticated: state.ready && a.id === 1,
          cookie_count: state.ready ? 1 : 0, updated_at: null,
        } })) };
        break;
      case "/api/collection-jobs":
        body = { items: [], total: 0 };
        break;
      case "/api/weibo-api/browser-sessions/active":
        body = { session: state.session };
        break;
      case "/api/weibo-api/browser-sessions/session-1":
        body = state.session;
        break;
      case "/api/messages":
        body = { items: [], total: 10, attachment_summary: {
          total_count: 15, message_count: 10, by_type: { image: 12, link: 3 },
        }, has_more: false, next_cursor: null };
        break;
      case '/api/message-view/facets':
        body = {members: [{id: 7, display_name: '测试用户', avatar_url: null, count: 10}], hours: Array(24).fill(0), hidden_noise: 0};
        break;
      case '/api/messages/delete-preview':
        body = {preview_count: 1, delete_mode: 'soft'};
        break;
      default:
        throw new Error(`Unexpected mock request: ${init?.method ?? "GET"} ${path}`);
    }
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { state, calls, fetchMock };
}
