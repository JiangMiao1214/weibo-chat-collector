import React from 'react';
import {fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {it,expect,vi} from 'vitest';
import {ContextPanel} from './ReaderNavigation';
const msg=(id:number)=>({id,account_id:1,group_id:11,is_deleted:0,sent_at:'2026-08-23 10:00:00',content_text:`正文${id}`,sender_name:'测试'});
it('向前拼接保留原消息，重叠页去重，同秒时间按 ID 稳定排序',async()=>{
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>new Response(JSON.stringify({target_id:url.includes('/8/')?8:10,account_id:1,group_id:11,deleted_only:false,
 items:url.includes('older')?[msg(8),msg(7)]:[msg(10),msg(8)],has_older:!url.includes('older'),has_newer:false}))));
 const user=userEvent.setup();render(<ContextPanel source={msg(10)} close={()=>{}} jump={()=>{}}/>);
 await screen.findByText('正文8');await user.click(screen.getByRole('button',{name:'查看更早上下文'}));
 await screen.findByText('正文7');
 expect(screen.getByText('正文10')).toBeInTheDocument();
 expect(within(screen.getByRole('complementary')).getAllByRole('button',{name:/跳到列表 #/}).map(e=>e.getAttribute('aria-label'))).toEqual(['跳到列表 #7','跳到列表 #8','跳到列表 #10']);
});

import {ContinuousReader} from './ContinuousReader';
import {mergeWindow} from './useContinuousContext';
import type {Message} from '../../shared/contracts';
it('窗口达到上限后保留反向游标，可以取回移出的消息',()=>{
 const messages=Array.from({length:100},(_,i)=>msg(i+20) as Message);
 const before={items:messages,has_older:false,has_newer:false};
 const older=mergeWindow(before,{items:[msg(18),msg(19)] as Message[],has_older:false,has_newer:true},'older');
 expect(older.items).toHaveLength(100);expect(older.items[0].id).toBe(18);expect(older.has_newer).toBe(true);
 const newer=mergeWindow(older,{items:[msg(118),msg(119)] as Message[],has_older:true,has_newer:false},'newer');
 expect(newer.items.map(m=>m.id)).toEqual(messages.map(m=>m.id));expect(newer.has_older).toBe(true);
});
it('引用 A 到 B 到 C 逐级返回时保留各级窗口及滚动位置',async()=>{
 const a={...msg(30),content_text:'「B」 - - - A'} as Message;
 const b={...msg(20),content_text:'「C」 - - - B'} as Message;
 const c=msg(10) as Message;
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>{
  const source=url.includes('/30/')?a:b;const target=source===a?b:c;
  return new Response(JSON.stringify({source_message_id:source.id,account_id:1,group_id:11,deleted_only:false,status:'matched',match_method:'manual',target,items:[target],has_older:false,has_newer:false}));
 }));
 const user=userEvent.setup();render(<ContinuousReader source={a} initial={{items:[a],has_older:false,has_newer:false}}/>);
 const original=screen.getByText(a.content_text).closest('.context-rows')!;original.scrollTop=43;
 await user.click(screen.getByRole('button',{name:'继续查看引用 #30'}));
 await user.click(await screen.findByRole('button',{name:'继续查看引用 #20'}));
 await screen.findByText('正文10');
 await user.click(screen.getByRole('button',{name:'返回上一级引用（2）'}));
 expect(screen.getByRole('button',{name:'继续查看引用 #20'})).toBeVisible();
 await user.click(screen.getByRole('button',{name:'返回上一级引用（1）'}));
 expect(screen.getByText(a.content_text)).toBeVisible();expect(original.scrollTop).toBe(43);
});

it.each(['older','newer'] as const)('滚动边缘加载 %s：在途不重复请求，到边界停止',async direction=>{
 let finish!:(response:Response)=>void;
 const request=vi.fn(()=>new Promise<Response>(resolve=>{finish=resolve;}));
 vi.stubGlobal('fetch',request);
 render(<ContinuousReader source={msg(10)} initial={{items:[msg(10)] as Message[],has_older:true,has_newer:true}}/>);
 const box=screen.getByText('正文10').closest('.context-rows')!;
 Object.defineProperties(box,{scrollHeight:{value:1000},clientHeight:{value:300}});
 box.scrollTop=direction==='older'?0:700;
 fireEvent.scroll(box);fireEvent.scroll(box);
 expect(request).toHaveBeenCalledTimes(1);
 expect(request.mock.calls[0]).toEqual(expect.arrayContaining([expect.stringContaining(`direction=${direction}`)]));
 finish(new Response(JSON.stringify({target_id:10,account_id:1,group_id:11,deleted_only:false,
  items:[msg(direction==='older'?9:11)],has_older:false,has_newer:false})));
 await screen.findByText(direction==='older'?'正文9':'正文11');
 await waitFor(()=>expect(screen.queryByRole('status')).not.toBeInTheDocument());
 box.scrollTop=direction==='older'?0:700;fireEvent.scroll(box);
 expect(request).toHaveBeenCalledTimes(1);
 expect(screen.getByText('正文10')).toBeInTheDocument();
});

it('关闭自动加载或内容未溢出时不自动请求，仍可按钮翻页',async()=>{
 const request=vi.fn(async()=>new Response(JSON.stringify({target_id:10,account_id:1,group_id:11,deleted_only:false,
  items:[msg(9)],has_older:false,has_newer:false})));
 vi.stubGlobal('fetch',request);
 const user=userEvent.setup();
 render(<ContinuousReader source={msg(10)} initial={{items:[msg(10)] as Message[],has_older:true,has_newer:true}}/>);
 const box=screen.getByText('正文10').closest('.context-rows')!;
 Object.defineProperties(box,{scrollHeight:{value:300,configurable:true},clientHeight:{value:300}});
 fireEvent.scroll(box);expect(request).not.toHaveBeenCalled();
 Object.defineProperty(box,'scrollHeight',{value:1000});
 await user.click(screen.getByRole('checkbox'));
 fireEvent.scroll(box);expect(request).not.toHaveBeenCalled();
 await user.click(screen.getByRole('button',{name:'查看更早上下文'}));
 await screen.findByText('正文9');expect(request).toHaveBeenCalledTimes(1);
});
