'use strict';

const TARGET_PATH = '/webim/groupchat/query_messages.json';
const TARGET_HOST = 'api.weibo.com';
const COOKIE_DOMAINS = ['weibo.com', 'sina.com.cn'];

function emit(event) {
  process.stdout.write(`${JSON.stringify(event)}\n`);
}

function extractGroupId(rawUrl) {
  try {
    const parsed = new URL(rawUrl);
    if (parsed.protocol !== 'https:' || parsed.hostname !== TARGET_HOST || parsed.pathname !== TARGET_PATH) {
      return null;
    }
    const groupId = parsed.searchParams.get('id');
    return groupId && /^\d+$/.test(groupId) ? groupId : null;
  } catch {
    return null;
  }
}

function isLoggedInText(bodyText) {
  const text = String(bodyText || '');
  if (text.includes('扫描登录') || text.includes('立即注册')) return false;
  return text.length > 500;
}

function hasUsableSubCookie(cookies, nowSeconds = Date.now() / 1000) {
  if (!Array.isArray(cookies)) return false;
  return cookies.some((cookie) => {
    if (!cookie || cookie.name !== 'SUB' || typeof cookie.value !== 'string' || !cookie.value) {
      return false;
    }
    const domain = String(cookie.domain || '').trim().toLowerCase().replace(/^\.+|\.+$/g, '');
    if (!COOKIE_DOMAINS.some((allowed) => domain === allowed || domain.endsWith(`.${allowed}`))) {
      return false;
    }
    if (TARGET_HOST !== domain && !TARGET_HOST.endsWith(`.${domain}`)) return false;
    const path = typeof cookie.path === 'string' && cookie.path.startsWith('/') ? cookie.path : '/';
    const pathMatches = TARGET_PATH === path || (
      TARGET_PATH.startsWith(path) && (path.endsWith('/') || TARGET_PATH.slice(path.length).startsWith('/'))
    );
    if (!pathMatches) return false;
    return typeof cookie.expires !== 'number' || cookie.expires <= 0 || cookie.expires > nowSeconds;
  });
}

async function waitForUsableSubCookies(
  readCookies,
  { intervalMs = 750, shouldStop = () => false, sleep = null } = {},
) {
  const pause = sleep || ((milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)));
  while (!shouldStop()) {
    const cookies = await readCookies();
    if (hasUsableSubCookie(cookies)) return cookies;
    await pause(intervalMs);
  }
  return null;
}

function publicError(error, fallbackCode = 'browser_launch_failed') {
  const code = typeof error?.code === 'string' ? error.code : fallbackCode;
  const allowedMessages = {
    browser_not_found: '未找到可用的 Chrome、Chromium 或 Edge。',
    browser_launch_failed: '浏览器启动失败。',
    login_timeout: '扫码登录已超时。',
    browser_closed: '浏览器窗口已关闭。',
    helper_protocol_error: '浏览器助手发生协议错误。',
  };
  return { code, message: allowedMessages[code] || allowedMessages[fallbackCode] };
}

module.exports = {
  TARGET_HOST,
  TARGET_PATH,
  emit,
  extractGroupId,
  hasUsableSubCookie,
  isLoggedInText,
  publicError,
  waitForUsableSubCookies,
};
