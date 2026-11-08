import React from 'react';
import type {FilterOptions} from '../../shared/contracts';
import type {Scope} from './model';
export function ScopeControls({value,onChange,options,allowDeleted=true}:{value:Scope;onChange:(s:Scope)=>void;options:FilterOptions;allowDeleted?:boolean}){
 return <div className="insight-filters">
 <label>账号<select value={value.account} onChange={e=>onChange({...value,account:e.target.value,group:''})}><option value="">全部账号</option>{options.accounts.map(a=><option key={a.id} value={a.id}>{a.display_name}</option>)}</select></label>
 <label>群聊<select value={value.group} onChange={e=>onChange({...value,group:e.target.value})}><option value="">全部群聊</option>{options.groups.filter(g=>!value.account||String(g.account_id)===value.account).map(g=><option key={g.id} value={g.id}>{g.name} · #{g.id}</option>)}</select></label>
 <label>开始日期<input type="date" value={value.from} onChange={e=>onChange({...value,from:e.target.value})}/></label>
 <label>结束日期<input type="date" value={value.to} onChange={e=>onChange({...value,to:e.target.value})}/></label>
 {allowDeleted&&<label><input type="checkbox" checked={value.deleted} onChange={e=>onChange({...value,deleted:e.target.checked})}/>仅回收站</label>}
 </div>;
}
