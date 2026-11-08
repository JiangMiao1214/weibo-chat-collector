import type { Archive, Message } from '../../shared/contracts';
import { safeHttpUrl } from './presentation';

export type ArchiveMessage = Pick<Message, 'account_id' | 'group_id' | 'is_deleted' | 'archives'>;

export function archiveFor(message: ArchiveMessage | undefined, url: string, kind?: string) {
  return message?.archives?.find(item => safeHttpUrl(item.source_url) === safeHttpUrl(url) && (!kind || item.kind === kind));
}

export function archiveHref(item: Archive, message: ArchiveMessage) {
  return `/api/archives/${item.id}/content?account_id=${message.account_id}&group_id=${message.group_id}&deleted_only=${!!message.is_deleted}`;
}

export function archiveLabel(item: Archive | undefined, fallback = 'pending') {
  if (item?.status === 'stored' && item.pending_resources) return `正文已保存 · ${item.pending_resources} 个关联媒体未完成`;
  const labels: Record<string, string> = {stored: '已保存本地', pending: '未保存本地', queued: '等待下载',
    downloading: '正在下载', failed: '下载失败', blocked: '等待处理', skipped: '已跳过 · 未保存本地'};
  return labels[item?.status || fallback] || '未保存本地';
}
