import { useEffect, useRef } from "react";
import type { BrowserLoginSession, BrowserLoginSessionStatus } from "../../shared/contracts";
import { fetchJson } from "../../shared/http";

export const terminalBrowserSessionStatuses = new Set<BrowserLoginSessionStatus>([
  "completed", "cancelled", "timed_out", "failed",
]);

type Options = {
  session: BrowserLoginSession | null;
  onSession: (session: BrowserLoginSession) => void;
  onCookieReady: () => Promise<unknown>;
  onError: (message: string) => void;
};

export function useBrowserLoginPolling(options: Options) {
  const latest = useRef(options);
  const inFlight = useRef(false);
  const mounted = useRef(false);
  const pendingRefresh = useRef<string | null>(null);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useEffect(() => { latest.current = options; });
  const sessionId = options.session?.session_id;
  const active = Boolean(options.session && !terminalBrowserSessionStatuses.has(options.session.status));

  useEffect(() => {
    if (!sessionId || !active) return;
    const currentId = sessionId;
    let cancelled = false;
    const isCurrent = () => !cancelled && latest.current.session?.session_id === sessionId;

    async function poll() {
      if (inFlight.current) return;
      inFlight.current = true;
      try {
        const session = await fetchJson<BrowserLoginSession>(
          `/api/weibo-api/browser-sessions/${sessionId}`, "浏览器登录状态读取",
        );
        if (!isCurrent()) return;
        if (session.cookie_ready && !latest.current.session?.cookie_ready) pendingRefresh.current = currentId;
        latest.current.onSession(session);
        if (session.cookie_ready && pendingRefresh.current === sessionId) {
          await latest.current.onCookieReady();
          // Only acknowledge a successful refresh; failures remain retryable.
          pendingRefresh.current = null;
        }
        if (mounted.current && latest.current.session?.session_id === currentId &&
          session.error_message && terminalBrowserSessionStatuses.has(session.status)) {
          latest.current.onError(session.error_message);
        }
      } catch (error) {
        if (mounted.current && latest.current.session?.session_id === currentId) {
          latest.current.onError(error instanceof Error ? error.message : "浏览器登录状态读取失败");
        }
      } finally {
        inFlight.current = false;
      }
    }

    const timer = window.setInterval(() => { void poll(); }, 1500);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [sessionId, active]);
}
