import React, { useState, useEffect } from "react";
import type { WorkspaceProps as Props } from "./workspaceTypes";
import { MessageDetails } from "./WorkspacePanels";
import { formatMessageType, buildFilterPayload } from "./model";
import { MessageTimeline } from "./MessageTimeline";
import { hasDateScope, selectionOf, type Facets, type ViewerSelection } from "./viewerModel";
import { useReadRequest } from "./useReadRequest";
import { Members, HoursAndMedia, FilterSummary } from "./ReaderControls";
import { ContextPanel, RequestNotice, useReaderNavigation } from "./ReaderNavigation";
import { ReaderSearch, ExportResults } from "./ReaderSearch";
import { ReaderScope, AdvancedFilters } from "./ReaderFilters";
import { ArchivePanel } from "./ArchivePanel";
import {usePrependPosition} from "./usePrependPosition";
import "./messages.css";
import "./reader.css";
import "./compact-reader.css";

export function MessageWorkspace(props: Props) {
  const {filters, options, applySearch, viewMode, deleting, loading, messages, total, attachmentSummary,
    selectedMessageId, setSelectedMessageId, loadMore, hasMore, loadingMore} = props;
  const applied = props.appliedFilters ?? filters;
  const selection = selectionOf(applied);
  const [query, setQuery] = useState('');
  const [showDetail, setShowDetail] = useState(false);
  const navigation = useReaderNavigation(messages, selectedMessageId, setSelectedMessageId);
  const prepend = usePrependPosition(messages, JSON.stringify([applied,viewMode,navigation.location?.id]),
    loading, loadingMore, loadMore);
  useEffect(()=>{if(props.externalMessage)navigation.jump(props.externalMessage);},[props.externalMessage]);
  const facets = useReadRequest<Facets>(props.appliedFilters ? '/api/message-view/facets' : null,
    JSON.stringify({filters: buildFilterPayload(applied), deleted_only: viewMode === 'deleted'}));
  function changeViewer(patch: Partial<ViewerSelection>) {
    const types: Record<string, string> = {photos: 'image', video: 'video', links: 'link', '': ''};
    const next = {...filters, viewer: JSON.stringify({...selection, ...patch})};
    if (patch.media !== undefined) next.messageType = types[patch.media];
    applySearch(next);
  }
  function selectDetail(id: number) {
    navigation.setSource(null); setShowDetail(true); setSelectedMessageId(id);
  }
  const controls = {selection, data: facets.data, disabled: loading || deleting, onChange: changeViewer};
  const sideOpen = showDetail || !!navigation.source;
  return <section ref={navigation.root} className={`workspace chat-workspace compact-reader${sideOpen ? ' has-reader-side' : ''}`}>
    <header className="reader-heading">
      <ReaderScope {...props} />
      <AdvancedFilters {...props} deleting={deleting || !!navigation.location} />
    </header>
    <aside className="filter-panel" aria-label="成员侧栏">
      <Members {...controls} /><RequestNotice {...facets} />
    </aside>
    <section className="message-panel">
      <div className="reader-topline"><h2>{viewMode === 'deleted' ? '回收站' : '消息'}</h2>
        <span>{messages.length} / {total}</span>
        <ArchivePanel key={`${applied.accountId}:${applied.groupId}:${viewMode}`} accountId={applied.accountId} groupId={applied.groupId}
          disabled={!applied.accountId || !applied.groupId || viewMode !== 'active' || !!navigation.location} />
        <ExportResults filters={applied} viewMode={viewMode} disabled={loading || !!navigation.location} />
      </div>
      <HoursAndMedia {...controls} showHours={hasDateScope(applied)} messageType={filters.messageType}
        onType={type => applySearch({...filters, messageType: type, viewer: JSON.stringify({...selection, media: ''})})} />
      <ReaderSearch filters={applied} viewMode={viewMode} query={query} onQuery={setQuery} jump={navigation.jump} />
      <details className="reader-statistics"><summary>筛选与附件统计 · 附件 {attachmentSummary.total_count}</summary>
        <FilterSummary filters={applied} options={options} data={facets.data} />
        <div className="attachment-summary" aria-label="当前筛选结果附件统计">
          <span>含附件消息 {attachmentSummary.message_count}</span><strong>附件总数 {attachmentSummary.total_count}</strong>
          {Object.entries(attachmentSummary.by_type).map(([type, count]) => <span key={type}>{formatMessageType(type)} {count}</span>)}
        </div>
      </details>
      {navigation.location && <div className="location-banner" role="status">
        定位阅读 #{navigation.location.id} · 原筛选未修改
        <button type="button" onClick={navigation.restore}>返回筛选列表</button><RequestNotice {...navigation.focused} />
      </div>}
      <div className="message-list" ref={prepend.list} onScroll={prepend.onScroll}>
        {messages.length > 0 && <div className="load-more-row"><button className="secondary-button" type="button" onClick={prepend.loadEarlier}
          disabled={!hasMore || loadingMore || !!navigation.location}>
          {hasMore ? (loadingMore ? '加载中' : '加载更早消息') : '没有更多消息'}
        </button></div>}
        <MessageTimeline messages={navigation.visible} selectedMessageId={selectedMessageId}
          onSelect={selectDetail} onContext={message => {setShowDetail(false); navigation.setSource(message);}} query={query.trim()} />
        {!loading && navigation.visible.length === 0 && <div className="empty-state">没有符合条件的消息</div>}

      </div>
    </section>
    {navigation.source && <ContextPanel key={navigation.source.id} source={navigation.source}
      close={navigation.closeContext} jump={navigation.jump} />}
    {!navigation.source && showDetail && <div className="reader-detail-wrap">
      <MessageDetails {...props} onClose={() => setShowDetail(false)} />
    </div>}
  </section>;
}
