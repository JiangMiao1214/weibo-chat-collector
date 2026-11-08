import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';

export function managementResponse(req,res,url,state,accounts,groups,messages){
 const json=value=>{res.setHeader('Content-Type','application/json');res.end(JSON.stringify(value));};
 if(url.pathname==='/api/management'){
  json({accounts:accounts.map(a=>({...a,display_name:state.managementName&&a.id===1?state.managementName:a.display_name,
   notes:'隔离管理测试',is_active:1,group_count:groups.filter(g=>g.account_id===a.id).length,message_count:a.id===3?0:2,cookie_profile:{exists:true,authenticated:true,updated_at:null}})),
   groups:groups.map(g=>({...g,account_name:accounts.find(a=>a.id===g.account_id).display_name,is_active:1,
    description:'隔离模拟群',message_count:2,attachment_count:3,first_message_at:'2026-08-23 10:00:00',last_message_at:'2026-08-23 10:01:00',
    archive:null,archive_counts:{},latest_collection:null}))});return true;
 }
 if(url.pathname==='/api/management/accounts/3/delete-preview'){
  json({account:{id:3,display_name:'待删除模拟账号'},groups:[{id:31,name:'模拟空群'}],
   counts:{messages:0,recycled_messages:0},can_delete:true,blockers:[],credentials_present:true,preview_token:'a'.repeat(64)});return true;
 }
 if(url.pathname==='/api/management/accounts/3'&&req.method==='DELETE'){
  let input='';req.on('data',chunk=>input+=chunk);req.on('end',()=>{
   state.accountDeletion=JSON.parse(input);accounts.splice(accounts.findIndex(a=>a.id===3),1);
   groups.splice(groups.findIndex(g=>g.id===31),1);json({deleted:true});});return true;
 }
 if(url.pathname==='/api/management/accounts/1'&&req.method==='PATCH'){
  let input='';req.on('data',chunk=>input+=chunk);req.on('end',()=>{state.managementName=JSON.parse(input).display_name;json({id:1});});return true;
 }
 if(url.pathname==='/api/messages/1/reference-candidates'){
  json({items:[messages[1]],has_more:false,offset:0});return true;
 }
 if(url.pathname==='/api/messages/1/reference'&&req.method==='PUT'){
  let input='';req.on('data',chunk=>input+=chunk);req.on('end',()=>{state.referenceChoice=JSON.parse(input);json({});});return true;
 }
 return false;
}

export async function checkManagement(page,artifacts,state){
 const button=label=>`xpath///button[normalize-space(.)='${label}']`;
 await page.setViewport({width:1440,height:1000});
 await page.locator(button('账号管理')).click();
 await page.waitForSelector('[aria-label="账号 账号甲"]');
 await page.locator('[aria-label="编辑账号甲"]').click();
 await page.locator('.management-editor input:not([type=checkbox])').fill('修改后的测试账号');
 await page.locator(button('保存账号')).click();
 await page.waitForSelector('[aria-label="账号 修改后的测试账号"]');
 assert.equal(state.managementName,'修改后的测试账号');
 await page.screenshot({path:fileURLToPath(new URL('management-accounts-desktop.png',artifacts))});
 await page.locator('[aria-label="账号 待删除模拟账号"] button::-p-text(删除账号)').click();
 await page.waitForFunction(()=>document.querySelector('[aria-label="确认删除账号"]')?.textContent.includes('模拟空群'));
 assert.equal(state.accountDeletion,undefined);
 await page.locator(button('取消删除')).click();
 assert.equal(state.accountDeletion,undefined);
 await page.locator('[aria-label="账号 待删除模拟账号"] button::-p-text(删除账号)').click();
 await page.waitForFunction(()=>document.querySelector('[aria-label="确认删除账号"]')?.textContent.includes('模拟空群'));
 const deletionPanel=await page.$('[aria-label="确认删除账号"]');
 assert.equal(await deletionPanel.evaluate(el=>el.scrollWidth<=el.clientWidth),true);
 await deletionPanel.screenshot({path:fileURLToPath(new URL('account-delete-preview-desktop.png',artifacts))});
 await page.locator(button('确认删除本地账号')).click();
 await page.waitForFunction(()=>!document.querySelector('[aria-label="账号 待删除模拟账号"]'));
 assert.deepEqual(state.accountDeletion,{confirmed:true,preview_token:'a'.repeat(64)});
 await page.locator(button('群聊管理')).click();
 await page.waitForSelector('[aria-label="群聊 甲群"]');
 assert.equal(await page.$('.chat-workspace'),null,'管理页不混入消息工作区');
 assert.equal(await page.$eval('.management-page',el=>el.scrollWidth<=el.clientWidth),true);
 await page.locator(button('新增群聊')).click();
 assert.equal(await page.$eval(button('保存群聊'),el=>el.disabled),true,'有群账号不能新增第二个群');
 await page.locator(button('取消编辑')).click();
 await page.screenshot({path:fileURLToPath(new URL('management-groups-desktop.png',artifacts))});
 await page.locator('[aria-label="群聊 甲群"] button:nth-child(2)').click();
 await page.waitForSelector('.chat-workspace');
 await page.locator('[aria-label="查看消息 #1"]').click();
 await page.locator(button('管理引用关系')).click();
 await page.locator(button('选择并永久关联原消息')).click();
 await page.locator('[aria-label="选择原消息 #2"]').click();
 assert.equal(state.referenceChoice,undefined);
 await page.locator(button('确认保存引用关系')).click();
 await page.waitForFunction(()=>document.querySelector('.quote-context-dialog')?.textContent.includes('人工确认的永久关联'));
 assert.deepEqual(state.referenceChoice,{account_id:1,group_id:11,deleted_only:false,target_message_id:2,confirmed:true});
 await page.screenshot({path:fileURLToPath(new URL('permanent-reference-desktop.png',artifacts))});
 await page.keyboard.press('Escape');
}
