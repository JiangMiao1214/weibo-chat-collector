// Reproducible browser regression: production UI, fake HTTP, isolated Chromium.
// No backend process, user data directory, real Cookie or remote request is used.
import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { createServer as createHttpServer } from "node:http";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { mkdir, readFile } from "node:fs/promises";
import {insightsResponse,checkInsights} from './insights-layout.mjs';
import {managementResponse,checkManagement} from './management-layout.mjs';
import { checkLongReaderLayout } from './reader-layout.mjs';
import {contextScrollResponse, checkContextScroll} from './context-scroll.mjs';
import {prependResponse, checkMessagePrepend} from './message-prepend.mjs';
import {joinNoticeResponse, checkJoinNotices} from './join-notices.mjs';
import {batchResponse, checkBatchScopes} from './batch-scopes.mjs';
import { testBrowserOptions } from './browser-options.mjs';
import { recordTestExport } from './export-records.mjs';
import {archivedPictures, archiveImageResponse, checkArchiveImprovements} from './archive-layout.mjs';

const helperRequire = createRequire(new URL("../../browser-helper/package.json", import.meta.url));
const puppeteer = helperRequire("puppeteer");
const { resolveChromePath } = helperRequire("./src/chrome-path.js");
const root = fileURLToPath(new URL("..", import.meta.url));

