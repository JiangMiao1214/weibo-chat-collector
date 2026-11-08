import React,{useEffect,useState} from 'react';
import {useReadRequest} from '../messages/useReadRequest';
import {RequestNotice} from '../messages/ReaderNavigation';
import {fetchJson} from '../../shared/http';
import {parameters,states,type Scope,type OpenMessages} from './model';
type Progress={enabled:boolean;paused:boolean;counts:Record<string,number>;eligible:number;undiscovered:number;errors:{archive_id:number;message_id:number;error_code:string}[];engine:string;available:boolean;setup:string};
type Hit={archive_id:number;message_id:number;account_id:number;group_id:number;is_deleted:number;sent_at:string;text:string;image_url:string};
export function OcrPage({scope,openMessages}:{scope:Scope;openMessages:OpenMessages}){
 const specific=!!scope.account&&!!scope.group;
 const progress=useReadRequest<Progress>(specific?`/api/ocr/status?account_id=${scope.account}&group_id=${scope.group}`:null);
 const [query,setQuery]=useState('');const [search,setSearch]=useState('');const [offset,setOffset]=useState(0);const [busy,setBusy]=useState(false);const [error,setError]=useState('');
 const params=parameters(scope);params.set('q',search);params.set('offset',String(offset));
 const results=useReadRequest<{items:Hit[];total:number;has_more:boolean}>(search?'/api/ocr/search?'+params:null);
 useEffect(()=>{if(!specific)return;const timer=window.setInterval(progress.retry,5000);return()=>clearInterval(timer);},[scope.account,scope.group]);
 async function control(action:string){setBusy(true);setError('');try{await fetchJson('/api/ocr/control','OCR 操作',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({account_id:Number(scope.account),group_id:Number(scope.group),action})});progress.retry();}catch(e){setError(String(e));}finally{setBusy(false);}}
 const data=progress.data;
 return <><h2>图片文字搜索</h2><p>本地识别，不上传图片。只处理已归档原图；同账号相同图片复用结果，识别文字与消息正文分开。低并发一次 1 张，暂停会在当前图片处理后停止发布。</p>
 {!specific&&<p>选择一个账号和群聊，可开启历史补识别及新增图片自动识别。</p>}<RequestNotice {...progress}/>{error&&<p role="alert">{error}</p>}
 {data&&<section className="ocr-progress"><p>{data.setup} 引擎：{data.engine}</p><p>{data.enabled?(data.paused?'OCR 已暂停':'OCR 已启用'):'OCR 尚未开启'} · 可识别图片 {data.eligible} · 待加入队列 {data.undiscovered}</p>
 <div className="insight-counts">{Object.entries(data.counts).map(([key,count])=><span key={key}>{states[key]||key} {count}</span>)}</div>
 <button disabled={busy||!data.available||scope.deleted} onClick={()=>void control(data.enabled?'resume':'start')}>{data.enabled?'恢复识别':'开启本群 OCR 并补识别历史'}</button>
 <button disabled={busy||!data.enabled||data.paused} onClick={()=>void control('pause')}>暂停 OCR</button><button disabled={busy||!data.counts.failed} onClick={()=>void control('retry')}>重试识别失败项</button><button onClick={progress.retry}>刷新识别状态</button>
 {data.errors.map(e=><p key={e.archive_id}>图片 #{e.archive_id} · 消息 #{e.message_id} · {e.error_code}<button onClick={()=>openMessages({accountId:scope.account,groupId:scope.group},{id:e.message_id,account_id:Number(scope.account),group_id:Number(scope.group),is_deleted:0})}>查看原消息</button></p>)}
 </section>}
 <p className="muted">识别结果供检索参考，手写、斜拍、表格和细小尺寸数字请核对原图。搜索受上方日期及回收站范围限制，识别队列处理本群全部未删除的已归档图片。</p>
 <form onSubmit={e=>{e.preventDefault();setSearch(query.trim());setOffset(0);results.retry();}}><label>图片文字关键词<input value={query} maxLength={200} onChange={e=>setQuery(e.target.value)}/></label><button disabled={!query.trim()}>搜索图片文字</button></form>
 <RequestNotice {...results}/>{results.data&&<><p>共 {results.data.total} 张匹配图片</p><div className="ocr-hits">{results.data.items.map(hit=><article key={hit.archive_id}>
 <a href={hit.image_url} target="_blank" rel="noreferrer"><img loading="lazy" src={hit.image_url.replace('/content?','/thumbnail?')} alt={`匹配图片 #${hit.archive_id}`}/>打开本地原图</a><time>{hit.sent_at}</time><pre>{hit.text}</pre>
 <button onClick={()=>openMessages({accountId:String(hit.account_id),groupId:String(hit.group_id)},{id:hit.message_id,account_id:hit.account_id,group_id:hit.group_id,is_deleted:hit.is_deleted},!!hit.is_deleted)}>定位消息 #{hit.message_id}</button>
 </article>)}</div><button disabled={!offset} onClick={()=>setOffset(v=>Math.max(0,v-30))}>上一页图片</button><button disabled={!results.data.has_more} onClick={()=>setOffset(v=>v+30)}>下一页图片</button></>}
 </>;
}
