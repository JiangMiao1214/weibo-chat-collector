import React, {useEffect, useRef, useState} from 'react';
import './archive-gallery.css';

type Preview = {count: number; bytes: number; token: string; removed: number};

export function ArchiveMaintenance() {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [preview, setPreview] = useState<Preview | null>(null);
  const alive = useRef(true);
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  async function act(action: 'backup' | 'cleanup' | 'restore', body?: File | object) {
    setBusy(true); setError(''); setMessage('');
    try {
      const response = await fetch(`/api/archive-maintenance/${action}`, {method: 'POST',
        headers: {'Content-Type': body instanceof File ? 'application/zip' : 'application/json'},
        body: body instanceof File ? body : JSON.stringify(body || {})});
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || '维护操作失败');
      if (!alive.current) return;
      if (action === 'cleanup') {
        setPreview(result.removed ? null : result);
        setMessage(result.removed ? `已清理 ${result.removed} 个无引用文件` : `可清理 ${result.count} 个文件，约 ${(result.bytes / 1024**2).toFixed(2)} MiB`);
      } else if (action === 'backup') setMessage(`备份已校验并保存到：${result.path}`);
      else setMessage(`已恢复到新目录：${result.destination}。当前库未替换，切换方法见归档说明。`);
    } catch (reason) {if (alive.current) setError(String(reason));}
    finally {if (alive.current) setBusy(false);}
  }
  return <details className="archive-maintenance"><summary>备份、恢复与磁盘维护（全部账号）</summary>
    <p>备份包含全部账号的数据库及附件，不含登录 Cookie。创建备份或清理前，请暂停全部群归档并停止采集，等待当前下载结束。</p>
    <button type="button" disabled={busy} onClick={() => void act('backup')}>创建完整备份</button>
    <button type="button" disabled={busy} onClick={() => void act('cleanup')}>预览可清理文件</button>
    {!!preview?.count && <button type="button" disabled={busy} onClick={() => {
      if (window.confirm(`将永久删除预览中的 ${preview.count} 个无引用文件。请确认已有备份。`)) void act('cleanup', {token: preview.token});
    }}>确认清理 {preview.count} 个文件</button>}
    <p>恢复会校验备份并生成新的数据库与附件目录，保留当前库。恢复副本中的任务默认暂停。</p>
    <label>恢复备份 ZIP<input type="file" accept=".zip,application/zip" disabled={busy} onChange={event => {
      const file = event.target.files?.[0];
      if (file) void act('restore', file);
      event.target.value = '';
    }} /></label>
    {busy && <p role="status">正在处理，请保持页面打开。大备份可能需要几分钟。</p>}
    {message && <p role="status">{message}</p>}{error && <p role="alert">{error}</p>}
  </details>;
}
