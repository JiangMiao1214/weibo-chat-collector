import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { safeHttpUrl } from './presentation';
import {ArchiveMaintenance} from './ArchiveMaintenance';

type ArchiveItem = {id: number; kind: string; message_id: number; source_url?: string | null; failure_url?: string | null};

export type ArchiveStatus = {account_id: number; group_id: number; enabled: boolean; paused: boolean;
  reason?: string | null; checked?: number; invalid?: number; repaired?: number; counts: Record<string, number>; active_jobs?: ArchiveItem[];
  errors: (ArchiveItem & {status?: string; error: string})[]};
const labels: Record<string, string> = {queued: '等待下载', downloading: '正在下载', stored: '文件已保存',
  failed: '下载失败', blocked: '等待处理', skipped: '已跳过'};

function AccessLink({url}: {url?: string | null}) {
  const safe = safeHttpUrl(url || '');
  return safe ? <> · <a href={safe} target="_blank" rel="noopener noreferrer" style={{overflowWrap: 'anywhere'}}>{safe}</a></> : null;
}

async function readStatus(response: Response, accountId: number, groupId: number) {
  if (!response.ok) throw new Error(`归档操作失败（HTTP ${response.status}）`);
  const result: ArchiveStatus = await response.json();
  if (result.account_id !== accountId || result.group_id !== groupId) throw new Error('归档响应范围不匹配');
  return result;
}

function repairNotice(result: ArchiveStatus) {
  return `已检查 ${result.checked || 0} 项，重新排队 ${result.repaired || 0} 项；暂停状态保持不变。`;
}

function requestUrl(accountId: number, groupId: number, action?: string, jobId?: number) {
  if (jobId) return `/api/archives/${jobId}/${action}`;
  if (action === 'repair') return '/api/archives/repair';
  return action ? '/api/archives/control' : `/api/archives/status?account_id=${accountId}&group_id=${groupId}`;
}

