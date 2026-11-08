import React,{useState} from 'react';
import {useReadRequest} from '../messages/useReadRequest';
import {RequestNotice} from '../messages/ReaderNavigation';
import {parameters,messageFilters,bucketEnd,states,type Scope,type OpenMessages} from './model';
type Data={message_count:number;trend:{date:string;count:number;members:number}[];types:{name:string;count:number}[];
 members:{user_id:number;display_name:string;count:number}[];archives:Record<string,number>;pages:{text_stored:number;media_complete:number};
 storage:{files:number;bytes:number;missing_paths:number;by_kind?:Record<string,{files:number;bytes:number}>};coverage_note:string};
export function Dashboard({scope,openMessages,openLogs}:{scope:Scope;openMessages:OpenMessages;openLogs:(result:string)=>void}){
 const [period,setPeriod]=useState('day');const params=parameters(scope);params.set('period',period);
 const state=useReadRequest<Data>('/api/insights/dashboard?'+params);const data=state.data;
 const max=Math.max(1,...(data?.trend.map(r=>r.count)??[]));
 return <><h2>统计仪表盘</h2><label>趋势粒度<select value={period} onChange={e=>setPeriod(e.target.value)}><option value="day">日</option><option value="week">周（周一开始）</option><option value="month">月</option></select></label>
 <button onClick={state.retry}>刷新统计</button><RequestNotice {...state}/>
 {data&&<><p>{data.coverage_note}</p><div className="insight-cards">
 <article><strong>{data.message_count}</strong><span>本地消息</span></article><article><strong>{(data.storage.bytes/1048576).toFixed(2)} MiB</strong><span>{data.storage.files} 个唯一原件 · 缺失路径 {data.storage.missing_paths}</span></article>
 <article><strong>{data.pages.text_stored}</strong><span>正文已保存 · 已识别媒体全部保存 {data.pages.media_complete}</span></article></div>
 <p className="muted">磁盘值为本筛选引用的实际唯一原件；跨群共享文件在全库只计一次，缓存、备份和数据库不在此数内。</p>
 <div className="insight-counts">{Object.entries(data.storage.by_kind??{}).map(([kind,usage])=><span key={kind}>{({image:"图片",video:"视频",audio:"音频",file:"文件",link:"网页"} as Record<string,string>)[kind]||kind}：{usage.files} 个 · {(usage.bytes/1048576).toFixed(2)} MiB</span>)}</div>
 <h3>消息趋势 · 点击日期进入消息</h3><div className="trend-grid">{data.trend.map(row=><button key={row.date} onClick={()=>openMessages({...messageFilters(scope),dateFrom:scope.from&&scope.from>row.date?scope.from:row.date,dateTo:scope.to&&scope.to<bucketEnd(row.date,period)?scope.to:bucketEnd(row.date,period)},undefined,scope.deleted)}>
 <span>{row.date}</span><span className="trend-bar" style={{width:`${100*row.count/max}%`}}/><strong>{row.count} 条</strong><small>{row.members} 位活跃成员</small></button>)}</div>
 {!data.trend.length&&<p>此范围没有本地消息；采集覆盖未知。</p>}
 <div className="insight-columns"><section><h3>消息类型</h3>{data.types.map(t=><p key={t.name}>{t.name}：{t.count}</p>)}</section>
 <section><h3>活跃成员（前 50）</h3>{data.members.map(m=><button key={m.user_id} onClick={()=>openMessages({...messageFilters(scope),viewer:JSON.stringify({members:[m.user_id]})},undefined,scope.deleted)}>{m.display_name} #{m.user_id} · {m.count}</button>)}</section>
 <section><h3>归档状态 · 点击查看日志</h3>{Object.entries(data.archives).map(([status,count])=><button key={status} onClick={()=>openLogs(status)}>{states[status]||status}：{count}</button>)}</section></div></>}
 </>;
}
