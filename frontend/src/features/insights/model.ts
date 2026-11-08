import type {Filters} from '../../shared/contracts';
import type {MessageRef} from '../messages/ReaderNavigation';
export type Scope={account:string;group:string;from:string;to:string;deleted:boolean};
export const emptyScope:Scope={account:'',group:'',from:'',to:'',deleted:false};
export type OpenMessages=(filters:Partial<Filters>,target?:MessageRef,deleted?:boolean)=>void;
export function parameters(scope:Scope){const params=new URLSearchParams();
 for(const [key,value] of Object.entries({account_id:scope.account,group_id:scope.group,date_from:scope.from,date_to:scope.to}))if(value)params.set(key,value);
 params.set('deleted_only',String(scope.deleted));return params;
}
export function messageFilters(scope:Scope):Partial<Filters>{return {accountId:scope.account,groupId:scope.group,dateFrom:scope.from,dateTo:scope.to};}
export function bucketEnd(day:string,period:string){const d=new Date(day+'T12:00:00Z');if(period==='week')d.setUTCDate(d.getUTCDate()+6);
 if(period==='month'){d.setUTCMonth(d.getUTCMonth()+1);d.setUTCDate(0);}return d.toISOString().slice(0,10);}
export const states:Record<string,string>={queued:'等待处理',downloading:'正在下载',processing:'正在识别',done:'识别完成',stored:'已保存',failed:'失败',blocked:'保护性暂停',skipped:'已跳过',paused:'已暂停',running:'运行中',stopped:'已停止',completed:'已完成',awaiting_confirmation:'待确认'};
export const actions:Record<string,string>={created:'创建',start:'开始',resume:'恢复',stop:'停止',finish:'完成',pause:'暂停',retry:'重试',skip:'跳过',baseline:'升级时状态',state:'状态变化',stop_requested:'请求停止',page:'提交分页'};
