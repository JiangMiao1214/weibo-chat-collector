import React from 'react';
import {act, fireEvent, render, renderHook, screen, waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {expect, it, vi} from 'vitest';
import {App} from '../../App';
import {fakeApi} from '../../test/fakeApi';
import {buildFilterPayload, buildMessageQuery, emptyFilters} from './model';
import {useReadRequest} from './useReadRequest';

it('未应用日期时隐藏整个小时区，输入高级日期草稿不会提前显示', async()=>{
  const user=userEvent.setup(); fakeApi(); render(<App/>);
  await screen.findByRole('option',{name:'账号1'});
  await user.click(screen.getByRole('button',{name:'消息'}));
  expect(screen.queryByText('小时分布')).toBeNull();
  expect(screen.queryByRole('button',{name:'清除小时'})).toBeNull();
  expect(document.querySelector('.hours-strip')).toBeNull();
  fireEvent.change(screen.getByLabelText('开始日期'),{target:{value:'2026-10-20'}});
  expect(screen.queryByText('小时分布')).toBeNull();
  await user.click(screen.getByRole('button',{name:'搜索'}));
  expect(await screen.findByText('小时分布')).toBeInTheDocument();
});

it.each(['全部日期','清空日期输入','高级清空'])('%s同步清除小时，列表、导出及删除预览不残留条件', async mode=>{
  const user=userEvent.setup(); const api=fakeApi(); render(<App/>);
  await screen.findByRole('option',{name:'账号1'});
  await user.click(screen.getByRole('button',{name:'消息'}));
  fireEvent.change(screen.getByLabelText('阅读日期'),{target:{value:'2026-10-20'}});
  await user.click(await screen.findByRole('button',{name:/^12 时，/}));
  await waitFor(()=>{
    const call=api.calls.filter(c=>c.path.startsWith('/api/messages?')).at(-1)!;
    expect(JSON.parse(new URL(call.path,'http://test').searchParams.get('viewer')! ).hours).toEqual([12]);
  });
  if(mode==='全部日期') await user.click(screen.getByRole('button',{name:'全部日期'}));
  else if(mode==='清空日期输入') fireEvent.change(screen.getByLabelText('阅读日期'),{target:{value:''}});
  else {
    fireEvent.change(screen.getByLabelText('开始日期'),{target:{value:''}});
    fireEvent.change(screen.getByLabelText('结束日期'),{target:{value:''}});
    await user.click(screen.getByRole('button',{name:'搜索'}));
  }
  await waitFor(()=>expect(screen.queryByText('小时分布')).toBeNull());
  const query=new URL(api.calls.filter(c=>c.path.startsWith('/api/messages?')).at(-1)!.path,'http://test');
  expect(JSON.parse(query.searchParams.get('viewer')!).hours).toEqual([]);
  Object.defineProperty(URL,'createObjectURL',{configurable:true,value:vi.fn(()=> 'blob:fixture')});
  Object.defineProperty(URL,'revokeObjectURL',{configurable:true,value:vi.fn()});
  vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(()=>{});
  await user.click(screen.getByRole('button',{name:'导出当前筛选结果'}));
  await user.click(screen.getByRole('button',{name:'预览删除数量'}));
  for(const path of ['/api/message-view/export','/api/messages/delete-preview']) {
    await waitFor(()=>expect(api.calls.some(c=>c.path===path)).toBe(true));
    const body=JSON.parse(api.calls.filter(c=>c.path===path).at(-1)!.body!);
    expect(JSON.parse(body.filters.viewer).hours).toEqual([]);
    expect(body.filters.date_from).toBeNull(); expect(body.filters.date_to).toBeNull();
  }
});

it('所有查询序列化统一移除无日期的小时，保留成员媒体等条件',()=>{
  const filters={...emptyFilters,viewer:JSON.stringify({hours:[12],members:[7],media:'links'})};
  const query=new URLSearchParams(buildMessageQuery(filters));
  expect(JSON.parse(query.get('viewer')!)).toMatchObject({hours:[],members:[7],media:'links'});
  expect(JSON.parse(buildFilterPayload(filters).viewer!)).toMatchObject({hours:[],members:[7],media:'links'});
});

it('切换日期立即撤下旧统计，迟到响应不能覆盖新日期',async()=>{
  let finish!:(response:Response)=>void;
  vi.stubGlobal('fetch',vi.fn((_url,init)=>init.body==='old'
    ? new Promise(resolve=>{finish=resolve;}) : Promise.resolve(new Response(JSON.stringify({hours:[2]})))));
  const {result,rerender}=renderHook(({date})=>useReadRequest<{hours:number[]}>('/api/message-view/facets',date),{initialProps:{date:'old'}});
  await waitFor(()=>expect(finish).toBeTypeOf('function'));
  rerender({date:'new'});
  expect(result.current.data).toBeNull();
  await waitFor(()=>expect(result.current.data?.hours).toEqual([2]));
  await act(async()=>finish(new Response(JSON.stringify({hours:[1]}))));
  expect(result.current.data?.hours).toEqual([2]);
});
