import React, { useState } from 'react';
import type { Filters, FilterOptions } from '../../shared/contracts';
import { safeHttpUrl } from './presentation';
import { selectionOf, toggle, type Facets, type ViewerSelection } from './viewerModel';

export function MemberAvatar({url, name}: {url: string | null; name: string}) {
  const [failed, setFailed] = useState<string | null>(null);
  const safe = safeHttpUrl(url ?? '');
  return safe && failed !== safe ? <img className="member-avatar" src={safe} alt={`${name}的头像`}
    loading="lazy" referrerPolicy="no-referrer" onError={() => setFailed(safe)} />
    : <span className="member-avatar" aria-label={`${name}的文字头像`}>{Array.from(name)[0] || '?'}</span>;
}

type Props = {selection: ViewerSelection; data: Facets | null; disabled: boolean;
  onChange: (patch: Partial<ViewerSelection>) => void};

export function Members({selection, data, disabled, onChange}: Props) {
  return <section className="members-panel" aria-label="成员浏览">
    <div className="reader-section-title"><h3>成员</h3><button type="button" disabled={disabled}
      onClick={() => onChange({members: []})}>清除成员</button></div>
    <p className="muted">可多选 · 按发言数排序</p>
    <div className="member-list">{data?.members.map(member => <button key={member.id} type="button"
      disabled={disabled} className="member-choice" aria-pressed={selection.members.includes(member.id)}
      aria-label={`筛选成员 ${member.display_name} #${member.id}，${member.count} 条`}
      onClick={() => onChange({members: toggle(selection.members, member.id)})}>
      <MemberAvatar url={member.avatar_url} name={member.display_name} />
      <span><strong>{member.display_name}</strong><small>#{member.id} · {member.count} 条</small>
        <meter aria-label={`${member.display_name}发言数量`} min={0} max={data.members[0]?.count || 1} value={member.count} /></span>
    </button>)}</div>
    {data?.members.length === 0 && <p className="muted">此范围尚无成员消息</p>}
  </section>;
}

export function HoursAndMedia({selection, data, disabled, onChange, messageType, onType, showHours}: Props & {
  messageType?: string; onType?: (type: string) => void; showHours: boolean;
}) {
  const hours = data?.hours ?? Array(24).fill(0) as number[];
  const maximum = Math.max(1, ...hours);
  return <section className="reader-toolbar" aria-label="阅读筛选">
    {showHours && <><div className="reader-section-title"><span>小时分布</span>
      <button type="button" disabled={disabled} onClick={() => onChange({hours: []})}>清除小时</button></div>
    <div className="hours-strip">{hours.map((count, hour) => <button key={hour} type="button" disabled={disabled}
      aria-pressed={selection.hours.includes(hour)} aria-label={`${hour} 时，${count} 条`}
      onClick={() => onChange({hours: toggle(selection.hours, hour)})}>
      <span className="hour-bar" style={{height: `${8 + 28 * count / maximum}px`}}>{count || ''}</span>
      <small>{String(hour).padStart(2, '0')}</small></button>)}</div></>}
    <div className="media-tabs">{[['', '全部'], ['photos', '图片'], ['video', '视频'], ['links', '链接']].map(([value, label]) =>
      <button key={value} type="button" disabled={disabled} aria-label={`媒体：${label}`}
        aria-pressed={selection.media === value && (!!value || !messageType)} onClick={() => onChange({media: value})}>{label}</button>)}
      <button type="button" disabled={disabled} aria-pressed={selection.hide_noise}
        onClick={() => onChange({hide_noise: !selection.hide_noise})}>隐藏噪声</button>
      <button type="button" disabled={disabled} aria-pressed={selection.hide_join_notices}
        title="只按完整入群通知内容过滤，不屏蔽发送者。隐藏噪声也包含入群通知。"
        onClick={() => onChange({hide_join_notices: !selection.hide_join_notices})}>隐藏入群通知</button>
      {onType && <select disabled={disabled} aria-label="更多消息类型" value={['text', 'file', 'system'].includes(messageType ?? '') ? messageType : ''}
        onChange={event => onType(event.target.value)}><option value="">更多类型</option><option value="text">文本</option>
        <option value="file">文件</option><option value="system">系统</option></select>}
    </div>
  </section>;
}

export function FilterSummary({filters, options, data}: {filters: Filters; options: FilterOptions; data: Facets | null}) {
  const s = selectionOf(filters);
  const members = s.members.map(id => `${data?.members.find(m => m.id === id)?.display_name ?? '成员'} #${id}`);
  const base = [options.accounts.find(a => String(a.id) === filters.accountId)?.display_name,
    options.groups.find(g => String(g.id) === filters.groupId)?.name,
    filters.dateFrom && `从 ${filters.dateFrom}`, filters.dateTo && `至 ${filters.dateTo}`,
    filters.user && `用户：${filters.user}`, filters.keyword && `关键词：${filters.keyword}`,
    filters.messageType && `标准类型：${filters.messageType}`, filters.hasAttachment && `有附件：${filters.hasAttachment}`].filter(Boolean);
  return <div className="filter-summary" aria-label="已应用筛选">
    <span>{base.join(' · ') || '全部账号 / 群聊 / 日期'}</span>
    <span>成员：{members.join('、') || '全部'} · 小时：{s.hours.join('、') || '全部'} · 媒体：{s.media || '全部'}</span>
    <span>已隐藏噪声：{data ? data.hidden_noise : '—'}</span>
    <span>入群通知：{s.hide_join_notices || s.hide_noise ? '已隐藏（按内容）' : '显示'}</span>
  </div>;
}
