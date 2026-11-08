import React from 'react';
import type { Attachment, Message } from '../../shared/contracts';
import { archiveFor, archiveHref, archiveLabel } from './archiveModel';
import { safeHttpUrl } from './presentation';
import {ArchiveGallery} from './ArchiveGallery';

function uniqueAttachments(items: Attachment[]) {
  const seen = new Set<string>();
  return items.filter(item => {
    const source = safeHttpUrl(item.source_url) || item.local_path;
    const key = source ? JSON.stringify([item.attachment_type, source]) : `id:${item.id}`;
    if (seen.has(key)) return false;
    seen.add(key); return true;
  });
}

export function AttachmentList({attachments, message}: {attachments: Attachment[];
  message: Pick<Message, 'account_id' | 'group_id' | 'is_deleted' | 'archives'>}) {
  return <div className="attachment-list"><ArchiveGallery message={message} />{uniqueAttachments(attachments).map(attachment => {
    const source = safeHttpUrl(attachment.source_url);
    const isImage = attachment.attachment_type === 'image';
    const supported = source && (/^https:\/\/(?:wx|ww|ws|tvax|tva)\d+\.sinaimg\.(?:cn|com)\//.test(source) ||
      source.startsWith('https://upload.api.weibo.com/2/mss/msget?'));
    const archived = archiveFor(message, attachment.source_url || '', attachment.attachment_type);
    const href = archived?.status === 'stored' ? archiveHref(archived, message) : isImage && supported
      ? `/api/attachments/${attachment.id}/image?account_id=${message.account_id}&group_id=${message.group_id}&deleted_only=${!!message.is_deleted}`
      : source;
    const title = attachment.file_name || attachment.title;
    const label = title && !safeHttpUrl(title) ? title : isImage ? '查看图片' : source || attachment.attachment_type;
    return <article className="attachment-item" key={attachment.id}>
      <div>{href ? <a href={href} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">{label}</a>
        : <strong>{label}</strong>}
        <span>{archiveLabel(archived, attachment.download_status)}</span>
      </div>
    </article>;
  })}</div>;
}
