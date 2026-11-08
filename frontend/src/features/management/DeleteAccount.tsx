import React,{useState} from 'react';
import {fetchJson} from '../../shared/http';
import {useReadRequest} from '../messages/useReadRequest';

type Preview={account:{id:number;display_name:string};groups:{id:number;name:string}[];
 counts:Record<string,number>;blockers:string[];can_delete:boolean;credentials_present:boolean;preview_token:string};

export function DeleteAccount({accountId,onDeleted}:{accountId:number;onDeleted:()=>Promise<void>}){
 const [open,setOpen]=useState(false);const [busy,setBusy]=useState(false);
 const [error,setError]=useState('');const [invalid,setInvalid]=useState(false);
 const preview=useReadRequest<Preview>(open?`/api/management/accounts/${accountId}/delete-preview`:null);
 async function remove(){
  if(!preview.data?.can_delete||invalid||busy)return;
  setBusy(true);setError('');
  try{
   await fetchJson(`/api/management/accounts/${accountId}`,'删除本地账号',{method:'DELETE',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({confirmed:true,preview_token:preview.data.preview_token})});
   setOpen(false);await onDeleted();
  }catch(e){setError(e instanceof Error?e.message:'删除失败');setInvalid(true);}
  finally{setBusy(false);}
 }
 function refresh(){setInvalid(false);setError('');preview.retry();}
 return <>
  <button onClick={()=>{setError('');setInvalid(false);setOpen(true);}}>删除账号</button>
  {open&&<div className="management-editor" role="alertdialog" aria-label="确认删除账号">
   <h3>删除本地账号</h3>
   <p>只允许删除无历史数据的账号；不涉及注销微博账号。磁盘原件保留。</p>
   {preview.loading&&<p>正在检查账号与关联数据…</p>}
   {preview.data&&<>
    <p>账号：{preview.data.account.display_name}（#{preview.data.account.id}）</p>
    <p>关联群（仅无历史时随账号删除）：{preview.data.groups.map(g=>`${g.name}（#${g.id}）`).join('、')||'无'}</p>
    <p>本机登录凭据：{preview.data.credentials_present?'随账号删除':'无凭据文件'}</p>
    <p>正常消息 {preview.data.counts.messages} 条 · 回收站消息 {preview.data.counts.recycled_messages} 条</p>
    {preview.data.blockers.map(reason=><p key={reason}>{reason}</p>)}
    {preview.data.can_delete&&<p>已检查：无消息、任务断点、成员、附件、归档/OCR、导入、快照、验证、删除记录或操作日志。确认后不可撤销。</p>}
   </>}
   {(error||preview.error)&&<p role="alert">{error||preview.error}</p>}
   <button disabled={busy||invalid||!preview.data?.can_delete} onClick={()=>void remove()}>确认删除本地账号</button>
   <button disabled={busy||preview.loading} onClick={refresh}>重新预览</button>
   <button disabled={busy} onClick={()=>setOpen(false)}>取消删除</button>
  </div>}
 </>;
}
