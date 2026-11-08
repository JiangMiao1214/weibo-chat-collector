import React,{useEffect,useRef,useState} from 'react';
import type {Message} from '../../shared/contracts';
import {fetchJson} from '../../shared/http';
import type {QuoteResult} from './useQuoteLookup';

type Candidates={items:Message[];has_more:boolean;offset:number};
function scopeQuery(source:Message){return new URLSearchParams({account_id:String(source.account_id),group_id:String(source.group_id),deleted_only:String(!!source.is_deleted)});}
export function ReferenceEditor({source,method,onSaved}:{source:Message;method:QuoteResult['match_method'];onSaved:(method:QuoteResult["match_method"])=>void}){
 const [open,setOpen]=useState(false);const [data,setData]=useState<Candidates|null>(null);
 const [query,setQuery]=useState('');const [selected,setSelected]=useState<Message|null>(null);
 const [remove,setRemove]=useState(false);const [error,setError]=useState('');const [busy,setBusy]=useState(false);
 const live=useRef(true);const version=useRef(0);
 useEffect(()=>{live.current=true;return()=>{live.current=false;version.current++;};},[]);
 async function search(offset=0){
  setOpen(true);setBusy(true);setError('');setSelected(null);const ticket=++version.current;
  const params=scopeQuery(source);params.set('q',query);params.set('offset',String(offset));
  try{const next=await fetchJson<Candidates>(`/api/messages/${source.id}/reference-candidates?${params}`,'读取原文候选');
   if(!next.items.every(m=>m.account_id===source.account_id&&m.group_id===source.group_id&&!!m.is_deleted===!!source.is_deleted))throw new Error('候选响应范围不一致');
   if(live.current&&ticket===version.current)setData(next);
  }catch(e){if(live.current&&ticket===version.current)setError(e instanceof Error?e.message:'查询失败');}
  finally{if(live.current&&ticket===version.current)setBusy(false);}
 }
 async function save(deleting=false){
  setBusy(true);setError('');
  try{const params=scopeQuery(source);params.set('confirmed','true');
   await fetchJson(`/api/messages/${source.id}/reference${deleting?`?${params}`:''}`,'更新引用关系',{
    method:deleting?'DELETE':'PUT',headers:{'Content-Type':'application/json'},
    body:deleting?undefined:JSON.stringify({account_id:source.account_id,group_id:source.group_id,deleted_only:!!source.is_deleted,target_message_id:selected?.id,confirmed:true})});
   if(live.current){setOpen(false);setRemove(false);setSelected(null);onSaved(deleting?"full_text":"manual");}
  }catch(e){if(live.current)setError(e instanceof Error?e.message:'保存失败');}
  finally{if(live.current)setBusy(false);}
 }
 return <section className="reference-editor" aria-label="管理永久引用">
  <p className="muted">人工选择会保存永久关联，并标为人工确认；不会当作微博接口确认。</p>
  <button disabled={busy} onClick={()=>void search()}>选择并永久关联原消息</button>
  {method!=='full_text'&&<button disabled={busy} onClick={()=>setRemove(true)}>解除永久关联</button>}
  {error&&<p role="alert">{error}</p>}
  {remove&&<div><p>解除后将恢复文字匹配，可能再次出现多个候选。确认解除？</p>
   <button disabled={busy} onClick={()=>void save(true)}>确认解除引用关系</button><button disabled={busy} onClick={()=>setRemove(false)}>保留关联</button></div>}
  {open&&<div><form onSubmit={e=>{e.preventDefault();void search();}}><label>原文关键词<input maxLength={200} value={query} onChange={e=>setQuery(e.target.value)}/></label>
   <button disabled={busy} type="submit">查找原消息</button></form>
   <p>留空查找相同引用文字；也可按关键词查找。仅列同账号、同群、同删除状态且不晚于回复的消息。</p>
   {data?.items.map(item=><article key={item.id} className="quote-context-message">
    <p>#{item.id} · {item.sender_name} · {item.sent_at} · 源 ID：{item.source_message_id||'未提供'}</p>
    <p>{item.content_text}</p><button disabled={busy} aria-label={`选择原消息 #${item.id}`} onClick={()=>setSelected(item)}>选择此原消息</button></article>)}
   {data?.items.length===0&&<p>没有候选；可修改关键词，或先导入缺失原文。</p>}
   <button disabled={busy||!data||data.offset===0} onClick={()=>void search(Math.max(0,(data?.offset||0)-20))}>上一页候选</button>
   <button disabled={busy||!data?.has_more} onClick={()=>void search((data?.offset||0)+20)}>下一页候选</button>
   {selected&&<div><p>确认将消息 #{source.id} 永久关联至 #{selected.id}（{selected.sender_name} · {selected.sent_at}）？</p>
    <button disabled={busy} onClick={()=>void save()}>确认保存引用关系</button></div>}
  </div>}
 </section>;
}
