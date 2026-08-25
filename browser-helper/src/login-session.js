'use strict';

const readline = require('readline');
const { resolveChromePath } = require('./chrome-path');
const { emit, extractGroupId, publicError, waitForUsableSubCookies } = require('./protocol');

const CHAT_URL = 'https://api.weibo.com/chat#/chat';
const timeoutSeconds = Math.max(30, Number(process.env.WEIBO_BROWSER_SESSION_TIMEOUT_SECONDS || 600));
const userDataDir = process.env.WEIBO_BROWSER_USER_DATA_DIR;

let browser = null;
let closing = false;
let loginCompleted = false;
let timeoutHandle = null;
const observedPages = new WeakSet();

function observeGroupRequests(page) {
  if (!page || observedPages.has(page)) return;
  observedPages.add(page);
  page.on('request', (request) => {
    const sourceGroupId = extractGroupId(request.url());
    if (sourceGroupId) emit({ event: 'group_candidate', source_group_id: sourceGroupId });
  });
}

async function waitForLoginCookies() {
  // QR confirmation, SPA navigation and the HttpOnly SUB write do not complete
  // atomically. Poll the browser context instead of taking a one-shot snapshot.
  return waitForUsableSubCookies(() => browser.cookies(), {
    shouldStop: () => closing,
  });
}

async function closeBrowser() {
  if (closing) return;
  closing = true;
  if (timeoutHandle) clearTimeout(timeoutHandle);
  if (browser) {
    try {
      await browser.close();
    } catch {
      // The parent process owns final process and temporary-directory cleanup.
    }
  }
}

async function fail(error, fallbackCode) {
  const safe = publicError(error, fallbackCode);
  emit({ event: 'error', error_code: safe.code, error_message: safe.message });
  await closeBrowser();
  process.exitCode = 1;
}

async function run() {
  let puppeteer;
  try {
    puppeteer = require('puppeteer');
  } catch {
    const error = new Error('Puppeteer is not installed.');
    error.code = 'browser_launch_failed';
    await fail(error, 'browser_launch_failed');
    return;
  }

  let chromePath;
  try {
    chromePath = resolveChromePath(process.env.WEIBO_BROWSER_CHROME_PATH || '');
  } catch (error) {
    await fail(error, 'browser_not_found');
    return;
  }

  try {
    browser = await puppeteer.launch({
      headless: false,
      executablePath: chromePath,
      defaultViewport: null,
      userDataDir,
      args: ['--no-first-run', '--window-size=1280,800'],
    });
  } catch (error) {
    await fail(error, 'browser_launch_failed');
    return;
  }

  browser.once('disconnected', () => {
    if (!closing) {
      emit({ event: 'error', error_code: 'browser_closed', error_message: '浏览器窗口已关闭。' });
      process.exitCode = 1;
    }
  });

  timeoutHandle = setTimeout(() => {
    void fail(Object.assign(new Error('Login session timed out.'), { code: 'login_timeout' }), 'login_timeout');
  }, timeoutSeconds * 1000);

  browser.on('targetcreated', (target) => {
    void target.page().then(observeGroupRequests).catch(() => {});
  });
  const pages = await browser.pages();
  pages.forEach(observeGroupRequests);
  const page = pages[0] || (await browser.newPage());
  observeGroupRequests(page);

  emit({ event: 'browser_opened' });
  try {
    await page.goto(CHAT_URL, { waitUntil: 'domcontentloaded', timeout: 30000 });
    emit({ event: 'awaiting_scan' });
    const cookies = await waitForLoginCookies();
    if (!cookies) return;
    emit({ event: 'login_detected' });
    emit({ event: 'cookies', cookies });
    loginCompleted = true;
    emit({ event: 'awaiting_group_selection' });
  } catch (error) {
    const timedOut = error?.name === 'TimeoutError';
    await fail(
      Object.assign(error instanceof Error ? error : new Error('Login failed.'), {
        code: timedOut ? 'login_timeout' : 'browser_launch_failed',
      }),
      timedOut ? 'login_timeout' : 'browser_launch_failed',
    );
  }
}

const commands = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
commands.on('line', (line) => {
  try {
    const command = JSON.parse(line);
    if (command?.command === 'close') {
      void closeBrowser().then(() => {
        emit({ event: 'closed', login_completed: loginCompleted });
        process.exit(0);
      });
    }
  } catch {
    void fail(Object.assign(new Error('Invalid parent command.'), { code: 'helper_protocol_error' }), 'helper_protocol_error');
  }
});

process.on('SIGTERM', () => {
  void closeBrowser().finally(() => process.exit(0));
});
process.on('SIGINT', () => {
  void closeBrowser().finally(() => process.exit(0));
});

void run();
