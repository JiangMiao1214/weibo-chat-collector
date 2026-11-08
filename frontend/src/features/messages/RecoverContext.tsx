import React, {useState} from 'react';
import type {Message} from '../../shared/contracts';
import {fetchJson} from '../../shared/http';
import {CollectionDateTime} from '../collection/CollectionDateTime';
import './recover-context.css';

function rangeError(start: string, finish: string, end: string) {
  if (!start || !finish) return '请选择完整的补采集开始和结束时间';
  // Compare local calendar values without applying the browser's timezone/DST.
  const duration = Date.parse(`${finish}:00Z`) - Date.parse(`${start}:00Z`);
  if (!(duration > 0 && duration <= 7 * 86400000)) return '补采集范围必须大于零且不超过 7 天';
  if (finish > end) return '补采集结束时间不能晚于引用消息';
  return '';
}

export function RecoverContext({source}: {source: Message}) {
  const end = source.sent_at.replace(' ', 'T').slice(0, 16);
  const [start, setStart] = useState(end.slice(0, 10) + 'T00:00');
  const [finish, setFinish] = useState(end);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const error = rangeError(start, finish, end);
  async function submit() {
    if (!confirm || busy || error) return;
    setBusy(true);
    try {
      const result = await fetchJson<{id: number; message: string}>(`/api/messages/${source.id}/recover-context`, '创建补采集任务', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({account_id: source.account_id, group_id: source.group_id,
          range_start: start, range_end: finish, confirmed: true}),
      });
      setNotice(`任务 #${result.id}：${result.message}`);
      setConfirm(false);
    } catch (e) { setNotice(String(e)); }
    finally { setBusy(false); }
  }
  if (source.is_deleted) return null;
  return <details className="recover-context"><summary>原文未在本地找到：创建有界补采集任务</summary>
    <p>已查询本账号、本群全部未删除消息。无法据此断定原文尚未采集；也可能是文本变化、删除或权限限制。最多选择 7 天、每次运行 20 页；任务排队后还需在“采集监控”确认启动。</p>
    <div className="recovery-time-range">
      <CollectionDateTime label="补采集开始" value={start} max={end}
        onChange={value => {setStart(value); setConfirm(false);}}/>
      <CollectionDateTime label="补采集结束" value={finish} max={end}
        onChange={value => {setFinish(value); setConfirm(false);}}/>
    </div>
    {error && <p role="alert">{error}</p>}
    <label className="recovery-confirm"><input type="checkbox" checked={confirm}
      onChange={event => setConfirm(event.target.checked)}/>我已核对账号、群和时间范围</label>
    <button className="secondary-button" type="button" disabled={!confirm || busy || !!error}
      onClick={() => void submit()}>创建补采集任务（等待启动确认）</button>
    <p role="status">{notice}</p>
  </details>;
}
