import {checkRecoveryTime, recoveryResponses} from './recovery-time.mjs';
import {mkdtemp, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {createServer as createHttpServer} from 'node:http';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {testBrowserOptions} from './browser-options.mjs';
const require = createRequire(new URL('../../browser-helper/package.json', import.meta.url));
const puppeteer = require('puppeteer');
const {resolveChromePath} = require('./src/chrome-path.js');

test('采集任务时间支持实际键盘修改小时和分钟并提交所选范围', async t => {
  const cacheDir = await mkdtemp(join(tmpdir(), 'collection-time-vite-'));
  t.after(() => rm(cacheDir, {recursive: true, force: true}));
  const http = createHttpServer();
  await new Promise(resolve => http.listen(0, '127.0.0.1', resolve));
  const server = await createServer({cacheDir, configFile: false, root: fileURLToPath(new URL('..', import.meta.url)),
    logLevel: 'error', server: {middlewareMode: true, hmr: {server: http, port: http.address().port}}});
  t.after(async () => {
    await server.close();
    await new Promise(resolve => {http.closeAllConnections(); http.close(resolve);});
  });
  http.on('request', server.middlewares);
  const origin = `http://127.0.0.1:${http.address().port}`;
  const browser = await puppeteer.launch(testBrowserOptions(resolveChromePath(process.env.TDD_CHROME_PATH)));
  t.after(() => browser.close());
  const page = await browser.newPage();
  const submitted = [];
  await page.setRequestInterception(true);
  page.on('request', request => {
    if (!request.url().startsWith(origin + '/')) return void request.abort();
    const path = new URL(request.url()).pathname;
    if (!path.startsWith('/api/')) return void request.continue();
    const accounts = [{id: 1, display_name: '模拟账号', cookie_profile: {authenticated: true}}];
    const bodies = {
      ...recoveryResponses,
      '/api/filter-options': {accounts, groups: [{id: 11, account_id: 1, name: '模拟群', source_group_id: '1001'}], users: [], message_types: []},
      '/api/weibo-api/status': {accounts},
      '/api/collection-jobs': {items: [], total: 0},
      '/api/weibo-api/browser-sessions/active': {session: null},
      '/api/collection-jobs/weibo-api': {id: 99},
    };
    if (path === '/api/collection-jobs/weibo-api' || path === '/api/messages/1815/recover-context') submitted.push(JSON.parse(request.postData()));
    void request.respond({status: path in bodies ? 200 : 404, contentType: 'application/json', body: JSON.stringify(bodies[path] ?? {})});
  });
  await page.goto(origin);
  await page.waitForSelector('input[type="date"]');
  for (const [label, day] of [['开始时间', '16'], ['结束时间', '17']]) {
    const input = await page.$(`[aria-label="${label}日期"]`);
    await input.evaluate((el, day) => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, `2026-10-${day}`);
      el.dispatchEvent(new Event('input', {bubbles: true}));
    }, day);
    for (const [part, initial] of [['小时', '20'], ['分钟', '16']]) {
      await page.click(`[aria-label="${label}${part}选择"]`);
      const dimensions = await page.$eval(`[aria-label="${label}${part}"]`, el => ({
        rows: el.size, height: el.clientHeight, row: el.options[0].getBoundingClientRect().height,
        scrollHeight: el.scrollHeight, count: el.options.length,
      }));
      assert.equal(dimensions.rows, 12);
      assert.equal(dimensions.height, dimensions.row * 12);
      assert.ok(dimensions.scrollHeight > dimensions.height);
      assert.equal(dimensions.count, part === '小时' ? 24 : 60);
      await page.select(`[aria-label="${label}${part}"]`, initial);
      await page.keyboard.press('Enter');
    }
    await page.click(`[aria-label="${label}小时选择"]`);
    await page.keyboard.type('09');
    await page.keyboard.press('Enter');
    await page.click(`[aria-label="${label}分钟选择"]`);
    await page.select(`[aria-label="${label}分钟"]`, '59');
    const option = await page.$(`[aria-label="${label}分钟"] option[value="35"]`);
    await option.evaluate(el => el.scrollIntoView({block: 'nearest'}));
    await option.click();
    assert.equal(await page.$(`[aria-label="${label}分钟"]`), null);
    assert.match(await page.$eval(`[aria-label="${label}小时选择"]`, el => el.textContent), /09 时/);
    assert.match(await page.$eval(`[aria-label="${label}分钟选择"]`, el => el.textContent), /35 分/);
    assert.equal(await input.evaluate(el => el.value), `2026-10-${day}`);
  }
  await page.locator('xpath///button[normalize-space(.)="启动采集"]').click();
  await page.waitForFunction(() => document.body.innerText.includes('已进入全局队列'));
  assert.deepEqual(submitted, [{account_id: 1, group_id: 11, range_start: '2026-10-16 09:35:00', range_end: '2026-10-17 09:35:00'}]);
  await checkRecoveryTime(page, submitted);
});
