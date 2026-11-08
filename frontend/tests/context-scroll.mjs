import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';

export function contextScrollResponse(res, url, state) {
  if (!state.contextScroll) return false;
  if (url.pathname === '/api/messages/2/quote-context') {
    const items = Array.from({length:11}, (_, i) => ({id:80+i, account_id:1, group_id:11,
      is_deleted:0, sender_name:'引用层成员', sent_at:'2026-08-23 09:00:00',
      content_text:`引用层样本 ${i}：` + '引用层也必须独立滚动并保留顶部返回入口。'.repeat(8)}));
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify({source_message_id:2,account_id:1,group_id:11,deleted_only:false,
      status:'matched',match_method:'manual',target:items[5],items,has_older:false,has_newer:false}));
    return true;
  }
  if (!/^\/api\/message-view\/\d+\/context$/.test(url.pathname)) return false;
  const direction = url.searchParams.get('direction');
  state.contextRequests.push(direction);
  const start = direction === 'older' ? 35 : direction === 'newer' ? 51 : 40;
  const length = direction === 'center' ? 11 : 5;
  const items = Array.from({length}, (_, i) => {
    const second = start + i;
    return {id: second === 45 ? 2 : second, account_id: 1, group_id: 11, is_deleted: 0,
      sender_name: '滚动测试成员', sent_at: `2026-08-23 10:00:${second}`, reference_method:second === 45 ? 'manual' : null,
      content_text: `上下文滚动样本 ${second}：` + '用于验证长正文不会撑破侧栏，顶部和底部操作始终可见。'.repeat(8)};
  });
  res.setHeader('Content-Type', 'application/json');
  res.end(JSON.stringify({target_id: Number(url.pathname.split('/')[3]), account_id: 1,
    group_id: 11, deleted_only: false, items, has_older: direction !== 'older', has_newer: direction !== 'newer'}));
  return true;
}

async function geometry(page) {
  return page.$eval('.reader-context', panel => {
    const rows = panel.querySelector('.context-rows');
    const rect = el => {const r = el.getBoundingClientRect(); return {top:r.top,bottom:r.bottom};};
    return {panel:rect(panel), header:rect(panel.querySelector('.panel-title')),
      controls:rect(panel.querySelector('label')), rows:rect(rows),
      footer:rect(panel.querySelector('.context-pagination')),
      scrollable:rows.scrollHeight > rows.clientHeight, height:rows.clientHeight,
      scrollbarWidth:rows.offsetWidth - rows.clientWidth,
      panelScroll:panel.scrollTop, workspaceScroll:panel.closest('.workspace').scrollTop};
  });
}

function assertFrame(g) {
  assert.equal(g.scrollable, true, '长上下文必须形成独立滚动区域');
  assert.ok(g.height > 100, '消息区域必须保留可阅读的高度');
  assert.ok(g.scrollbarWidth > 0, '消息区应保留可见的滚动条位置');
  assert.ok(g.header.top >= g.panel.top && g.controls.bottom <= g.rows.top, '顶部标题和选项不得被裁切或遮挡消息');
  assert.ok(g.rows.bottom <= g.footer.top + 1, '底部按钮不得覆盖消息');
  assert.ok(g.footer.bottom <= g.panel.bottom + 1, '底部翻页按钮必须位于侧栏可见范围');
  assert.equal(g.panelScroll, 0, '只能滚动消息区，不能滚动侧栏');
  assert.equal(g.workspaceScroll, 0, '上下文不能带动外层工作区滚动');
}

