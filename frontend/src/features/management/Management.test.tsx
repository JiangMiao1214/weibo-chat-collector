import React from 'react';
import {render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {describe,it,expect,vi} from 'vitest';
import {App} from '../../App';
import {fakeApi} from '../../test/fakeApi';

function setup() {
  fakeApi();
  const fallback = globalThis.fetch;
  const data = {accounts: [{id:1,display_name:'账号1',notes:'旧备注',is_active:1,group_count:1,message_count:8,
    cookie_profile:{exists:false,authenticated:false,updated_at:null}}], groups:[{id:11,account_id:1,account_name:'账号1',name:'甲群',
    description:'群备注',source_group_id:'1001',is_active:1,message_count:8,attachment_count:3,first_message_at:'2026-08-23',
    last_message_at:'2026-08-25',archive:null,archive_counts:{},latest_collection:null}]};
  const calls: {path:string;init?:RequestInit}[] = [];
  vi.stubGlobal('fetch',vi.fn(async(path:string,init?:RequestInit)=>{
    if (!String(path).startsWith('/api/management')) return fallback(path,init);
    calls.push({path,init});
    return new Response(JSON.stringify(init?.method ? {id:1}:data),{status:200,headers:{'Content-Type':'application/json'}});
  }));
  render(<App/>);
  return {user:userEvent.setup(),calls};
}

describe('独立账号和群聊管理',()=>{
  it('从另一个账号的卡片进入采集，刷新后仍选中该账号的唯一群',async()=>{
    const {user}=setup();
    await screen.findByRole('option',{name:'账号2'});
    await user.selectOptions(screen.getByLabelText('账号'),'2');
    await user.click(screen.getByRole('button',{name:'账号管理'}));
    await user.click(await screen.findByRole('button',{name:'登录与采集'}));
    await waitFor(()=>expect(screen.getByLabelText('账号')).toHaveValue('1'));
    expect(screen.getByLabelText('群聊')).toHaveValue('11');
  });
  it('账号管理可编辑资料并提交启停，不删除历史',async()=>{
    const {user,calls}=setup();
    await user.click(screen.getByRole('button',{name:'账号管理'}));
    await user.click(await screen.findByRole('button',{name:'编辑账号1'}));
    const name=screen.getByLabelText('账号名称');
    await user.clear(name);await user.type(name,'新名称');
    await user.click(screen.getByLabelText('启用账号'));
    await user.click(screen.getByRole('button',{name:'保存账号'}));
    await waitFor(()=>expect(calls.some(c=>c.init?.method==='PATCH')).toBe(true));
    const sent=calls.find(c=>c.init?.method==='PATCH')!;
    expect(sent.path).toBe('/api/management/accounts/1');
    expect(JSON.parse(sent.init!.body as string)).toMatchObject({display_name:'新名称',is_active:false});
    expect(screen.getByText(/停用保留历史/)).toBeInTheDocument();
  });
  it('群聊管理显示已知日期和归档，并带范围跳转消息',async()=>{
    const {user}=setup();
    await user.click(screen.getByRole('button',{name:'群聊管理'}));
    const card=await screen.findByRole('article',{name:'群聊 甲群'});
    expect(screen.queryByLabelText('正文查找（保留上下文）')).toBeNull();
    expect(within(card).getByText(/2026-08-23/)).toBeInTheDocument();
    expect(within(card).getByRole('button',{name:'本地归档'})).toBeInTheDocument();
    await user.click(within(card).getByRole('button',{name:'查看消息'}));
    await waitFor(()=>expect(screen.getByLabelText('群聊')).toHaveValue('11'));
    expect(screen.getByLabelText('账号')).toHaveValue('1');
  });
  it('清除凭据需要明确确认且默认不发送删除请求',async()=>{
    const {user,calls}=setup();
    await user.click(screen.getByRole('button',{name:'账号管理'}));
    await user.click(await screen.findByRole('button',{name:'清除登录凭据'}));
    expect(calls.some(c=>c.init?.method==='DELETE')).toBe(false);
    await user.click(screen.getByRole('button',{name:'确认清除凭据'}));
    await waitFor(()=>expect(calls.some(c=>c.path==='/api/management/accounts/1/credentials?confirmed=true' && c.init?.method==='DELETE')).toBe(true));
  });
});
