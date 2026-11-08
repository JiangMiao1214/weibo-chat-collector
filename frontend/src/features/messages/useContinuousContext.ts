import {useEffect,useLayoutEffect,useRef,useState} from 'react';
import type {Message} from '../../shared/contracts';
import type {ContextData,MessageRef} from './contextTypes';
import {chronological} from './presentation';
export type WindowData=Pick<ContextData,'items'|'has_older'|'has_newer'>;
export function mergeWindow(current:WindowData,page:WindowData,direction:'center'|'older'|'newer'):WindowData {
 const items=chronological([...new Map([...(direction==='center'?[]:current.items),...page.items].map(m=>[m.id,m])).values()]);
 const trimmed=items.length>100;
 return {items:direction==='older'?items.slice(0,100):items.slice(-100),
  has_older:direction==='newer'?(current.has_older||trimmed):page.has_older,
  has_newer:direction==='older'?(current.has_newer||trimmed):page.has_newer};
}
export function useContinuousContext(source:MessageRef,initial?:WindowData) {
 const [data,setData]=useState<WindowData>(initial?mergeWindow(initial,initial,'center'):{items:[],has_older:false,has_newer:false});
 const [error,setError]=useState('');const [loading,setLoading]=useState(false);
 const rows=useRef<HTMLDivElement>(null);const busy=useRef(false);const abort=useRef<AbortController|null>(null);
 const anchor=useRef<{id:string;top:number}|null>(null);const last=useRef<'center'|'older'|'newer'>('center');
 function remember(){const box=rows.current;if(!box)return;
  const visible=[...box.querySelectorAll<HTMLElement>('[data-context-id]')].find(e=>e.getBoundingClientRect().bottom>box.getBoundingClientRect().top);
  if(visible)anchor.current={id:visible.dataset.contextId!,top:visible.getBoundingClientRect().top};
 }
 async function load(direction:'center'|'older'|'newer') {
  if(busy.current)return;busy.current=true;setLoading(true);setError('');last.current=direction;remember();
  const controller=new AbortController();abort.current=controller;
  const target=direction==='center'?source:direction==='older'?data.items[0]:data.items.at(-1);
  const timer=window.setTimeout(()=>controller.abort(),15000);
  try {
   if(!target)throw new Error('没有可用上下文游标');
   const params=new URLSearchParams({account_id:String(source.account_id),group_id:String(source.group_id),deleted_only:String(!!source.is_deleted),direction});
   const response=await fetch(`/api/message-view/${target.id}/context?${params}`,{signal:controller.signal,cache:'no-store'});
   if(!response.ok)throw new Error(`读取上下文失败 HTTP ${response.status}`);
   const page:ContextData=await response.json();
   const scoped=(item:Message)=>item.account_id===source.account_id&&item.group_id===source.group_id&&!!item.is_deleted===!!source.is_deleted;
   if(page.target_id!==target.id||page.account_id!==source.account_id||page.group_id!==source.group_id||page.deleted_only!==!!source.is_deleted||!Array.isArray(page.items)||!page.items.every(scoped))throw new Error('上下文范围不匹配，已拒绝展示');
   if(!controller.signal.aborted)setData(current=>mergeWindow(current,page,direction));
  } catch(e){if(abort.current===controller)setError(controller.signal.aborted?'上下文读取超时，请重试':String(e));}
  finally{window.clearTimeout(timer);if(abort.current===controller){busy.current=false;setLoading(false);}}
 }
 useEffect(()=>{if(!initial)void load('center');return()=>{abort.current?.abort();abort.current=null;busy.current=false;};},[]);
 useLayoutEffect(()=>{const saved=anchor.current;const box=rows.current;
  const element=box?.querySelector<HTMLElement>(`[data-context-id="${saved?.id}"]`);
  if(box&&element&&saved)box.scrollTop+=element.getBoundingClientRect().top-saved.top;
  anchor.current=null;
 },[data]);
 return {data,rows,error,loading,load,expanded:last.current!=='center',retry:()=>void load(last.current)};
}
