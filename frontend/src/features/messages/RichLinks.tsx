import React from 'react';
import type { LinkPreview } from '../../shared/contracts';
import { archiveFor, archiveHref, archiveLabel, type ArchiveMessage } from './archiveModel';
import { safeHttpUrl } from './presentation';

export function RichLinks({cards = [], urls = [], message}: {cards?: LinkPreview[]; urls?: string[]; message?: ArchiveMessage}) {
  const links = [...new Set([...urls, ...cards.map(card => card.url)])].filter(url => safeHttpUrl(url));
  return <div className="message-links">{links.map(url => {
    const archived = archiveFor(message, url, 'link');
    return <div key={url}><a className="chat-link-card"
      href={archived?.status === 'stored' && message ? archiveHref(archived, message) : url}
      target="_blank" rel="noopener noreferrer">{url}</a>
      {archived && <small>{archiveLabel(archived)}</small>}</div>;
  })}</div>;
}

export function Highlight({text, query}: {text: string; query: string}) {
  if (!query) return <>{text}</>;
  const nodes: React.ReactNode[] = [];
  let start = 0;
  let index = text.toLowerCase().indexOf(query.toLowerCase());
  while (index !== -1) {
    nodes.push(text.slice(start, index), <mark key={index}>{text.slice(index, index + query.length)}</mark>);
    start = index + query.length;
    index = text.toLowerCase().indexOf(query.toLowerCase(), start);
  }
  return <>{nodes}{text.slice(start)}</>;
}
