import React,{useState} from 'react';
import type {Message} from '../../shared/contracts';
import type {MessageRef} from './contextTypes';
import {useContinuousContext,type WindowData} from './useContinuousContext';
import {useQuoteLookup} from './useQuoteLookup';
import {splitQuote} from './presentation';
import {RecoverContext} from './RecoverContext';
import './continuous-reader.css';

type Frame={source:MessageRef;initial?:WindowData;quote?:Message};
function QuoteFrame({source,jump,follow}:{source:Message;jump?:(m:MessageRef)=>void;follow:(m:Message)=>void}){
 const [attempt,setAttempt]=useState(0);const {data,error}=useQuoteLookup(source,attempt);
 if(error)return <p role="alert">{error}<button onClick={()=>setAttempt(x=>x+1)}>重试定位</button></p>;
 if(!data)return <p role="status">正在定位引用…</p>;
 if(!data.target)return <><p>{data.status==='ambiguous'?'存在多个匹配项，请返回原列表管理引用关系':'同账号、同群的本地范围内没有找到唯一原文'}</p>{data.status==='not_found'&&<RecoverContext source={source}/>}</>;
 return <Window source={data.target} initial={data} jump={jump} follow={follow}/>;
}
function Window({source,initial,jump,follow}:{source:MessageRef;initial?:WindowData;jump?:(m:MessageRef)=>void;follow:(m:Message)=>void}){
 const state=useContinuousContext(source,initial);const [automatic,setAutomatic]=useState(true);
 return <>
 <label><input type="checkbox" checked={automatic} onChange={e=>setAutomatic(e.target.checked)}/>滚动到边缘自动加载</label>
 <p className="muted">旧 → 新 · 窗口最多 100 条，移出的消息可反向加载</p>
 {state.error&&<p role="alert">{state.error}<button onClick={state.retry}>重试</button></p>}
 {state.loading&&<p role="status">读取中…</p>}
 <div className="context-rows" ref={state.rows} tabIndex={0} role="region" aria-label="上下文消息列表" onScroll={e=>{const box=e.currentTarget;
  if(!automatic||state.loading||box.scrollHeight<=box.clientHeight)return;
  if(box.scrollTop<25&&state.data.has_older)void state.load('older');
  else if(box.scrollHeight-box.clientHeight-box.scrollTop<25&&state.data.has_newer)void state.load('newer');
 }}>
 {state.data.items.map(item=><article key={item.id} data-context-id={item.id} className={`context-row${item.id===source.id?' is-target':''}`}>
 <strong>{item.sender_name}</strong> <time>{item.sent_at}</time><small> #{item.id}</small><p>{item.content_text||'(无文本内容)'}</p>
 {jump&&<button type="button" aria-label={`跳到列表 #${item.id}`} onClick={()=>jump(item)}>跳到列表</button>}
 {(item.reference_method||splitQuote(item.content_text))&&<button type="button" onClick={()=>follow(item)}>继续查看引用 #{item.id}</button>}
 </article>)}
 </div><div className="context-pagination">
 <button type="button" disabled={state.loading||!state.data.has_older} onClick={()=>void state.load('older')}>查看更早上下文</button>
 <button type="button" disabled={state.loading||!state.data.has_newer} onClick={()=>void state.load('newer')}>查看更新上下文</button>
 {state.expanded&&<button type="button" disabled={state.loading} onClick={()=>void state.load('center')}>回到原消息</button>}
 </div></>;
}
export function ContinuousReader({source,initial,jump}:{source:MessageRef;initial?:WindowData;jump?:(m:MessageRef)=>void}){
 const [frames,setFrames]=useState<Frame[]>([{source,initial}]);
 function follow(quote:Message){setFrames(current=>current.length>=20?current:[...current,{source:quote,quote}]);}
 return <div className="continuous-reader">
 {frames.length>1&&<button type="button" onClick={()=>setFrames(current=>current.slice(0,-1))}>返回上一级引用（{frames.length-1}）</button>}
 {frames.length>=20&&<p role="status">已达到 20 层阅读记录，请先返回上一级</p>}
 {frames.map((frame,index)=><div className="context-frame" key={index} hidden={index!==frames.length-1}>
 {frame.quote?<QuoteFrame source={frame.quote} jump={jump} follow={follow}/>:<Window source={frame.source} initial={frame.initial} jump={jump} follow={follow}/>}
 </div>)}
 </div>;
}