function fixtureApi() {
  const state = { polls: 0, options: 0, queries: [], quoteQueries: [], viewerRequests: [], unexpected: [], longReader: false, archiveActions: [], archiveEnabled: false, archivePaused: false, archiveSkipped: false, archiveSkipRequest: null };
  const accounts = [{ id: 1, display_name: "账号甲" }, { id: 2, display_name: "账号乙" }, {id:3,display_name:'待删除模拟账号'}];
  const groups = [{ id: 11, account_id: 1, name: "甲群", source_group_id: "1001" },
    { id: 21, account_id: 2, name: "乙群", source_group_id: "2001" }, {id:31,account_id:3,name:'模拟空群',source_group_id:null}];
  const session = () => ({ session_id: "isolated-test", account_id: 1, group_id: 11,
    status: state.polls >= 3 ? "cancelled" : "awaiting_group_selection",
    cookie_ready: state.polls >= 1, error_message: null });
  const message = (id, type) => ({ id, account_id: 1, account_name: "账号甲", group_id: 11,
    group_name: "甲群", user_id: 7, sender_name: "测试用户", is_deleted: 0,
    sent_at: type === "image" ? "2026-08-23 10:01:00" : "2026-08-23 10:00:00",
    message_type: type, content_text: type === "image"
      ? "「测试链接 https://fixture.invalid/article」 - - - 两张测试图片"
      : "测试链接 https://fixture.invalid/article",
    attachment_count: type === "image" ? 2 : 1,
    link_previews: type === 'link' ? [{url: 'https://fixture.invalid/article', author: '测试作者',
      title: '模拟微博分享', text: '来自已入库数据的预览正文', reposts: 3, comments: 4, likes: 5}] : [] });
  const messages = [message(1, "image"), message(2, "link")];
  const longMessages = [messages[0], ...Array.from({length: 80}, (_, index) => ({...message(100 + index, 'text'),
    content_text: `长列表模拟消息 ${index}，用于验证阅读区滚动与引用定位。`,
    sent_at: '2026-08-23 10:00:30', attachment_count: 0})), messages[1]];
  const detailFor = (selected) => ({ ...selected, archives: state.archivedImages && selected.message_type === 'image' ? archivedPictures() : [], attachments: Array.from({ length: selected.attachment_count }, (_, i) => ({
    id: i + 1, attachment_type: selected.message_type, title: `测试附件${i + 1}`,
    source_url: `https://fixture.invalid/attachment-${i + 1}`, download_status: "pending",
  })) });
  function middleware(req, res, next) {
    const url = new URL(req.url, "http://fixture.test");
    if (!url.pathname.startsWith("/api/")) return next();
    if (batchResponse(req, res, url, state)) return;
    if (joinNoticeResponse(res, url, state)) return;
    if (prependResponse(res, url, state)) return;
    if (contextScrollResponse(res, url, state)) return;
    if (archiveImageResponse(url, res)) return;
    if (insightsResponse(req,res,url,state)) return;
    if (managementResponse(req,res,url,state,accounts,groups,messages)) return;
    let body;
    const archiveStatus = () => ({account_id: 1, group_id: 11, enabled: state.archiveEnabled,
      paused: state.archivePaused, counts: state.archiveEnabled ? {stored: 2, queued: 1, downloading: state.archivePaused ? 0 : 3, skipped: Number(state.archiveSkipped)} : {},
      active_jobs: state.archiveEnabled && !state.archivePaused ? [1, 2, 3].map(id =>
        ({id, message_id: id, kind: 'image', source_url: `https://fixture.invalid/active-${id}`})) : [],
      errors: state.archiveEnabled ? [{id: 9, message_id: 101, kind: 'link', status: state.archiveSkipped ? 'skipped' : 'blocked',
        source_url: 'https://fixture.invalid/article?description=' + 'long-fixture-url-'.repeat(12),
        error: state.archiveSkipped ? '已跳过：用户确认此链接不可访问，未保存本地' : '微博原文不可访问'}] : []});
    switch (url.pathname) {
      case '/api/archives/status': body = archiveStatus(); break;
      case '/api/archives/control':
      case '/api/archives/9/skip': {
        let input = '';
        req.on('data', chunk => {input += chunk;});
        req.on('end', () => {
          const action = JSON.parse(input);
          if (url.pathname.endsWith('/skip')) {
            state.archiveSkipped = true;
            state.archiveSkipRequest = action;
          } else {
            state.archiveActions.push(action);
            state.archiveEnabled = true;
            state.archivePaused = action.action === 'pause';
          }
          res.setHeader('Content-Type', 'application/json');
          res.end(JSON.stringify(archiveStatus()));
        });
        return;
      }
      case '/api/message-view/facets':
        body = {members: [{id: 7, display_name: '测试用户', count: 2, avatar_url: null}],
          hours: Array.from({length: 24}, (_, h) => h === 10 ? 2 : 0), hidden_noise: 0};
        if (state.longReader) body.members = Array.from({length: 40}, (_, index) => ({id: index + 7,
          display_name: `模拟成员${index}`, count: 82 - index, avatar_url: null}));
        break;
      case '/api/message-view/1/context':
      case '/api/message-view/2/context':
      case '/api/message-view/3/context':
        body = {target_id: Number(url.pathname.split('/')[3]), account_id: 1, group_id: 11,
          deleted_only: false, items: messages, has_older: true, has_newer: false};
        if (url.searchParams.get('direction') === 'older') {
          body = {...body, items: [{...messages[1], id: 3, content_text: '真正更早的上下文',
            sent_at: '2026-08-23 09:59:00'}], has_older: false, has_newer: true};
        }
        break;
      case '/api/message-view/matches':
        body = {total: 1, items: [messages[1]]};
        break;
      case '/api/message-view/export':
        req.on('data', chunk => state.viewerRequests.push(String(chunk)));
        res.setHeader('Content-Type', 'text/plain; charset=utf-8');
        res.setHeader('Content-Disposition', 'attachment; filename="weibo-chat-filtered.txt"');
        res.end('测试链接 https://fixture.invalid/article');
        return;
      case "/api/filter-options":
        state.options++;
        body = { accounts, groups, users: [], message_types: ["text", "image", "link", "file", "video", "system"] };
        break;
      case "/api/weibo-api/status":
        body = { accounts: accounts.map((a) => ({ ...a, cookie_profile: {
          account_id: a.id, authenticated: a.id === 1 && state.polls > 0, exists: state.polls > 0,
        } })) };
        break;
      case "/api/collection-jobs": body = { items: [], total: 0 }; break;
      case "/api/weibo-api/browser-sessions/active": body = { session: session() }; break;
      case "/api/weibo-api/browser-sessions/isolated-test": state.polls++; body = session(); break;
      case "/api/messages": {
        state.queries.push(url.searchParams.toString());
        const type = url.searchParams.get("message_type");
        const source = state.longReader ? longMessages : messages;
        const items = type ? source.filter((m) => m.message_type === type) : source;
        body = { items, total: items.length, attachment_summary: type === "link"
          ? { total_count: 1, message_count: 1, by_type: { link: 1 } }
          : type === "image" ? { total_count: 2, message_count: 1, by_type: { image: 2 } }
          : { total_count: 3, message_count: 2, by_type: { image: 2, link: 1 } },
        has_more: false, next_cursor: null };
        break;
      }
      case "/api/messages/1":
      case "/api/messages/2": {
        const selected = messages[Number(url.pathname.split("/").pop()) - 1];
        body = detailFor(selected);
        break;
      }
      case "/api/messages/1/quote-context": {
        state.quoteQueries.push(Object.fromEntries(url.searchParams));
        body = { source_message_id: 1, account_id: 1, group_id: 11, deleted_only: false,
          match_method: state.referenceChoice ? "manual" : "full_text", status: "matched", target: detailFor(messages[1]), items: messages,
          has_older: false, has_newer: false };
        break;
      }
      default:
        state.unexpected.push(`${req.method} ${url.pathname}`);
        res.statusCode = 500;
        body = { detail: "Unexpected fixture request" };
    }
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify(body));
  }
  return { state, middleware };
}

