import React,{useState} from 'react';
import type {ManagedAccount,ManagedGroup,AccountForm,GroupForm} from './model';

type Actions<T>={busy:boolean;save:(form:T)=>void;cancel:()=>void};
export function AccountEditor({item,busy,save,cancel}:{item:ManagedAccount|null}&Actions<AccountForm>){
 const [form,setForm]=useState<AccountForm>({display_name:item?.display_name||'',notes:item?.notes||'',is_active:item?!!item.is_active:true});
 return <form className="management-editor" onSubmit={e=>{e.preventDefault();save(form);}}>
  <h3>{item?'编辑账号':'新增账号'}</h3>
  <label>账号名称<input required maxLength={100} value={form.display_name} onChange={e=>setForm({...form,display_name:e.target.value})}/></label>
  <label>账号备注<textarea maxLength={2000} value={form.notes} onChange={e=>setForm({...form,notes:e.target.value})}/></label>
  <label><input type="checkbox" checked={form.is_active} onChange={e=>setForm({...form,is_active:e.target.checked})}/>启用账号</label>
  <p>停用保留历史，停止排队任务并暂停归档；运行中的采集请先安全停止。重新启用后仍需手动恢复任务。</p>
  <button disabled={busy} type="submit">保存账号</button><button disabled={busy} type="button" onClick={cancel}>取消编辑</button>
 </form>;
}
export function GroupEditor({item,accounts,busy,save,cancel}:{item:ManagedGroup|null;accounts:ManagedAccount[]}&Actions<GroupForm>){
 const eligible=accounts.filter(a=>a.id===item?.account_id||a.group_count===0);
 const [form,setForm]=useState<GroupForm>({account_id:item?.account_id||eligible[0]?.id||0,name:item?.name||'',description:item?.description||'',source_group_id:item?.source_group_id||null,is_active:item?!!item.is_active:true});
 const canSave=!busy&&eligible.some(a=>a.id===form.account_id);
 return <form className="management-editor" onSubmit={e=>{e.preventDefault();if(canSave)save(form);}}>
  <h3>{item?'编辑群聊':'新增群聊'}</h3>
  <label>所属账号<select disabled={!!item} value={form.account_id} onChange={e=>setForm({...form,account_id:Number(e.target.value)})}>
   <option value="0">请选择无群账号</option>
   {accounts.map(a=><option key={a.id} value={a.id} disabled={!eligible.includes(a)}>{a.display_name}{a.is_active?'':'（已停用）'}</option>)}
  </select></label>
  <label>群聊名称<input required maxLength={200} value={form.name} onChange={e=>setForm({...form,name:e.target.value})}/></label>
  <label>群聊备注<textarea maxLength={2000} value={form.description} onChange={e=>setForm({...form,description:e.target.value})}/></label>
  <label>微博群 ID<input disabled={accounts.some(a=>a.id===form.account_id&&a.group_count>1)} inputMode="numeric" pattern="[0-9]*" maxLength={64} value={form.source_group_id||''} onChange={e=>setForm({...form,source_group_id:e.target.value||null})}/></label>
  <p>群 ID 可暂留空，随后进入扫码与绑定。已有历史的群不能更换所属账号或已绑定的微博群 ID。</p>
  <p>一个账号只能对应一个群聊（包含已停用群聊）。无可选账号时，请先新增账号。</p>
  <label><input type="checkbox" checked={form.is_active} onChange={e=>setForm({...form,is_active:e.target.checked})}/>启用群聊</label>
  <button disabled={!canSave} type="submit">保存群聊</button><button disabled={busy} type="button" onClick={cancel}>取消编辑</button>
 </form>;
}