async function scrollTo(page, edge) {
  await page.$eval('.reader-context .context-rows', (box, value) => {
    box.scrollTop = value === 'bottom' ? box.scrollHeight : value === 'top' ? 0 : 80;
  }, edge);
  // Allow native scroll events and any queued React layout work to settle.
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

export async function checkContextScroll(page, artifacts, state) {
  const button = name => `xpath///button[normalize-space(.)='${name}']`;
  await page.locator(button('关闭上下文')).click();
  state.contextScroll = true;
  state.contextRequests = [];
  await page.setViewport({width:1366, height:800});
  await page.locator('[aria-label="上下文 #2"]').click();
  await page.waitForSelector('.reader-context [data-context-id="50"]');
  const initial = await geometry(page);
  assertFrame(initial);
  // Development StrictMode may mount the initial reader twice; compare against
  // completed initialization so this assertion targets scroll-triggered requests.
  const initialRequests = [...state.contextRequests];
  assert.ok(initialRequests.length > 0 && initialRequests.every(x => x === 'center'));
  await page.locator('.reader-context input[type="checkbox"]').click();
  await scrollTo(page, 'bottom');
  assert.deepEqual(state.contextRequests, initialRequests, '关闭自动加载后滚动不得翻页');
  const after = await geometry(page);
  assertFrame(after);
  assert.deepEqual(after.header, initial.header, '滚动时标题固定');
  assert.deepEqual(after.controls, initial.controls, '滚动时自动加载选项固定');
  assert.deepEqual(after.footer, initial.footer, '滚动时翻页按钮固定');
  await page.locator('.reader-context input[type="checkbox"]').click();
  await scrollTo(page, 'middle');
  await scrollTo(page, 'bottom');
  await page.waitForSelector('.reader-context [data-context-id="55"]');
  assert.equal(state.contextRequests.filter(x => x === 'newer').length, 1, '底边只请求一次更新消息');
  await scrollTo(page, 'top');
  await page.waitForSelector('.reader-context [data-context-id="35"]');
  assert.equal(state.contextRequests.filter(x => x === 'older').length, 1, '顶边只请求一次更早消息');
  assert.ok(await page.$('.reader-context [data-context-id="2"]'), '双向加载保留原消息');
  assert.equal(await page.$$eval('.reader-context [data-context-id]', els => new Set(els.map(el => el.dataset.contextId)).size), 21);
  await scrollTo(page, 'middle');
  await scrollTo(page, 'top');
  await scrollTo(page, 'bottom');
  assert.equal(state.contextRequests.length, initialRequests.length + 2, '已到边界不得继续发请求');
  assertFrame(await geometry(page));
  await page.screenshot({path:fileURLToPath(new URL('context-scroll-desktop.png', artifacts))});
  await page.setViewport({width:1440,height:900});
  assertFrame(await geometry(page));
  await page.locator('.reader-context input[type="checkbox"]').click();
  await page.locator(button('回到原消息')).click();
  await page.waitForFunction(() => document.querySelectorAll('.reader-context [data-context-id]').length === 11);
  await page.locator(button('查看更早上下文')).click();
  await page.waitForSelector('.reader-context [data-context-id="35"]');
  assert.equal(state.contextRequests.at(-1), 'older', '关闭自动加载后仍可手动翻页');
  assertFrame(await geometry(page));
  await page.$eval('.reader-context [data-context-id="2"]', article => {
    const link = [...article.querySelectorAll('button')].find(el => el.textContent.includes('继续查看引用'));
    const box = article.closest('.context-rows');
    box.scrollTop += link.getBoundingClientRect().top - box.getBoundingClientRect().top - 80;
  });
  const savedScroll = await page.$eval('.reader-context .context-rows', el => el.scrollTop);
  await page.locator(button('继续查看引用 #2')).click();
  await page.waitForSelector('.reader-context [data-context-id="85"]');
  // Read the active frame only; previous frames remain mounted for restoration.
  const nested = await page.$eval('.context-frame:not([hidden]) .context-rows', el =>
    ({height:el.clientHeight, scrollHeight:el.scrollHeight, bottom:el.getBoundingClientRect().bottom,
      footer:el.nextElementSibling.getBoundingClientRect().top}));
  assert.ok(nested.height > 100 && nested.scrollHeight > nested.height, '进入引用层后仍有独立滚动区');
  assert.ok(nested.bottom <= nested.footer + 1, '引用层消息不覆盖底部操作');
  await page.locator(button('返回上一级引用（1）')).click();
  assert.equal(await page.$eval('.reader-context .context-rows', el => el.scrollTop), savedScroll, '返回上一层保留阅读位置');
  assertFrame(await geometry(page));
  state.contextScroll = false;
}
