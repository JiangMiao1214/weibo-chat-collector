import React from 'react';
import {render,screen,waitFor,fireEvent} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {it,expect,vi} from 'vitest';
import {InsightsPage} from './InsightsPage';
import type {FilterOptions} from '../../shared/contracts';
const options:FilterOptions={accounts:[{id:1,display_name:'甲',auth_type:'cookie',login_profile_name:null,is_active:1}],groups:[{id:11,account_id:1,name:'甲群',account_name:'甲',source_group_id:'fake',is_active:1}],users:[],message_types:[]};
it('统计展示唯一磁盘占用并将日期和删除状态带入消息',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>new Response(JSON.stringify({message_count:3,trend:[{date:'2026-08-23',count:3,members:2}],types:[],members:[],archives:{failed:1},pages:{text_stored:1,media_complete:0},storage:{bytes:1048576,files:1,missing_paths:0},coverage_note:'仅本地数据'}))));
 const open=vi.fn();const user=userEvent.setup();render(<InsightsPage mode="dashboard" options={options} openMessages={open}/>);
 expect(await screen.findByText('1.00 MiB')).toBeVisible();
 await user.click(screen.getByLabelText('仅回收站'));
 await user.click(await screen.findByRole('button',{name:/2026-08-23/}));
 expect(open).toHaveBeenCalledWith(expect.objectContaining({dateFrom:'2026-08-23',dateTo:'2026-08-23'}),undefined,true);
});
it('日志保留原始和失败地址、消息范围及脱敏导出入口',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>new Response(JSON.stringify({items:[{id:1,occurred_at:'2026-10-12 00:00:00',category:'archive',task_id:7,message_id:42,account_id:1,group_id:11,action:'stop',result:'failed',error_code:'dns_fake_ip',source_url:'https://fixture.invalid/original',failure_url:'https://fixture.invalid/failure',is_deleted:1}],total:1,has_more:false,retention:'最近 90 天'}))));
 const open=vi.fn();const user=userEvent.setup();render(<InsightsPage mode="logs" options={options} openMessages={open}/>);
 expect(await screen.findByText('https://fixture.invalid/failure')).toBeVisible();
 expect(screen.getByRole('link',{name:'导出脱敏诊断记录'})).toHaveAttribute('href',expect.stringContaining('/api/insights/logs/export'));
 await user.click(screen.getByRole('button',{name:'定位消息 #42'}));
 expect(open).toHaveBeenCalledWith(expect.any(Object),{id:42,account_id:1,group_id:11,is_deleted:1},true);
});
it('OCR 显式开启本群、展示状态，并从独立文字结果定位图片消息',async()=>{
 const calls=vi.fn(async(url:string,init?:RequestInit)=>new Response(JSON.stringify(url.includes('/search?')?{items:[{archive_id:7,message_id:42,account_id:1,group_id:11,is_deleted:0,sent_at:'2026-08-23',text:'施工图纸节点A',image_url:'/api/archives/7/content?account_id=1&group_id=11'}],total:1,has_more:false}: {enabled:false,paused:false,counts:{},eligible:2,undiscovered:2,errors:[],available:true,engine:'fake',setup:'本地测试'})));
 vi.stubGlobal('fetch',calls);const open=vi.fn();const user=userEvent.setup();render(<InsightsPage mode="ocr" options={options} openMessages={open}/>);
 await user.selectOptions(screen.getByLabelText('账号'),'1');await user.selectOptions(screen.getByLabelText('群聊'),'11');
 await user.click(await screen.findByRole('button',{name:'开启本群 OCR 并补识别历史'}));
 await waitFor(()=>expect(calls.mock.calls.some(([url,init])=>url==='/api/ocr/control'&&JSON.parse(String(init?.body)).action==='start')).toBe(true));
 await user.type(screen.getByLabelText('图片文字关键词'),'节点');await user.click(screen.getByRole('button',{name:'搜索图片文字'}));
 expect(await screen.findByText('施工图纸节点A')).toBeVisible();
 expect(screen.getByRole('link',{name:'匹配图片 #7 打开本地原图'})).toHaveAttribute('href','/api/archives/7/content?account_id=1&group_id=11');
 await user.click(screen.getByRole('button',{name:'定位消息 #42'}));expect(open).toHaveBeenCalledWith(expect.any(Object),expect.objectContaining({id:42,account_id:1,group_id:11}),false);
});
