import React,{useState} from 'react';
import {useReadRequest} from '../messages/useReadRequest';
import {RequestNotice} from '../messages/ReaderNavigation';
import {ArchivePanel} from '../messages/ArchivePanel';
import {fetchJson} from '../../shared/http';
import {states} from './model';
export function LogResource({eventId,account,group,deleted}:{eventId:number;account:number;group:number;deleted:boolean}){
 const state=useReadRequest<{id:number;kind:string;status:string;source_url:string|null;failure_url:string|null}>(`/api/insights/events/${eventId}/resource?account_id=${account}&group_id=${group}&deleted_only=${deleted}`);
 const [busy,setBusy]=useState(false);const [notice,setNotice]=useState('');const data=state.data;
 async function action(name:string){if(!data)return;setBusy(true);try{await fetchJson(`/api/archives/${data.id}/${name}`,'处理归档',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({account_id:account,group_id:group})});setNotice('已更新此项；群归档的暂停状态保持不变。');state.retry();}catch(e){setNotice(String(e));}finally{setBusy(false);}}
 return <><RequestNotice {...state}/>{notice&&<p role="status">{notice}</p>}{data&&<><p>当前资源 #{data.id} · {states[data.status]||data.status}</p>
 {data.source_url&&<p><a href={data.source_url} target="_blank" rel="noreferrer">打开原始访问地址</a></p>}
 {data.failure_url&&<p><a href={data.failure_url} target="_blank" rel="noreferrer">打开实际失败地址</a></p>}
 {!deleted&&['failed','blocked','skipped'].includes(data.status)&&<button disabled={busy} onClick={()=>void action('retry')}>重新下载这一项</button>}
 {!deleted&&data.kind==='link'&&['failed','blocked'].includes(data.status)&&<button disabled={busy} onClick={()=>void action('skip')}>确认无权访问，跳过此链接</button>}
 </>}<ArchivePanel accountId={String(account)} groupId={String(group)} disabled={deleted}/></>;
}