async function assertDetailsLayout(page) {
  const sizes = await page.$eval('.reader-detail-wrap', wrap => {
    const body = wrap.querySelector('.detail-body');
    const header = wrap.querySelector('.panel-title');
    const close = wrap.querySelector('.reader-detail-close');
    return {available: wrap.clientWidth, body: body.getBoundingClientRect().width,
      fits: body.scrollWidth <= body.clientWidth,
      closeInHeader: header.contains(close),
      headerAboveBody: header.getBoundingClientRect().bottom <= body.getBoundingClientRect().top + 1};
  });
  assert.ok(Math.abs(sizes.available - sizes.body) <= 2,
    `详情正文应使用整栏宽度：可用 ${sizes.available}px，实际 ${sizes.body}px`);
  assert.equal(sizes.fits, true, '详情正文及附件不应横向溢出');
  assert.equal(sizes.closeInHeader, true, '关闭按钮应属于标题行，不占用正文栏宽');
  assert.equal(sizes.headerAboveBody, true, '标题和关闭按钮不得遮挡正文');
}

test("扫码状态、聊天列表与引用定位、附件汇总与详情、链接筛选、账号群隔离", { timeout: 60000 }, async (t) => {
  const fixture = fixtureApi();
  const server = await createServer({ configFile: false, root, logLevel: "error",
    server: { middlewareMode: true, hmr: false },
    plugins: [{ name: "isolated-test-api", configureServer(s) { s.middlewares.use(fixture.middleware); } }],
  });
  t.after(() => server.close());
  const httpServer = createHttpServer(server.middlewares);
  t.after(() => new Promise((resolve) => { httpServer.closeAllConnections(); httpServer.close(resolve); }));
  await new Promise((resolve, reject) => { httpServer.once("error", reject); httpServer.listen(0, "127.0.0.1", resolve); });
  const address = httpServer.address();
  const origin = `http://127.0.0.1:${address.port}`;
  const browser = await puppeteer.launch({...testBrowserOptions(resolveChromePath(process.env.TDD_CHROME_PATH)),
    ignoreDefaultArgs: ['--hide-scrollbars']});
  t.after(() => browser.close());
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => {
    const create = URL.createObjectURL;
    URL.createObjectURL = function(blob) {
      window.__testExportText = blob.text();
      return create.call(this, blob);
    };
  });
  await page.setViewport({ width: 1440, height: 1000 });
  page.setDefaultTimeout(10000);
  await page.setRequestInterception(true);
  page.on("request", (request) => {
    if (request.url().startsWith(origin + "/")) void request.continue();
    else void request.abort();
  });
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  // Puppeteer filter closures cannot capture Node variables; use grounded XPath.
  const button = (label) => `xpath///button[normalize-space(.)='${label}']`;
  const select = (label) => `xpath///label[span[normalize-space(.)='${label}']]/select`;
  await page.goto(origin);
  await page.waitForFunction(() => document.body.innerText.includes("Cookie 未就绪"));
  await page.waitForFunction(() => document.body.innerText.includes("Cookie 已就绪"));
  await page.waitForFunction(() => document.body.innerText.includes("已取消"));
  // React.StrictMode mounts bootstrap twice in dev; count once per bootstrap + ready transition.
  assert.equal(fixture.state.options, 3);
  await page.locator(button("消息")).click();
  await page.waitForFunction(() => document.querySelector(".attachment-summary")?.textContent.includes("附件总数 3"));
  assert.equal(await page.$('.reader-detail-wrap'), null);
  assert.equal(await page.$('.hours-strip'), null, '无日期时整个小时区不占空间');
  assert.equal(await page.$(button('清除小时')), null);
  assert.ok(await page.$eval('.message-list', el => el.clientHeight >= 450));
  assert.equal(await page.$('[aria-label="成员侧栏"] select'), null);
  assert.equal(await page.$(select('消息类型')), null);
  await page.locator('[aria-label="查看消息 #1"]').click();
  await page.waitForFunction(() => document.querySelectorAll(".attachment-item").length === 2);
  await assertDetailsLayout(page);
  assert.equal(await page.$eval(".attachment-summary", (el) => el.textContent.includes("含附件消息 2")), true);
  assert.equal(await page.$$eval(".chat-message", (els) => els.length), 2);
  assert.deepEqual(await page.$$eval('.chat-message', els => els.map(el => el.getAttribute('aria-label'))), ['消息 #2', '消息 #1']);
  assert.equal(await page.$('.chat-message .quote-lookup-trigger'), null, '已加载引用不重复展示数据库定位入口');
  assert.equal(await page.$eval('[aria-label="消息 #1"]', (el) => el.dataset.continuation), "true");
  assert.equal(await page.$eval(".chat-link-card", (el) => el.href), "https://fixture.invalid/article");
  assert.equal(await page.$(".chat-workspace button button, .chat-workspace button a"), null);
  await page.locator('[aria-label="查看匹配消息 #2"]').click();
  await page.waitForFunction(() => document.querySelector('[aria-label="查看消息 #2"]')?.getAttribute("aria-pressed") === "true");
  assert.equal(await page.evaluate(() => document.activeElement?.getAttribute("aria-label")), "查看消息 #2");
  await page.waitForFunction(() => document.querySelectorAll(".attachment-item").length === 1);
  await page.locator('[aria-label="消息 #1"] .chat-text').click();
  await page.waitForFunction(() => document.querySelectorAll(".attachment-item").length === 2);
  const artifacts = new URL("../../.tdd-results/ui/", import.meta.url);
  await mkdir(artifacts, { recursive: true });
  await (await page.$(".chat-workspace")).screenshot({ path: fileURLToPath(new URL("message-list-desktop.png", artifacts)) });
  await page.setViewport({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
  await assertDetailsLayout(page);
  await (await page.$('.reader-detail-wrap')).screenshot({path: fileURLToPath(new URL('reader-detail-mobile.png', artifacts))});
  await (await page.$(".message-panel")).screenshot({ path: fileURLToPath(new URL("message-list-mobile.png", artifacts)) });
  await page.setViewport({ width: 1440, height: 1000 });
  assert.equal(fixture.state.quoteQueries.length, 0);
  await page.locator('[aria-label="媒体：图片"]').click();
  await page.waitForFunction(() => document.querySelector(".attachment-summary")?.textContent.includes("附件总数 2"));
  assert.equal(await page.$('[aria-label="消息 #2"]'), null);
  const listState = await page.$eval(".message-list", (el) => ({ scrollTop: el.scrollTop, text: el.textContent }));
  await page.locator(button("查看引用原文")).click();
  await page.waitForSelector('dialog[open] [aria-label="匹配消息 #2"]');
  assert.deepEqual(await page.$$eval('.quote-context-message', els => els.map(el => el.getAttribute('aria-label'))), ['匹配消息 #2', '上下文消息 #1']);
  assert.deepEqual(fixture.state.quoteQueries, [{ account_id: "1", group_id: "11", deleted_only: "false" }]);
  assert.equal(await page.$eval(".quote-context-dialog", (el) => el.matches(":modal")), true);
  await (await page.$(".quote-context-dialog")).screenshot({ path: fileURLToPath(new URL("quote-context-desktop.png", artifacts)) });
  await page.setViewport({ width: 390, height: 844 });
  assert.equal(await page.$eval(".quote-context-dialog", (el) => el.scrollWidth <= el.clientWidth), true);
  await (await page.$(".quote-context-dialog")).screenshot({ path: fileURLToPath(new URL("quote-context-mobile.png", artifacts)) });
  await page.setViewport({ width: 1440, height: 1000 });
  await page.keyboard.press("Escape");
  await page.waitForFunction(() => !document.querySelector("dialog"));
  assert.equal(await page.evaluate(() => document.activeElement?.textContent), "查看引用原文");
  assert.equal(await page.$eval('[aria-label="媒体：图片"]', el => el.getAttribute('aria-pressed')), 'true');
  assert.deepEqual(await page.$eval(".message-list", (el) => ({ scrollTop: el.scrollTop, text: el.textContent })), listState);
  assert.equal(await page.$eval('[aria-label="查看消息 #1"]', (el) => el.getAttribute("aria-pressed")), "true");
  await page.locator('[aria-label="媒体：链接"]').click();
  await page.waitForFunction(() => document.querySelector(".attachment-summary")?.textContent.includes("附件总数 1"));
  assert.ok(fixture.state.queries.some((query) => new URLSearchParams(query).get("message_type") === "link"));
  assert.equal(await page.$$eval(".chat-message", (els) => els.length), 1);
  await page.waitForSelector('[aria-label="筛选成员 测试用户 #7，2 条"]');
  await page.locator('[aria-label="筛选成员 测试用户 #7，2 条"]').click();
  await page.waitForFunction(() => document.querySelector('[aria-label="筛选成员 测试用户 #7，2 条"]')?.getAttribute('aria-pressed') === 'true');
  await page.$eval('[aria-label="阅读日期"]', el => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, '2026-08-23');
    el.dispatchEvent(new Event('input', {bubbles:true}));
  });
  await page.waitForSelector('[aria-label="10 时，2 条"]');
  await page.locator('[aria-label="10 时，2 条"]').click();
  await page.waitForFunction(() => document.querySelector('[aria-label="10 时，2 条"]')?.getAttribute('aria-pressed') === 'true');
  assert.ok(fixture.state.queries.some(query => JSON.parse(new URLSearchParams(query).get('viewer') || '{}').hours?.includes(10)));
  const exportResponse = page.waitForResponse(response => response.url().endsWith('/api/message-view/export'));
  const exportRecordedAt = new Date().toISOString();
  await page.locator(button('导出当前筛选结果')).click();
  const exported = await exportResponse;
  assert.equal(exported.status(), 200);
  await page.waitForFunction(() => document.querySelector('.export-results button')?.textContent === '导出当前筛选结果' && window.__testExportText);
  const exportText = await page.evaluate(() => window.__testExportText);
  assert.equal(exportText, '测试链接 https://fixture.invalid/article');
  const exportDocument = new URL('../../docs/test-export-records.md', import.meta.url);
  await recordTestExport(exportDocument, exportText, exportRecordedAt);
  assert.ok((await readFile(exportDocument, 'utf8')).includes(`| ${exportRecordedAt} | 测试链接 https://fixture.invalid/article |`),
    '每次成功的测试导出应记录到 Markdown，而不是下载文件');
  await page.waitForFunction(() => document.querySelector('.export-results button')?.textContent === '导出当前筛选结果');
  assert.equal(JSON.parse(fixture.state.viewerRequests.join('')).filters.message_type, 'link');
  const beforeContext = await page.$eval('.message-list', el => ({text: el.textContent, scrollTop: el.scrollTop}));
  await page.locator('[aria-label="上下文 #2"]').click();
  try {
    await page.waitForSelector('[aria-label="消息上下文"] [aria-label="跳到列表 #1"]');
  } catch (error) {
    await page.screenshot({path:fileURLToPath(new URL('context-open-failure.png',artifacts))});
    throw new Error(`${error.message}; fixture context: ${await page.$eval('.chat-workspace',el=>el.innerText)}; page errors: ${errors.join(';')}`);
  }
  assert.deepEqual(await page.$$eval('.context-row button[aria-label^="跳到列表"]', els => els.map(el => el.getAttribute('aria-label'))), ['跳到列表 #2', '跳到列表 #1']);
  await page.locator(button('查看更早上下文')).click();
  await page.waitForFunction(() => document.querySelector('.context-rows')?.textContent.includes('真正更早的上下文'));
  assert.equal(await page.$eval(button('查看更早上下文'), el => el.disabled), true);
  assert.deepEqual(await page.$eval('.message-list', el => ({text: el.textContent, scrollTop: el.scrollTop})), beforeContext);
  assert.equal(await page.$eval(button('查看更新上下文'), el => el.disabled), true, '连续窗口仍含原最新消息，无须重复加载');
  assert.ok(await page.$('.reader-context [data-context-id="2"]'), '追加更早消息不丢弃原窗口');
  await page.waitForSelector('[aria-label="消息上下文"] [aria-label="跳到列表 #1"]');
  await page.locator(button('回到原消息')).click();
  await page.waitForFunction(() => ![...document.querySelectorAll('button')].some(el => el.textContent === '回到原消息'));
  await (await page.$('.chat-workspace')).screenshot({path: fileURLToPath(new URL('reader-context-desktop.png', artifacts))});
  await page.locator('[aria-label="跳到列表 #1"]').click();
  await page.waitForSelector('[aria-label="消息 #1"]');
  assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')), '查看消息 #1');
  assert.equal(await page.$eval(button('预览删除数量'), el => el.disabled), true);
  await page.locator(button('返回筛选列表')).click();
  await page.waitForFunction(() => !document.querySelector('[aria-label="消息 #1"]'));
  assert.deepEqual(await page.$eval('.message-list', el => ({text: el.textContent, scrollTop: el.scrollTop})), beforeContext);
  await page.locator('[aria-label="正文查找（保留上下文）"]').fill('测试链接');
  await page.waitForFunction(() => document.querySelector('[aria-label="匹配消息进度"]')?.textContent.includes('1 条匹配消息'));
  await page.locator(button('下一个匹配')).click();
  assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')), '查看消息 #2');
  assert.ok(await page.$('.chat-text mark'));
  assert.equal(await page.$('.rich-link-card'), null);
  assert.equal(await page.$eval('.chat-link-card', el => el.href), 'https://fixture.invalid/article');
  assert.equal(await page.$eval('.message-list', el => el.textContent.includes('来自已入库数据的预览正文')), false);
  await page.setViewport({width: 390, height: 844});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
  await (await page.$('.message-panel')).screenshot({path: fileURLToPath(new URL('reader-mobile.png', artifacts))});
  await page.screenshot({path: fileURLToPath(new URL('reader-mobile-full.png', artifacts))});
  await page.setViewport({width: 1366, height: 900});
  assert.ok(await page.$eval('.message-list', el => el.clientHeight >= 450));
  await page.screenshot({path: fileURLToPath(new URL('reader-simple-desktop.png', artifacts))});
  await page.setViewport({width: 1440, height: 1000});
  const dateResponse = page.waitForResponse(response => response.url().includes('/api/messages?') && response.url().includes('date_from='));
  // Puppeteer's date fill assigns React's tracked value, suppressing onChange.
  // Use the native input setter and DOM event to model a calendar selection.
  await page.$eval('[aria-label="阅读日期"]', el => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, '2026-09-12');
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
  });
  const dateQuery = new URL((await dateResponse).url()).searchParams;
  assert.equal(dateQuery.get('date_from'), '2026-09-12 00:00:00');
  assert.equal(dateQuery.get('date_to'), '2026-09-12 23:59:59');
  const allDateResponse = page.waitForResponse(response => response.url().includes('/api/messages?') && !response.url().includes('date_from='));
  await page.locator(button('全部日期')).click();
  const allDateQuery = new URL((await allDateResponse).url()).searchParams;
  assert.deepEqual(JSON.parse(allDateQuery.get('viewer')).hours, []);
  assert.equal(await page.$('.hours-strip'), null);
  assert.equal(await page.$(button('清除小时')), null);
  await page.select(select("账号"), "1");
  await page.select(select("群聊"), "11");
  await page.select(select("账号"), "2");
  const groupState = await page.$eval(select("群聊"), (el) => ({ value: el.value, options: [...el.options].map((o) => o.value) }));
  assert.equal(groupState.value, "");
  assert.deepEqual(groupState.options, ["", "21"]);
  fixture.state.longReader = true;
  await checkLongReaderLayout(page, artifacts);
  await page.setViewport({width: 1440, height: 1000});
  await page.select(select('群聊'), '11');
  await page.locator(button('本地归档')).click();
  await page.waitForSelector('.archive-dialog[open]');
  await page.locator(button('开启本群归档并补下载历史')).click();
  await page.waitForFunction(() => document.querySelector('.archive-dialog')?.textContent.includes('文件已保存 2'));
  assert.deepEqual(fixture.state.archiveActions[0], {account_id: 1, group_id: 11, action: 'start'});
  await page.locator(button('暂停归档')).click();
  await page.waitForFunction(() => document.querySelector('.archive-dialog [role="status"]')?.textContent.includes('已暂停'));
  await page.locator(button('跳过此链接')).click();
  await page.waitForFunction(() => document.querySelector('.archive-dialog')?.textContent.includes('已跳过 1'));
  assert.equal(fixture.state.archivePaused, true);
  assert.deepEqual(fixture.state.archiveSkipRequest, {account_id: 1, group_id: 11, action: 'skip'});
  await page.locator(button('恢复归档并重试失败项')).click();
  await page.waitForFunction(() => document.querySelector('.archive-dialog [role="status"]')?.textContent.includes('已开启'));
  await page.waitForFunction(() => document.querySelector('.archive-dialog')?.textContent.includes('正在下载 3 / 3'));
  assert.equal(await page.$$eval('.archive-dialog a', links => links.length), 4);
  assert.equal(await page.$$eval('.archive-dialog a', links => links.some(link => link.href.startsWith('https://fixture.invalid/article?'))), true);
  await page.screenshot({path: fileURLToPath(new URL('archive-desktop.png', artifacts))});
  await page.setViewport({width: 390, height: 844});
  assert.equal(await page.$eval('.archive-dialog', el => el.scrollWidth <= el.clientWidth), true);
  await page.screenshot({path: fileURLToPath(new URL('archive-mobile.png', artifacts))});
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => !document.querySelector('.archive-dialog'));
  assert.equal(await page.evaluate(() => document.activeElement?.textContent), '本地归档');
  assert.deepEqual(fixture.state.archiveActions.map(item => item.action), ['start', 'pause', 'resume']);
  await checkArchiveImprovements(page, artifacts, fixture.state);
  await checkManagement(page, artifacts, fixture.state);
  await checkInsights(page, artifacts, fixture.state);
  await checkContextScroll(page, artifacts, fixture.state);
  await checkMessagePrepend(page, artifacts, fixture.state);
  await checkJoinNotices(page, artifacts, fixture.state);
  try { await checkBatchScopes(page, fixture.state); }
  catch (error) {
    await page.screenshot({path: fileURLToPath(new URL('batch-scope-failure.png', artifacts)), fullPage: true});
    console.error('Batch scope failure', {step: fixture.state.batchStep, writes: fixture.state.batchWrites,
      unexpected: fixture.state.unexpected, errors});
    throw error;
  }
  assert.deepEqual(fixture.state.unexpected, []);
  assert.deepEqual(errors, []);
});
