import React,{useState} from 'react';
import {useReadRequest} from '../messages/useReadRequest';
import {RequestNotice} from '../messages/ReaderNavigation';
import {fetchJson} from '../../shared/http';
import {states} from './model';
export function LogTask({id,account,group}:{id:number;account:number;group:number}){
 const state=useReadRequest<{id:number;account_id:number;group_id:number;status:string;page_count:number;range_start:string;range_end:string;stop_code:string|null;resume_not_before:string|null}>(`/api/collection-jobs/${id}`);
 const [confirmed,setConfirmed]=useState(false);const [busy,setBusy]=useState(false);const [error,setError]=useState('');
 async function action(name:string){setBusy(true);setError('');try{await fetchJson(`/api/collection-jobs/${id}/${name}`,'任务操作',{method:'POST'});setConfirmed(false);state.retry();}catch(e){setError(String(e));}finally{setBusy(false);}}
 const task=state.data;
 return <section className="log-task"><h3>采集任务 #{id}</h3><RequestNotice {...state}/>{error&&<p role="alert">{error}</p>}
 {task&&task.account_id===account&&task.group_id===group&&<><p>{states[task.status]||task.status} · {task.range_start} — {task.range_end} · 已提交 {task.page_count} 页</p><p>停止码：{task.stop_code||'无'} · 冷却至：{task.resume_not_before||'无'}</p>
 {task.status==='awaiting_confirmation'&&<label><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>已关闭此账号的微博 App 和网页，确认启动采集</label>}
 {task.status==='awaiting_confirmation'&&<button disabled={!confirmed||busy} onClick={()=>void action('confirm')}>确认并启动任务</button>}
 {['queued','awaiting_confirmation','running'].includes(task.status)&&<button disabled={busy} onClick={()=>void action('stop')}>安全停止任务</button>}
 {task.status==='stopped'&&<button disabled={busy} onClick={()=>void action('resume')}>沿断点续传（仍需确认）</button>}
 <button onClick={state.retry}>刷新任务状态</button></>}
 </section>;
}