function ArchiveDialog({accountId, groupId, close}: {accountId: number; groupId: number; close: () => void}) {
  const [data, setData] = useState<ArchiveStatus | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const controller = useRef<AbortController | null>(null);
  const alive = useRef(true);
  const acting = useRef(false);
  const pending = useRef(false);
  async function request(action?: string, jobId?: number, text?: string) {
    if ((acting.current || pending.current) && !action) return;
    controller.current?.abort();
    const current = new AbortController(); controller.current = current;
    pending.current = true;
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true; current.abort();
      if (alive.current && controller.current === current) {
        setError('归档请求超时，请刷新状态'); setBusy(false); acting.current = false; pending.current = false;
      }
    }, action === 'repair' ? 300000 : 15000);
    if (action) {acting.current = true; setBusy(true);}
    try {
      const url = requestUrl(accountId, groupId, action, jobId);
      const response = await fetch(url,
        {signal: current.signal, cache: 'no-store', method: action ? 'POST' : 'GET',
          headers: action ? {'Content-Type': 'application/json'} : undefined,
          body: action ? JSON.stringify({account_id: accountId, group_id: groupId, action, text}) : undefined});
      const result = await readStatus(response, accountId, groupId);
      if (alive.current && !current.signal.aborted) {
        setData(result); setError('');
        if (action === 'repair') setNotice(repairNotice(result));
      }
    } catch (reason) {
      if (alive.current && (timedOut || !current.signal.aborted)) setError(timedOut ? '归档请求超时，请刷新状态' : String(reason));
    } finally {
      clearTimeout(timer);
      if (controller.current === current) {
        pending.current = false; acting.current = false; if (alive.current) setBusy(false);
      }
    }
  }
  useEffect(() => {
    alive.current = true;
    if (dialog.current?.showModal) dialog.current.showModal();
    else if (dialog.current) dialog.current.open = true;
    void request();
    const timer = setInterval(() => void request(), 2000);
    return () => {alive.current = false; controller.current?.abort(); clearInterval(timer); dialog.current?.close?.();};
  }, []);
  function dismiss() {dialog.current?.close?.(); close();}
  return createPortal(<dialog ref={dialog} className="quote-context-dialog archive-dialog" aria-label="本群本地归档"
    onCancel={event => {event.preventDefault(); dismiss();}}>
    <header className="quote-context-heading"><h2>本地归档</h2><button type="button" onClick={dismiss}>关闭归档</button></header>
    <p>保存本群全部历史和后续新消息中的图片、视频、文件及链接正文，不受日期、成员或类型筛选限制。</p>
    <p>原文件保存在本机，按内容去重。微博原文保存为正文快照，并下载可识别的关联媒体。</p>
    <p className="muted">需保持后端运行；登录失效会暂停所属账号。网页正文保存成功不代表所有关联媒体都已完成。</p>
    <p className="muted">明确无权限或已删除的微博会自动跳过；已跳过项不会随恢复操作重试。手动跳过仅用于你已确认无法访问的链接。</p>
    <p className="muted">最多同时下载 3 项，每 2 秒刷新状态。暂停时不启动新下载；小文件可能在两次刷新之间完成。</p>
    {error && <p role="alert">{error}</p>}
    {!data && !error && <p role="status">正在读取归档状态…</p>}
    {data && <>
      <p role="status">{!data.enabled ? '本群尚未开启归档' : data.paused ? '归档已暂停' : '归档已开启 · 自动检查新消息'}{data.reason && `：${data.reason}`}</p>
      <div className="attachment-summary">{Object.entries(labels).map(([key, label]) => <span key={key}>{label} {data.counts[key] || 0}{key === 'downloading' ? ' / 3' : ''}</span>)}</div>
      {!!data.active_jobs?.length && <ul aria-label="正在下载的内容">{data.active_jobs.map(item =>
        <li key={item.id}>消息 #{item.message_id} · {item.kind}<AccessLink url={item.source_url} /></li>)}</ul>}
      <div className="button-row">
        {!data.enabled ? <button disabled={busy} type="button" onClick={() => void request('start')}>开启本群归档并补下载历史</button>
          : <><button disabled={busy || data.paused} type="button" onClick={() => void request('pause')}>暂停归档</button>
            <button disabled={busy} type="button" onClick={() => void request('resume')}>恢复归档并重试失败项</button></>}
      </div>
      <button type="button" disabled={busy} onClick={() => void request('repair')}>检查并补下载缺失或损坏文件</button>
      {notice && <p role="status">{notice}</p>}
      {data.errors.length > 0 && <ul>{data.errors.map(item => <li key={item.id}>消息 #{item.message_id} · {item.kind}：{item.error}
        <AccessLink url={item.source_url} />
        {item.failure_url && <span> · 实际失败地址<AccessLink url={item.failure_url} /></span>}
        {item.failure_url && item.error.includes('Fake-IP') && <button type="button" disabled={busy} onClick={() => {
          const host = new URL(item.failure_url!).hostname;
          if (window.confirm(`允许账号通过 HTTPS 代理访问 ${host}？仍校验证书和地址，设置后需手动重试。`)) void request('trust-host', item.id);
        }}>设置此域名 HTTPS 兼容</button>}
        {['failed', 'blocked', 'skipped'].includes(item.status || '') &&
          <button type="button" disabled={busy} onClick={() => void request('retry', item.id)}>重新下载此项</button>}
        {item.kind === 'link' && ['failed', 'blocked', 'skipped'].includes(item.status || '') &&
          <button type="button" disabled={busy} onClick={() => {
            const text = window.prompt('粘贴你已打开网页的正文（至少 40 字、最多 2 MiB）。只保存文字，不包含图片或视频。');
            if (text) void request('snapshot', item.id, text);
          }}>导入网页正文</button>}
        {item.kind === 'link' && ['failed', 'blocked'].includes(item.status || '') &&
          <button type="button" disabled={busy} onClick={() => void request('skip', item.id)}>跳过此链接</button>}
      </li>)}</ul>}
    </>}
    <button type="button" disabled={busy} onClick={() => void request()}>刷新归档状态</button>
    <ArchiveMaintenance />
    <p className="muted">查看归档后的消息或附件，请刷新消息列表。未保存的内容仍需联网，失效地址无法保证补回。</p>
  </dialog>, document.body);
}

export function ArchivePanel({accountId, groupId, disabled}: {accountId: string; groupId: string; disabled: boolean}) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  return <><button ref={trigger} type="button" disabled={disabled} onClick={() => setOpen(true)}>本地归档</button>
    {open && <ArchiveDialog accountId={Number(accountId)} groupId={Number(groupId)}
      close={() => {setOpen(false); trigger.current?.focus({preventScroll: true});}} />}</>;
}
