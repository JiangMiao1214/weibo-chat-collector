import React from 'react';
import type {FilterOptions} from '../../shared/contracts';
import {hasMultipleGroups} from './selection';

export function GroupLimitNotice({data,accountId}:{data:FilterOptions;accountId:string}){
 if(hasMultipleGroups(data,accountId))return <p role="status">已有多个群聊，待整理；历史全部保留，暂不能增加或变更群聊绑定。</p>;
 return <p>一个账号对应一个群聊。尚未配置群聊时，可先到群聊管理中新增。</p>;
}
