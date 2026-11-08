import React from 'react';
import type {ViewMode} from './contracts';
const tabs:[ViewMode,string][]=[['active','消息'],['deleted','回收站'],['accounts','账号管理'],['groups','群聊管理'],
 ['dashboard','统计仪表盘'],['logs','操作日志'],['ocr','图片文字搜索'],['jobs','采集任务'],['monitor','采集监控'],['tools','高级工具 / 数据导入'],['weibo','微博验证']];
export function ViewNavigation({value,onChange}:{value:ViewMode;onChange:(value:ViewMode)=>void}){
 return <>{tabs.map(([key,label])=><button key={key} className={value===key?'icon-button':'secondary-button'}
  type="button" aria-pressed={value===key} onClick={()=>onChange(key)}>{label}</button>)}</>;
}
