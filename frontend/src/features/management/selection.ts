import type {FilterOptions,Filters} from '../../shared/contracts';
import {emptyFilters} from '../messages/model';

type Target={accountId:string;groupId:string};
export function retainExistingTarget<T extends Target>(current:T,data:FilterOptions):T{
 if(current.accountId&&!data.accounts.some(a=>String(a.id)===current.accountId))return {...current,accountId:'',groupId:''};
 if(current.groupId&&!data.groups.some(g=>String(g.id)===current.groupId&&(!current.accountId||String(g.account_id)===current.accountId)))
  return {...current,groupId:''};
 return current;
}
export function retainExistingFilters(current:Filters,data:FilterOptions):Filters{
 return retainExistingTarget(current,data)===current?current:{...emptyFilters};
}

export function hasMultipleGroups(data:Pick<FilterOptions,'groups'>,accountId:string|number|undefined){
 return data.groups.filter(g=>String(g.account_id)===String(accountId)).length>1;
}
export function bindingDisabled(data:FilterOptions,accountId:number|undefined,current:string|null|undefined,candidate:string|null|undefined,busy:boolean){
 return busy||!accountId||(hasMultipleGroups(data,accountId)&&current!==candidate);
}
