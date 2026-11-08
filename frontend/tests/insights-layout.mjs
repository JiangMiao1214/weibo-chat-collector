import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
export function insightsResponse(req,res,url,state){
 const json=value=>{res.setHeader('Content-Type','application/json');res.end(JSON.stringify(value));};
 if(url.pathname==='/api/insights/dashboard'){
  json({message_count:846,trend:[{date:'2026-08-23',count:240,members:18},{date:'2026-08-24',count:386,members:26},{date:'2026-08-25',count:220,members:21}],types:[{name:'text',count:610},{name:'image',count:236}],members:[{user_id:7,display_name:'测试用户',count:120}],archives:{stored:180,failed:3,skipped:2},pages:{text_stored:12,media_complete:10},storage:{files:165,bytes:30000000,missing_paths:0},coverage_note:'仅统计本地已入库消息；缺少日期表示未知。'});return true;
 }
 if(url.pathname==='/api/insights/logs'){
  json({items:[{id:1,occurred_at:'2026-10-12 02:00:00',account_id:1,group_id:11,category:'archive',task_id:1,message_id:1,action:'stop',result:'failed',error_code:'dns_fake_ip',source_url:'https://fixture.invalid/original?token=%5Bredacted%5D',failure_url:'https://fixture.invalid/cdn?signature=%5Bredacted%5D',is_deleted:0}],total:1,has_more:false,retention:'最近 90 天且最多 10000 条，升级前记录为当前状态基线。'});return true;
 }
 if(url.pathname==='/api/ocr/status'){
  json({enabled:!!state.ocrEnabled,paused:!!state.ocrPaused,counts:{done:18,queued:3,failed:1},eligible:22,undiscovered:0,errors:[],engine:'模拟本地引擎',available:true,setup:'本地中文识别，不上传图片'});return true;
 }
 if(url.pathname==='/api/ocr/control'){
  let input='';req.on('data',c=>input+=c);req.on('end',()=>{const body=JSON.parse(input);state.ocrRequest=body;state.ocrEnabled=true;state.ocrPaused=body.action==='pause';json({});});return true;
 }
 if(url.pathname==='/api/ocr/search'){
  state.ocrSearch=url.searchParams.get('q');json({items:[{archive_id:71,message_id:1,account_id:1,group_id:11,is_deleted:0,sent_at:'2026-08-23 10:01:00',text:'施工图纸 节点 A\n混凝土强度 C30',image_url:'/api/archives/71/content?account_id=1&group_id=11&deleted_only=false'}],total:1,has_more:false});return true;
 }
 return false;
}
export async function checkInsights(page,artifacts,state){
 const button=label=>`xpath///button[normalize-space(.)='${label}']`;
 await page.setViewport({width:1440,height:1000});
 await page.locator(button('统计仪表盘')).click();await page.waitForSelector('.insight-cards strong');
 assert.equal(await page.$('.chat-workspace'),null);
 assert.equal(await page.$eval('.insight-cards',el=>el.textContent.includes('28.61 MiB')),true);
 await page.screenshot({path:fileURLToPath(new URL('dashboard-desktop.png',artifacts))});
 await page.locator(button('失败：3')).click();await page.waitForSelector('.log-list article');
 assert.ok(await page.$('a[download]'));
 await page.screenshot({path:fileURLToPath(new URL('logs-desktop.png',artifacts))});
 await page.locator(button('图片文字搜索')).click();
 await page.select('.insight-filters label:nth-child(1) select','1');
 await page.select('.insight-filters label:nth-child(2) select','11');
 await page.locator(button('开启本群 OCR 并补识别历史')).click();
 await page.waitForFunction(()=>document.querySelector('.ocr-progress')?.textContent.includes('OCR 已启用'));
 assert.deepEqual(state.ocrRequest,{account_id:1,group_id:11,action:'start'});
 await page.locator('.insights-page form input').fill('节点');await page.locator(button('搜索图片文字')).click();
 await page.waitForSelector('.ocr-hits article');assert.equal(state.ocrSearch,'节点');
 assert.match(await page.$eval('.ocr-hits a',a=>a.getAttribute('href')),/^\/api\/archives\/71\/content/);
 await page.waitForFunction(()=>document.querySelector('.ocr-hits img')?.naturalWidth>0);
 await page.screenshot({path:fileURLToPath(new URL('ocr-desktop.png',artifacts))});
 await page.locator(button('暂停 OCR')).click();await page.waitForFunction(()=>document.querySelector('.ocr-progress')?.textContent.includes('OCR 已暂停'));
 await page.locator(button('定位消息 #1')).click();await page.waitForSelector('.chat-workspace .location-banner');
 assert.ok(await page.$('[aria-label="消息 #1"]'));
 await page.locator(button('返回筛选列表')).click();
 await page.locator('[aria-label="上下文 #1"]').click();
 await page.locator(button('查看更早上下文')).click();
 await page.waitForFunction(()=>document.querySelector('.reader-context')?.textContent.includes('真正更早的上下文'));
 assert.equal(await page.$$eval('.reader-context [data-context-id]',els=>els.length),3);
 await page.screenshot({path:fileURLToPath(new URL('continuous-context-desktop.png',artifacts))});
}
