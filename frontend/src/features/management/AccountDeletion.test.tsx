import React from 'react';
import {act,render,screen,waitFor,within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {expect,it,vi} from 'vitest';
import {App} from '../../App';
import {fakeApi} from '../../test/fakeApi';

function setup(blocked=false){
  const api=fakeApi();const fallback=globalThis.fetch;
  let deleted=false;
  const preview={account:{id:2,display_name:'账号2'},groups:[{id:21,name:'乙群'}],counts:{messages:0,recycled_messages:0},
    can_delete:!blocked,blockers:blocked?['采集任务与断点 1 条，需保留账号']:[],credentials_present:true,preview_token:'a'.repeat(64)};
  const requests:{path:string;init?:RequestInit}[]=[];
  const fetcher=vi.fn(async(path:string,init?:RequestInit)=>{
    requests.push({path,init});
    if(path==='/api/management/accounts/2/delete-preview') return new Response(JSON.stringify(preview));
    if(path==='/api/management/accounts/2'&&init?.method==='DELETE') {deleted=true;return new Response(JSON.stringify({deleted:true}));}
    if(path==='/api/management') return new Response(JSON.stringify({accounts:deleted?[]:[{id:2,display_name:'账号2',notes:'',
      is_active:1,group_count:1,message_count:0,cookie_profile:{authenticated:false}}],groups:[]}));
    const response=await fallback(path,init);
    if(deleted&&['/api/filter-options','/api/weibo-api/status'].includes(path)){
      const body=await response.json(); body.accounts=body.accounts.filter((a:{id:number})=>a.id!==2);
      if(body.groups)body.groups=body.groups.filter((g:{account_id:number})=>g.account_id!==2);
      return new Response(JSON.stringify(body));
    }
    return response;
  });
  vi.stubGlobal('fetch',fetcher);render(<App/>);
  return {user:userEvent.setup(),requests,fetcher,preview,api};
}

it('先预览再确认；取消不删除；成功刷新账号及失效的消息和采集选择',async()=>{
  const {user,requests}=setup();
  await screen.findByRole('option',{name:'账号2'});
  await user.selectOptions(screen.getByLabelText('账号'),'2');
  await user.click(screen.getByRole('button',{name:'消息'}));
  await user.selectOptions(screen.getByLabelText('账号'),'2');
  await user.selectOptions(screen.getByLabelText('群聊'),'21');
  await user.click(screen.getByRole('button',{name:'账号管理'}));
  await user.click(await screen.findByRole('button',{name:'删除账号'}));
  const dialog=await screen.findByRole('alertdialog',{name:'确认删除账号'});
  expect(dialog).toHaveTextContent('账号2');expect(dialog).toHaveTextContent('乙群');
  expect(dialog).toHaveTextContent('本机登录凭据');
  expect(requests.some(r=>r.init?.method==='DELETE')).toBe(false);
  await user.click(within(dialog).getByRole('button',{name:'取消删除'}));
  expect(screen.queryByRole('alertdialog')).toBeNull();
  expect(requests.some(r=>r.init?.method==='DELETE')).toBe(false);
  await user.click(screen.getByRole('button',{name:'删除账号'}));
  await user.click(await screen.findByRole('button',{name:'确认删除本地账号'}));
  await waitFor(()=>expect(screen.queryByRole('article',{name:'账号 账号2'})).toBeNull());
  const deletion=requests.find(r=>r.init?.method==='DELETE')!;
  expect(deletion.path).toBe('/api/management/accounts/2');
  expect(JSON.parse(String(deletion.init?.body))).toEqual({confirmed:true,preview_token:'a'.repeat(64)});
  await user.click(screen.getByRole('button',{name:'消息'}));
  await waitFor(()=>expect(screen.getByLabelText('账号')).toHaveValue(''));
  expect(screen.getByLabelText('群聊')).toHaveValue('');
  expect(screen.queryByRole('option',{name:'账号2'})).toBeNull();
  await user.click(screen.getByRole('button',{name:'采集任务'}));
  expect(screen.getByLabelText('账号')).not.toHaveValue('2');
  expect(screen.getByLabelText('群聊')).not.toHaveValue('21');
});

it('有历史时说明阻止原因，无法确认删除',async()=>{
  const {user,requests}=setup(true);
  await user.click(screen.getByRole('button',{name:'账号管理'}));
  await user.click(await screen.findByRole('button',{name:'删除账号'}));
  const dialog=await screen.findByRole('alertdialog',{name:'确认删除账号'});
  expect(await within(dialog).findByText(/采集任务与断点/)).toBeInTheDocument();
  expect(within(dialog).getByRole('button',{name:'确认删除本地账号'})).toBeDisabled();
  expect(requests.some(r=>r.init?.method==='DELETE')).toBe(false);
});

it('关闭预览后迟到响应不重开窗口，失败后必须重新预览',async()=>{
  const {user,fetcher,preview,requests}=setup();
  const original=fetcher.getMockImplementation()!;
  let finish!:(response:Response)=>void;
  fetcher.mockImplementation((path,init)=>path.endsWith('/delete-preview')
    ?new Promise(resolve=>{finish=resolve;}):original(path,init));
  await user.click(screen.getByRole('button',{name:'账号管理'}));
  await user.click(await screen.findByRole('button',{name:'删除账号'}));
  await user.click(screen.getByRole('button',{name:'取消删除'}));
  await act(async()=>finish(new Response(JSON.stringify(preview))));
  expect(screen.queryByRole('alertdialog')).toBeNull();
  fetcher.mockImplementation((path,init)=>init?.method==='DELETE'
    ?Promise.resolve(new Response(JSON.stringify({detail:'范围已变化，请重新预览'}),{status:409})):original(path,init));
  await user.click(screen.getByRole('button',{name:'删除账号'}));
  await user.click(await screen.findByRole('button',{name:'确认删除本地账号'}));
  expect(await screen.findByText(/范围已变化/)).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'确认删除本地账号'})).toBeDisabled();
  expect(screen.getByRole('button',{name:'重新预览'})).toBeEnabled();
  expect(fetcher.mock.calls.filter(([,init])=>init?.method==='DELETE')).toHaveLength(1);
});
