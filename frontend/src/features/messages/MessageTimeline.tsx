import React, { useEffect, useMemo, useRef, useState } from "react";
import { ArrowUpRight, FileText, Image, Link as LinkIcon, CornerUpLeft } from "lucide-react";
import type { Message } from "../../shared/contracts";
import { formatMessageType, groupMessagesByDate } from "./model";
import { chronological, bodyLinks, indexQuoteCandidates, isContinuation, resolveQuote, splitQuote } from "./presentation";
import { QuoteLookup } from "./QuoteLookup";
import { Highlight, RichLinks } from './RichLinks';
import { focusMessageInList } from './scrollMessage';
import {ArchiveGallery} from './ArchiveGallery';

type Props = {
  query?: string;
  onContext?: (message: Message) => void;
  messages: Message[];
  selectedMessageId: number | null;
  onSelect: (id: number) => void;
};

function MessageContent({ message, candidates, onJump, query }: {
  query: string;
  message: Message; candidates: Map<string, Message[]>; onJump: (id: number) => void;
}) {
  const [referenceMethod,setReferenceMethod] = useState(message.reference_method);
  useEffect(() => setReferenceMethod(message.reference_method), [message.reference_method]);
  const referenceChanged = (method: "full_text" | "manual" | "source_id") => setReferenceMethod(method === "full_text" ? null : method);
  const parsed = splitQuote(message.content_text);
  const targets = parsed && !referenceMethod ? resolveQuote(message, parsed.quote, candidates) : [];
  const reply = parsed ? parsed.reply : message.content_text;
  const links = bodyLinks(reply);
  return <>
    {parsed && <blockquote className="chat-quote">
      <span className="chat-caption"><CornerUpLeft size={13} aria-hidden="true" />引用内容</span>
      <p><Highlight text={parsed.quote} query={query} /></p>
      <span className="chat-caption">{referenceMethod ? "已保存永久引用关系" : "文字匹配 · 仅当前列表"}</span>
      {targets.length === 1 ? <button type="button" className="chat-quote-jump"
        onClick={() => onJump(targets[0].id)} aria-label={`查看匹配消息 #${targets[0].id}`}>
        查看匹配消息 · {targets[0].sender_name} · {targets[0].sent_at}
      </button> : !referenceMethod && <span className="chat-caption">{targets.length > 1
        ? "有多个文字匹配项，无法确定原消息" : "当前列表未找到匹配项，可加载更早消息或调整筛选"}</span>}
      {targets.length !== 1 && <QuoteLookup source={message} onReferenceChange={referenceChanged} />}
    </blockquote>}
    {!parsed && referenceMethod && <QuoteLookup source={message} onReferenceChange={referenceChanged} />}
    {reply && <p className="chat-text"><Highlight text={reply} query={query} /></p>}
    {!message.content_text && <p className="chat-text muted">(无文本内容)</p>}
    <RichLinks message={message} cards={message.link_previews} urls={links} />
  </>;
}

function MessageCard({ message, continuation, selected, candidates, onSelect, onJump, selectRef, onContext, query }: {
  onContext?: (message: Message) => void; query: string;
  message: Message; continuation: boolean; selected: boolean; candidates: Map<string, Message[]>;
  onSelect: (id: number) => void; onJump: (id: number) => void;
  selectRef: (node: HTMLButtonElement | null) => void;
}) {
  const AttachmentIcon = message.message_type === "image" ? Image : FileText;
  const hue = (message.account_id * 47 + (message.user_id || 0) * 67) % 360;
  return <article className={`chat-message${selected ? " is-selected" : ""}`}
    aria-label={`消息 #${message.id}`} data-continuation={continuation}
    onClick={(event) => {
      if ((event.target as Element).closest("a, button") || window.getSelection()?.toString()) return;
      onSelect(message.id);
    }}>
    <span className="chat-avatar" role="img" aria-label={`${message.sender_name}的文字头像`}
      style={{ backgroundColor: `hsl(${hue} 35% 90%)`, color: `hsl(${hue} 35% 28%)` }}>
      {Array.from(message.sender_name.trim())[0] || "?"}
    </span>
    <div className="chat-message-main">
      <header className="chat-message-header">
        <strong>{message.sender_name}</strong>
        <time dateTime={message.sent_at.replace(" ", "T")}>{message.sent_at.slice(11)}</time>
        <button ref={selectRef} type="button" className="chat-detail-button" aria-label={`查看消息 #${message.id}`}
          aria-pressed={selected} onClick={() => onSelect(message.id)}>详情 <span>#{message.id}</span></button>
        {onContext && <button type="button" className="chat-detail-button" aria-label={`上下文 #${message.id}`}
          onClick={() => onContext(message)}>上下文</button>}
      </header>
      <div className="chat-bubble">
        <MessageContent message={message} candidates={candidates} onJump={onJump} query={query} />
        <ArchiveGallery message={message} />
        {message.attachment_count > 0 && <button type="button" className="chat-attachment-button"
          onClick={() => onSelect(message.id)} aria-label={`查看 ${message.attachment_count} 个附件`}>
          <AttachmentIcon size={18} aria-hidden="true" />
          <span>附件 {message.attachment_count}<small>查看附件信息与原地址</small></span>
          <ArrowUpRight size={16} aria-hidden="true" />
        </button>}
      </div>
      <footer className="chat-message-context"><span>{message.account_name}</span><span>{message.group_name}</span>
        <span>{formatMessageType(message.message_type)}</span></footer>
    </div>
  </article>;
}

export function MessageTimeline({ messages, selectedMessageId, onSelect, onContext, query = '' }: Props) {
  const groups = useMemo(() => groupMessagesByDate(chronological(messages)), [messages]);
  const candidates = useMemo(() => indexQuoteCandidates(messages), [messages]);
  const buttons = useRef(new Map<number, HTMLButtonElement>());
  function jumpTo(id: number) {
    const button = buttons.current.get(id);
    if (!button) return;
    onSelect(id);
    focusMessageInList(button);
  }
  return <div className="chat-timeline" aria-label="消息时间线">
    {groups.map((group) => <section className="date-group" key={group.date}>
      <h3>{group.date}</h3>
      {group.items.map((message, index) => <MessageCard key={message.id} message={message}
        continuation={isContinuation(group.items[index - 1], message)} selected={selectedMessageId === message.id}
        candidates={candidates} onSelect={onSelect} onJump={jumpTo} onContext={onContext} query={query}
        selectRef={(node) => { if (node) buttons.current.set(message.id, node); else buttons.current.delete(message.id); }} />)}
    </section>)}
  </div>;
}
