import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';

const messages = ['模拟新成员 加入了群', '这是一条正常讨论', '早安'].map((content_text,i)=>({
  id:1500+i,account_id:1,account_name:'模拟账号',group_id:11,group_name:'模拟群',
  user_id:7,sender_name:'模拟通知发送者',is_deleted:0,sent_at:`2026-08-23 14:00:0${i}`,
  message_type:'text',content_text,attachment_count:0
}));

export function joinNoticeResponse(res,url,state) {
  if(!state.joinNotices)return false;
  let body;
  if(url.pathname==='/api/messages') {
    const selection=JSON.parse(url.searchParams.get('viewer')||'{}');
    state.joinRequests.push({selection,user:url.searchParams.get('user')});
    const items=selection.hide_join_notices?messages.slice(1):messages;
    body={items,total:items.length,has_more:false,next_cursor:null,
      attachment_summary:{message_count:0,total_count:0,by_type:{}}};
  } else if(/^\/api\/messages\/150[012]$/.test(url.pathname)) {
    body={...messages[Number(url.pathname.split('/').at(-1))-1500],attachments:[],archives:[]};
  } else return false;
  res.setHeader('Content-Type','application/json');res.end(JSON.stringify(body));
  return true;
}

export async function checkJoinNotices(page,artifacts,state) {
  state.joinNotices=true;state.joinRequests=[];
  const button=name=>`xpath///button[normalize-space(.)='${name}']`;
  await page.locator(button('消息')).click();
  await page.waitForSelector('[aria-label="消息 #1500"]');
  await page.locator(button('隐藏入群通知')).click();
  await page.waitForFunction(()=>document.querySelectorAll('.chat-message').length===2);
  assert.equal(await page.$('[aria-label="消息 #1500"]'),null);
  assert.ok(await page.$('[aria-label="消息 #1501"]'));
  assert.ok(await page.$('[aria-label="消息 #1502"]'));
  assert.deepEqual(await page.$$eval('.chat-message-header strong',els=>els.map(el=>el.textContent)),
    ['模拟通知发送者','模拟通知发送者']);
  assert.equal(state.joinRequests.at(-1).selection.hide_join_notices,true);
  assert.equal(state.joinRequests.at(-1).selection.hide_noise,false);
  assert.deepEqual(state.joinRequests.at(-1).selection.members,[]);
  assert.equal(state.joinRequests.at(-1).user,null);
  await page.screenshot({path:fileURLToPath(new URL('hide-join-notices.png',artifacts))});
  await page.locator(button('隐藏入群通知')).click();
  await page.waitForFunction(()=>document.querySelectorAll('.chat-message').length===3);
  assert.ok(await page.$('[aria-label="消息 #1500"]'));
  assert.equal(state.joinRequests.at(-1).selection.hide_join_notices,false);
  state.joinNotices=false;
}
