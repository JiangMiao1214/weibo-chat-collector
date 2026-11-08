import React from 'react';
import {render,screen,within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {expect,it,vi} from 'vitest';
import {GroupEditor} from './TargetEditors';
import {AccountCard} from './TargetCards';
import {App} from '../../App';
import {fakeApi} from '../../test/fakeApi';
import type {ManagedAccount,ManagedGroup} from './model';

const account=(id:number,count:number):ManagedAccount=>({id,display_name:`账号${id}`,notes:null,is_active:1,
  group_count:count,message_count:0,cookie_profile:{account_id:id,exists:false,authenticated:false,cookie_count:0,updated_at:null}});
const group={id:11,account_id:1,name:'唯一群',latest_collection:null,archive:null} as ManagedGroup;

it('新增群仅允许无群账号，已有群包括停用群不能再次保存',async()=>{
  const save=vi.fn();const user=userEvent.setup();
  const view=render(<GroupEditor item={null} accounts={[account(1,1),account(2,0)]} busy={false} save={save} cancel={vi.fn()}/>);
  expect(screen.getByLabelText('所属账号')).toHaveValue('2');
  expect(screen.getByRole('option',{name:/账号1/})).toBeDisabled();
  await user.type(screen.getByLabelText('群聊名称'),'首群');
  await user.click(screen.getByRole('button',{name:'保存群聊'}));
  expect(save).toHaveBeenCalledWith(expect.objectContaining({account_id:2,name:'首群'}));
  view.rerender(<GroupEditor item={null} accounts={[account(1,1),account(2,1)]} busy={false} save={save} cancel={vi.fn()}/>);
  expect(screen.getByRole('button',{name:'保存群聊'})).toBeDisabled();
  expect(screen.getByText(/一个账号只能对应一个群聊/)).toBeInTheDocument();
});

it('唯一群随账号卡片进入采集；旧多群显示待整理并保留全部群名',async()=>{
  const navigate=vi.fn();const user=userEvent.setup();
  const props={item:account(1,1),groups:[group],navigate,edit:vi.fn(),clear:vi.fn(),onDeleted:vi.fn()};
  const view=render(<AccountCard {...props}/>);
  await user.click(screen.getByRole('button',{name:'登录与采集'}));
  expect(navigate).toHaveBeenCalledWith('jobs',1,11);
  view.rerender(<AccountCard {...props} item={account(1,2)} groups={[group,{...group,id:12,name:'旧第二群'}]}/>);
  expect(screen.getByText(/待整理/)).toBeInTheDocument();
  expect(screen.getByText(/关联群：/)).toHaveTextContent('唯一群、旧第二群');
  view.rerender(<AccountCard {...props} item={account(1,0)} groups={[]}/>);
  expect(within(screen.getByRole('article')).getByText(/待配置群聊/)).toBeInTheDocument();
});

it('已有多群只可编辑资料，不能在管理页改绑另一个远端群',()=>{
  render(<GroupEditor item={{...group,source_group_id:'123',is_active:1}} accounts={[account(1,2)]}
    busy={false} save={vi.fn()} cancel={vi.fn()}/>);
  expect(screen.getByLabelText('微博群 ID')).toBeDisabled();
  expect(screen.getByRole('button',{name:'保存群聊'})).toBeEnabled();
});

it('备用配置禁止已有群账号选择新增，旧多群的扫码候选也不能新增绑定',async()=>{
  const api=fakeApi();const original=api.fetchMock.getMockImplementation()!;
  api.fetchMock.mockImplementation(async(input,init)=>{
    const path=String(input);
    if(path==='/api/import-files'||path.startsWith('/api/browser-captures'))return new Response(JSON.stringify({items:[],total:0}));
    if(path.includes('/browser-sessions'))return new Response(JSON.stringify({session:{session_id:'fake',account_id:1,group_id:12,
      status:'group_candidate_found',cookie_ready:true,candidate_source_group_id:'999'}}));
    return original(input,init);
  });
  const user=userEvent.setup();render(<App/>);
  expect(await screen.findByRole('button',{name:'确认绑定'})).toBeDisabled();
  await user.click(screen.getByRole('button',{name:'高级工具 / 数据导入'}));
  expect(await screen.findByRole('option',{name:'新增群聊'})).toBeDisabled();
  expect(screen.getByLabelText('微博群 ID')).toBeDisabled();
});
