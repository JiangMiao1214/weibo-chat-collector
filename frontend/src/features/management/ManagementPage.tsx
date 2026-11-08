import React,{useEffect,useRef,useState} from 'react';
import {fetchJson} from '../../shared/http';
import {AccountCard,GroupCard} from './TargetCards';
import {AccountEditor,GroupEditor} from './TargetEditors';
import type {Overview,Navigate,ManagedAccount,ManagedGroup,AccountForm,GroupForm} from './model';
import './management.css';
export function ManagementPage({mode,onNavigate,onChanged}:{mode:'accounts'|'groups';onNavigate:Navigate;onChanged:()=>Promise<unknown>}){
 const subject=mode==='accounts'?'账号':'群聊';
 const [data,setData]=useState<Overview|null>(null);
 const [error,setError]=useState('');const [notice,setNotice]=useState('');const [busy,setBusy]=useState(false);
 const [accountFilter,setAccountFilter]=useState('');
 const [editing,setEditing]=useState<{account?:ManagedAccount|null;group?:ManagedGroup|null}|null>(null);
 const [clearing,setClearing]=useState<ManagedAccount|null>(null);
 const alive=useRef(true);const version=useRef(0);
 async function load(){
  const ticket=++version.current;
  try {const next=await fetchJson<Overview>('/api/management','读取管理状态');if(alive.current&&ticket===version.current){setData(next);setError('');}}
  catch(e){if(alive.current&&ticket===version.current)setError(e instanceof Error?e.message:'读取失败');}
 }
 useEffect(()=>{alive.current=true;void load();const timer=window.setInterval(()=>void load(),10000);
  return()=>{alive.current=false;version.current++;window.clearInterval(timer);};},[]);
 async function mutate(path:string,method:string,form?:AccountForm|GroupForm){
  setBusy(true);setError('');setNotice('');
  try {await fetchJson(path,'管理操作',{method,headers:{'Content-Type':'application/json'},body:form?JSON.stringify(form):undefined});
   if(!alive.current)return;
   setEditing(null);setClearing(null);setNotice('已保存；历史消息保留，暂停的任务和归档不会自动恢复。');
   await load();await onChanged();
  }catch(e){if(alive.current)setError(e instanceof Error?e.message:'操作失败');}
  finally{if(alive.current)setBusy(false);}
 }
 async function deleted(){
  setEditing(null);setClearing(null);setAccountFilter('');
  setNotice('本地账号、空群和凭据已删除，磁盘原件保留。');
  try{await load();await onChanged();}
  catch(e){if(alive.current)setError(`账号已删除，刷新失败：${e instanceof Error?e.message:'请刷新页面'}`);}
 }
 return <section className="management-page" aria-label={`${subject}管理页面`}>
  <header><h2>{subject}管理</h2><div className="management-actions">
   <button disabled={busy||!data} onClick={()=>{setClearing(null);setEditing(mode==='accounts'?{account:null}:{group:null});}}>新增{subject}</button>
   <button disabled={busy} onClick={()=>void load()}>刷新管理状态</button></div></header>
  <p>停用保留历史。已入库日期仅代表现有消息范围，不代表期间全部采齐。状态每 10 秒刷新。</p>
  {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
  {!data&&<p>正在读取本地管理状态…</p>}
  {clearing&&<div className="management-editor" role="alertdialog" aria-label="确认清除登录凭据">
   <p>清除“{clearing.display_name}”的本机登录凭据？保留历史，停止排队任务并暂停归档。之后需重新登录。</p>
   <button disabled={busy} onClick={()=>void mutate(`/api/management/accounts/${clearing.id}/credentials?confirmed=true`,'DELETE')}>确认清除凭据</button>
   <button disabled={busy} onClick={()=>setClearing(null)}>取消清除</button></div>}
  {editing&&'account' in editing&&<AccountEditor key={editing.account?.id||'new-account'} item={editing.account||null} busy={busy} cancel={()=>setEditing(null)}
   save={form=>void mutate(`/api/management/accounts${editing.account?`/${editing.account.id}`:''}`,editing.account?'PATCH':'POST',form)}/>}
  {editing&&'group' in editing&&data&&<GroupEditor key={editing.group?.id||'new-group'} item={editing.group||null} accounts={data.accounts} busy={busy} cancel={()=>setEditing(null)}
   save={form=>void mutate(`/api/management/groups${editing.group?`/${editing.group.id}`:''}`,editing.group?'PATCH':'POST',form)}/>}
  {mode==='groups'&&data&&<label>筛选所属账号<select value={accountFilter} onChange={e=>setAccountFilter(e.target.value)}>
   <option value="">全部账号</option>{data.accounts.map(a=><option key={a.id} value={a.id}>{a.display_name}</option>)}</select></label>}
  {data&&<ManagementList mode={mode} data={data} accountFilter={accountFilter} onNavigate={onNavigate}
    edit={setEditing} clear={setClearing} onDeleted={deleted}/>}

 </section>;
}

function ManagementList({mode,data,accountFilter,onNavigate,edit,clear,onDeleted}:{mode:'accounts'|'groups';data:Overview;accountFilter:string;
 onNavigate:Navigate;edit:(value:{account?:ManagedAccount|null;group?:ManagedGroup|null})=>void;clear:(account:ManagedAccount|null)=>void;onDeleted:()=>Promise<void>}){
 const groups=data.groups.filter(g=>!accountFilter||String(g.account_id)===accountFilter);
 return <div className="management-grid">{mode==='accounts'?data.accounts.map(a=><AccountCard key={a.id} item={a} groups={data.groups} navigate={onNavigate}
  edit={()=>{clear(null);edit({account:a});}} clear={()=>clear(a)} onDeleted={onDeleted}/>):
  groups.map(g=><GroupCard key={g.id} item={g} navigate={onNavigate}
   enabled={!!g.is_active&&!!data.accounts.find(a=>a.id===g.account_id)?.is_active} edit={()=>edit({group:g})}/>)}
  {!(mode==='accounts'?data.accounts.length:groups.length)&&<p>暂无符合条件的记录，可先新增账号和群聊。</p>}
 </div>;
}
