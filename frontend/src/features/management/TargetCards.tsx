import React from 'react';
import {ArchivePanel} from '../messages/ArchivePanel';
import {DeleteAccount} from './DeleteAccount';
import {stateLabels,type ManagedAccount,type ManagedGroup,type Navigate} from './model';
export function AccountCard({item,groups,edit,clear,navigate,onDeleted}:{item:ManagedAccount;groups:ManagedGroup[];edit:()=>void;clear:()=>void;navigate:Navigate;onDeleted:()=>Promise<void>}){
 const owned=groups.filter(g=>g.account_id===item.id);
 return <article className="management-card" aria-label={`账号 ${item.display_name}`}>
  <h3>{item.display_name} <small>#{item.id} · {item.is_active?'已启用':'已停用'}</small></h3>
  <p>{item.notes||'暂无备注'}</p><p>{item.group_count} 个群 · {item.message_count} 条未删除消息</p>
  <p>登录准备：{item.cookie_profile.authenticated?'本机凭据已就绪（未联网验证）':'需要登录或更新凭据'} · 更新时间：{item.cookie_profile.updated_at||'—'}</p>
  <p>关联群：{owned.map(g=>g.name).join('、')||'待配置群聊，请在群聊管理中新增'}</p>
  {owned.length>1&&<p role="status">已有多个群聊，待整理；历史全部保留，不能继续增加或绑定其他群聊。</p>}
  <p>最近任务：{owned.filter(g=>g.latest_collection).map(g=>`${g.name} #${g.latest_collection!.id} ${stateLabels[g.latest_collection!.status]||g.latest_collection!.status}`).join('；')||'暂无'}</p>
  <p>归档：{owned.filter(g=>g.archive).map(g=>`${g.name} ${g.archive?.paused?'已暂停':'已开启'}，保存 ${g.archive_counts.stored||0} 项`).join('；')||'未开启'}</p>
  <div className="management-actions"><button onClick={edit} aria-label={`编辑${item.display_name}`}>编辑账号</button>
   <button disabled={!item.is_active} onClick={()=>navigate('jobs',item.id,owned.length===1?owned[0].id:undefined)}>登录与采集</button>
   <button onClick={()=>navigate('active',item.id)}>查看账号消息</button>
   <button onClick={clear}>清除登录凭据</button></div>
  <DeleteAccount accountId={item.id} onDeleted={onDeleted}/>
 </article>;
}
export function GroupCard({item,enabled,edit,navigate}:{item:ManagedGroup;enabled:boolean;edit:()=>void;navigate:Navigate}){
 return <article className="management-card" aria-label={`群聊 ${item.name}`}>
  <h3>{item.name} <small>#{item.id} · {item.is_active?'已启用':'已停用'}</small></h3>
  <p>{item.account_name} · 微博群 ID：{item.source_group_id||'未绑定'}</p><p>{item.description||'暂无备注'}</p>
  <p>已入库日期：{item.first_message_at||'—'} ～ {item.last_message_at||'—'}</p>
  <p>{item.message_count} 条未删除消息 · {item.attachment_count} 个附件</p>
  <p>最近采集：{item.latest_collection?`#${item.latest_collection.id} ${stateLabels[item.latest_collection.status]||item.latest_collection.status}`:'暂无任务'}</p>
  <p>归档：{item.archive?(item.archive.paused?'已暂停':'已开启'):'未开启'} · 已保存 {item.archive_counts.stored||0} · 等待 {(item.archive_counts.queued||0)+(item.archive_counts.downloading||0)} · 失败 {(item.archive_counts.failed||0)+(item.archive_counts.blocked||0)}</p>
  {item.archive?.reason&&<p>{item.archive.reason}</p>}
  <div className="management-actions"><button onClick={edit} aria-label={`编辑${item.name}`}>编辑群聊</button>
   <button onClick={()=>navigate('active',item.account_id,item.id)}>查看消息</button>
   <button disabled={!enabled} onClick={()=>navigate('jobs',item.account_id,item.id)}>扫码绑定与采集</button>
   <button onClick={()=>navigate('monitor',item.account_id,item.id)}>全部采集监控</button>
   <ArchivePanel accountId={String(item.account_id)} groupId={String(item.id)} disabled={false}/></div>
 </article>;
}
