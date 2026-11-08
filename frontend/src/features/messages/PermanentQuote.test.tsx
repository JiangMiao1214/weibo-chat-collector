import React from 'react';
import {render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {it,expect,vi} from 'vitest';
import {QuoteLookup} from './QuoteLookup';
import type {Message} from '../../shared/contracts';
const source:Message={id:10,account_id:1,group_id:11,account_name:'甲',group_name:'群',user_id:1,sender_name:'回复者',
  source_user_id:null,source_message_id:'r1',sent_at:'2026-08-23',message_type:'text',content_text:'「原文」 - - - 回复',is_deleted:0,deleted_at:null,attachment_count:0};
const target={...source,id:1,content_text:'原文',sender_name:'原作者',attachments:[]};
it('人工候选选择后需确认保存，并展示来源区别',async()=>{
  const user=userEvent.setup();let bound=false;
  const fetcher=vi.fn(async(path:string,init?:RequestInit)=>{
    if(path.includes('reference-candidates'))return new Response(JSON.stringify({items:[target],has_more:false,offset:0}));
    if(init?.method==='PUT')bound=true;
    return new Response(JSON.stringify({source_message_id:10,account_id:1,group_id:11,deleted_only:false,
      match_method:bound?'manual':'full_text',status:bound?'matched':'ambiguous',target:bound?target:null,
      items:bound?[target]:[],has_older:false,has_newer:false}));
  });
  vi.stubGlobal('fetch',fetcher);render(<QuoteLookup source={source}/>);
  await user.click(screen.getByRole('button',{name:'查看引用原文'}));
  await user.click(await screen.findByRole('button',{name:'选择并永久关联原消息'}));
  await user.click(await screen.findByRole('button',{name:'选择原消息 #1'}));
  expect(bound).toBe(false);
  await user.click(screen.getByRole('button',{name:'确认保存引用关系'}));
  await screen.findByText(/人工确认的永久关联/);
  const call=fetcher.mock.calls.find(([,init])=>init?.method==='PUT')!;
  expect(JSON.parse(call[1]!.body as string)).toMatchObject({target_message_id:1,confirmed:true,account_id:1,group_id:11});
  await waitFor(()=>expect(screen.getByLabelText('匹配消息 #1')).toBeInTheDocument());
});
