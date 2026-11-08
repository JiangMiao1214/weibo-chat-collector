import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';

function message(index) {
  const day = 21 + Math.floor(index / 100);
  return {id:1000+index,account_id:1,account_name:'测试账号',group_id:11,group_name:'测试群',
    user_id:7,sender_name:'分页测试成员',is_deleted:0,message_type:'text',attachment_count:0,
    sent_at:`2026-08-${day} 08:${String(Math.floor(index % 100 / 60)).padStart(2,'0')}:${String(index % 100 % 60).padStart(2,'0')}`,
    content_text:`历史分页样本 ${index}：` + '前插消息后保留当前阅读位置。'.repeat(index % 4 + 1)};
}

export function prependResponse(res, url, state) {
  if (!state.mainPrepend) return false;
  const detail = /^\/api\/messages\/(1[012]\d\d)$/.exec(url.pathname);
  let body;
  if (detail) body = {...message(Number(detail[1])-1000),attachments:[],archives:[]};
  else if (url.pathname === '/api/messages') {
    state.prependQueries.push(Object.fromEntries(url.searchParams));
    const cursor = Number(url.searchParams.get('before_id'));
    const start = cursor ? cursor - 1100 : 200;
    const items = Array.from({length:100}, (_, i) => message(start+99-i));
    body = {items,total:300,has_more:start > 0,
      next_cursor:start > 0 ? {before_id:1000+start,before_sent_at:items.at(-1).sent_at} : null,
      attachment_summary:{message_count:0,total_count:0,by_type:{}}};
    if (cursor) {
      state.releaseOlder = () => {res.setHeader('Content-Type','application/json');res.end(JSON.stringify(body));};
      state.onOlderRequest();
      return true;
    }
  } else return false;
  res.setHeader('Content-Type','application/json');res.end(JSON.stringify(body));
  return true;
}

async function anchor(page) {
  return page.$eval('.message-list', box => {
    const top = box.getBoundingClientRect().top;
    const card = [...box.querySelectorAll('.chat-message')].find(el => el.getBoundingClientRect().bottom > top);
    return {id:card.getAttribute('aria-label'),offset:card.getBoundingClientRect().top-top};
  });
}

async function assertAnchor(page, saved) {
  const actual = await page.$eval('.message-list', (box, id) => {
    const card = [...box.querySelectorAll('.chat-message')].find(el => el.getAttribute('aria-label') === id);
    return card.getBoundingClientRect().top-box.getBoundingClientRect().top;
  }, saved.id);
  assert.ok(Math.abs(actual-saved.offset) <= 2,
    `加载更早消息应保留 ${saved.id} 的视口位置：之前 ${saved.offset}px，之后 ${actual}px`);
  assert.equal(await page.$eval('.workspace', el => el.scrollTop), 0, '不能带动工作区滚动');
}

export async function checkMessagePrepend(page, artifacts, state) {
  await page.locator('xpath///button[normalize-space(.)="关闭上下文"]').click();
  state.mainPrepend = true;state.prependQueries = [];
  // Start normal reading, clearing the earlier OCR fixture's external location.
  await page.locator('xpath///button[normalize-space(.)="消息"]').click();
  await page.waitForFunction(() => document.querySelectorAll('.chat-message').length === 100);
  await page.$eval('.message-list', el => {el.scrollTop=0;});
  const saved = await anchor(page);
  await page.screenshot({path:fileURLToPath(new URL('message-prepend-before.png', artifacts))});
  let requested = new Promise(resolve => {state.onOlderRequest=resolve;});
  await page.locator('xpath///button[normalize-space(.)="加载更早消息"]').click();
  await requested;
  assert.equal(await page.$eval('.load-more-row button', el => el.disabled), true);
  state.releaseOlder();
  await page.waitForFunction(() => document.querySelectorAll('.chat-message').length === 200);
  await assertAnchor(page, saved);
  assert.equal(await page.$eval('.chat-message', el => el.getAttribute('aria-label')), '消息 #1100');
  await page.screenshot({path:fileURLToPath(new URL('message-prepend-position.png', artifacts))});
  // Scroll manually to the new top, then keep reading while the next request waits.
  await page.$eval('.message-list', el => {el.scrollTop=0;});
  requested = new Promise(resolve => {state.onOlderRequest=resolve;});
  await page.locator('xpath///button[normalize-space(.)="加载更早消息"]').click();
  await requested;
  await page.$eval('.message-list', el => {el.scrollTop=180;});
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const moved = await anchor(page);
  state.releaseOlder();
  await page.waitForFunction(() => document.querySelectorAll('.chat-message').length === 300);
  await assertAnchor(page, moved);
  assert.equal(await page.$eval('.load-more-row button', el => el.disabled && el.textContent.includes('没有更多消息')), true);
  assert.deepEqual(state.prependQueries.map(q => q.limit), ['100','100','100']);
  assert.deepEqual(state.prependQueries.slice(1).map(q => q.before_id), ['1200','1100']);
  state.mainPrepend = false;
}
