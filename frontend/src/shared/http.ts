export const API_BASE_URL = "";

export async function fetchJson<T>(path: string, label: string, init?: RequestInit): Promise<T> {
  const url = `${API_BASE_URL}${path}`;
  const maxAttempts = init?.method && init.method !== "GET" ? 1 : 2;

  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      const response = await fetch(url, {
        cache: "no-store",
        credentials: "same-origin",
        ...init,
      });
      if (response.ok) {
        return (await response.json()) as T;
      }

      const body = await response.text().catch(() => "");
      let detail = body.trim().slice(0, 200);
      if (body) {
        try {
          const parsed = JSON.parse(body) as { detail?: unknown };
          if (typeof parsed.detail === "string") detail = parsed.detail.slice(0, 200);
          else if (
            typeof parsed.detail === "object" &&
            parsed.detail !== null &&
            "message" in parsed.detail &&
            typeof (parsed.detail as { message?: unknown }).message === "string"
          ) {
            detail = (parsed.detail as { message: string }).message.slice(0, 200);
          }
        } catch {
          // Keep the bounded plain-text body when the response is not JSON.
        }
      }
      if (attempt < maxAttempts && response.status >= 500) {
        await new Promise((resolve) => window.setTimeout(resolve, 300));
        continue;
      }

      throw new Error(
        `${label}失败：HTTP ${response.status}${response.statusText ? ` ${response.statusText}` : ""}，接口 ${url}${
          detail ? `，返回：${detail}` : ""
        }`,
      );
    } catch (requestError) {
      if (attempt < maxAttempts) {
        await new Promise((resolve) => window.setTimeout(resolve, 300));
        continue;
      }
      throw requestError;
    }
  }

  throw new Error(`${label}失败：接口 ${url}`);
}
