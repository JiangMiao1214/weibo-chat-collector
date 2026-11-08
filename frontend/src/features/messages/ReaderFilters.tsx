import React from 'react';
import type { WorkspaceProps } from './workspaceTypes';
import { BatchActions } from './WorkspacePanels';

export function ReaderScope({filters, options, applySearch}: WorkspaceProps) {
  const groups = options.groups.filter(group => !filters.accountId || String(group.account_id) === filters.accountId);
  const date = filters.dateFrom === filters.dateTo ? filters.dateFrom : '';
  return <div className="reader-scope" aria-label="阅读范围">
    <label><span>账号</span><select value={filters.accountId} onChange={event => applySearch({
      ...filters, accountId: event.target.value, groupId: '', viewer: '', messageType: ''})}>
      <option value="">全部账号</option>{options.accounts.map(account => <option key={account.id} value={account.id}>{account.display_name}</option>)}
    </select></label>
    <label><span>群聊</span><select value={filters.groupId} onChange={event => applySearch({...filters, groupId: event.target.value, viewer: '', messageType: ''})}>
      <option value="">全部群聊</option>{groups.map(group => <option key={group.id} value={group.id}>{group.name}</option>)}
    </select></label>
    <label><span>日期</span><input aria-label="阅读日期" type="date" value={date}
      onChange={event => applySearch({...filters, dateFrom: event.target.value, dateTo: event.target.value})} /></label>
    <button type="button" onClick={() => applySearch({...filters, dateFrom: '', dateTo: ''})}>全部日期</button>
  </div>;
}

export function AdvancedFilters(props: WorkspaceProps) {
  const {filters, updateFilter, applySearch, resetSearch} = props;
  return <details className="reader-advanced"><summary>高级筛选与批量操作</summary>
    <div className="advanced-popover">
      <p className="muted">以下是可选的精确条件；修改后点击搜索。类型统一使用上方按钮。</p>
      <div className="advanced-fields">
        <label><span>用户</span><input value={filters.user} placeholder="昵称或用户 ID"
          onChange={event => updateFilter('user', event.target.value)} /></label>
        <label><span>关键词</span><input value={filters.keyword} placeholder="筛选正文、链接、文件名"
          onChange={event => updateFilter('keyword', event.target.value)} /></label>
        <label><span>开始日期</span><input type="date" value={filters.dateFrom}
          onChange={event => updateFilter('dateFrom', event.target.value)} /></label>
        <label><span>结束日期</span><input type="date" value={filters.dateTo}
          onChange={event => updateFilter('dateTo', event.target.value)} /></label>
        <label><span>附件</span><select value={filters.hasAttachment} onChange={event => updateFilter('hasAttachment', event.target.value)}>
          <option value="">全部消息</option><option value="true">有附件</option><option value="false">无附件</option>
        </select></label>
      </div>
      <div className="button-row"><button type="button" onClick={() => applySearch()}>搜索</button>
        <button type="button" onClick={resetSearch}>重置</button></div>
      <BatchActions {...props} />
    </div>
  </details>;
}
