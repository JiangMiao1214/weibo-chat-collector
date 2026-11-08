import React,{useState} from 'react';
import {useReadRequest} from '../messages/useReadRequest';
import {RequestNotice} from '../messages/ReaderNavigation';
import {LogResource} from './LogResource';
import {LogTask} from './LogTask';
import {parameters,states,actions,type Scope,type OpenMessages} from './model';
type Event={id:number;occurred_at:string;account_id:number;group_id:number;category:string;task_id:number|null;message_id:number|null;action:string;result:string;error_code:string;source_url:string|null;failure_url:string|null;is_deleted:number|null};
export function Logs({scope,initialResult='',messageDates,openMessages}:{scope:Scope;initialResult?:string;messageDates?:{from:string;to:string};openMessages:OpenMessages}){
 const [filter,setFilter]=useState({category:initialResult?'archive':'',task_id:'',action:'',result:initialResult,error_code:''});const [offset,setOffset]=useState(0);const [selected,setSelected]=useState<Event|null>(null);
 const params=parameters(scope);params.delete('deleted_only');for(const [key,value] of Object.entries(filter))if(value)params.set(key,value);
 if(messageDates?.from)params.set('message_date_from',messageDates.from);if(messageDates?.to)params.set('message_date_to',messageDates.to);
 const exportUrl='/api/insights/logs/export?'+params;params.set('offset',String(offset));
 const state=useReadRequest<{items:Event[];total:number;has_more:boolean;retention:string}>('/api/insights/logs?'+params);
 return <><h2>操作日志</h2>{messageDates&&<p>来自统计：限定消息日期 {messageDates.from||"不限"} — {messageDates.to||"不限"}；下方日志日期为事件发生时间。</p>}<p>日志按发生时间（UTC）筛选，包含普通消息与回收站的操作元数据。资源失败只影响单项；account_protection 表示账号保护性暂停。</p>
 <div className="insight-filters"><label>操作类型<select value={filter.category} onChange={e=>{setFilter({...filter,category:e.target.value});setOffset(0);}}><option value="">全部</option><option value="collection">采集</option><option value="archive">归档</option><option value="ocr">OCR</option></select></label>
 {(['task_id','action','result','error_code'] as const).map((key,i)=><label key={key}>{['任务 ID','操作代码','结果代码','错误码'][i]}<input value={filter[key]} type={key==='task_id'?'number':'text'} min={1} onChange={e=>{setFilter({...filter,[key]:e.target.value});setOffset(0);}}/></label>)}
 <button onClick={state.retry}>刷新日志</button><a href={exportUrl} download>导出脱敏诊断记录</a></div>
 <p className="muted">操作：start / pause / resume / skip / finish；结果：failed / blocked / stored。链接参数已脱敏，实际访问请进入归档处理。</p>
 <RequestNotice {...state}/>{state.data&&<><p>{state.data.retention} 共 {state.data.total} 条。</p>
 <div className="log-list">{state.data.items.map(row=><article key={row.id}>
 <header><time>{row.occurred_at} UTC</time><strong>{row.category} #{row.task_id??'群控制'} · {actions[row.action]||row.action} · {states[row.result]||row.result}</strong></header>
 <p>账号 #{row.account_id} / 群 #{row.group_id} · {row.error_code||'无错误码'}</p>
 {row.source_url&&<p>原始地址（脱敏）：<code>{row.source_url}</code></p>}{row.failure_url&&<p>实际失败地址（脱敏）：<code>{row.failure_url}</code></p>}
 {row.message_id&&row.is_deleted!==null&&<button onClick={()=>openMessages({accountId:String(row.account_id),groupId:String(row.group_id)},{id:row.message_id!,account_id:row.account_id,group_id:row.group_id,is_deleted:row.is_deleted!},!!row.is_deleted)}>定位消息 #{row.message_id}</button>}
 <button onClick={()=>setSelected(row)}>查看处理操作</button>
 </article>)}</div>
 <button disabled={offset===0} onClick={()=>setOffset(v=>Math.max(0,v-50))}>上一页日志</button><button disabled={!state.data.has_more} onClick={()=>setOffset(v=>v+50)}>下一页日志</button></>}
 {selected&&<section className="insight-action"><button onClick={()=>setSelected(null)}>关闭处理操作</button><p>账号 #{selected.account_id} / 群 #{selected.group_id}</p>
 {selected.category==='collection'&&selected.task_id&&<LogTask id={selected.task_id} account={selected.account_id} group={selected.group_id}/>}
 {selected.category==='archive'&&<LogResource key={selected.id} eventId={selected.id} account={selected.account_id} group={selected.group_id} deleted={!!selected.is_deleted}/>}
 {selected.category==='ocr'&&<p>请进入“图片文字搜索”，选择此账号和群查看 OCR 进度与重试失败项。</p>}
 </section>}
 </>;
}
