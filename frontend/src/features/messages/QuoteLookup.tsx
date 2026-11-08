import {ContinuousReader} from "./ContinuousReader";
import {RecoverContext} from "./RecoverContext";
import {ReferenceEditor} from "./ReferenceEditor";
import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { Message } from "../../shared/contracts";
import { AttachmentList } from "./AttachmentList";
import { chronological } from "./presentation";
import { type QuoteResult, useQuoteLookup } from "./useQuoteLookup";
import "./quote-context.css";

function ContextMessages({ data }: { data: QuoteResult }) {
  const targetRef = useRef<HTMLElement>(null);
  useEffect(() => {
    targetRef.current?.focus({ preventScroll: true });
    targetRef.current?.scrollIntoView?.({ block: "center" });
  }, [data]);
  if (!data.target) return <p role="status">{{
    ambiguous: "存在多个匹配项，无法确定原消息",
    not_found: "未找到匹配项：原消息可能尚未采集、已被删除或文本不一致",
    not_quote: "此消息没有可识别的引用格式",
    matched: "定位结果不完整",
  }[data.status]}</p>;
  return <>
    <p className="quote-context-note">{{full_text:"完整文字唯一匹配，不是源 ID 确认。",manual:"人工确认的永久关联。",source_id:"按导入数据明确提供的目标源 ID 定位。"}[data.match_method]}展示目标及前后各最多 5 条消息，按旧到新排列。</p>
    <div className="quote-context-list">
      {data.has_older && <p className="muted">上方还有更早消息，此窗口仅展示相邻上下文</p>}
      {chronological(data.items).map((item) => {
        const isTarget = item.id === data.target?.id;
        return <article key={item.id} ref={isTarget ? targetRef : undefined} tabIndex={isTarget ? -1 : undefined}
          className={isTarget ? "quote-context-message is-target" : "quote-context-message"}
          aria-label={`${isTarget ? "匹配消息" : "上下文消息"} #${item.id}`}>
          <header><strong>{item.sender_name} {isTarget ? "· 匹配消息" : ""}</strong><span>#{item.id} · {item.sent_at}</span></header>
          <p>{item.content_text || "(无文本内容)"}</p>
          {isTarget && data.target && <AttachmentList attachments={data.target.attachments} message={data.target} />}
          <small>{item.account_name} · {item.group_name} · 附件 {item.attachment_count}</small>
        </article>;
      })}
      {data.has_newer && <p className="muted">下方还有更新消息，此窗口仅展示相邻上下文</p>}
    </div>
  </>;
}

function QuoteDialog({ source, onClose, onReferenceChange }: { source: Message; onClose: () => void; onReferenceChange?: (method: QuoteResult["match_method"]) => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [continuous,setContinuous]=useState(false);
  const [attempt, setAttempt] = useState(0);
  const { data, error } = useQuoteLookup(source, attempt);
  function closeDialog() {
    // Remove native modal inertness before restoring focus to the list trigger.
    dialogRef.current?.close?.();
    onClose();
  }
  useEffect(() => {
    const dialog = dialogRef.current!;
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.open = true; // DOM test environments without native dialog support.
    return () => { dialog.close?.(); };
  }, []);
  return createPortal(<dialog ref={dialogRef} className="quote-context-dialog" aria-label="引用消息上下文"
    onClick={(event) => event.stopPropagation()} onCancel={(event) => { event.preventDefault(); closeDialog(); }}>
    <header className="quote-context-heading"><h2>引用消息上下文</h2>
      <button className="secondary-button" type="button" onClick={closeDialog}>返回原列表</button></header>
    <p className="quote-context-note">{source.account_name} / {source.group_name} · {source.is_deleted ? "回收站" : "未删除消息"} · 来自消息 #{source.id}</p>
    <p className="quote-context-note">查找范围：同账号同群的全部已入库消息；忽略日期、关键词、用户和类型筛选，不改变原列表或批量删除范围。</p>
    {error ? <div><p role="alert">{error}</p><button type="button" className="secondary-button"
      onClick={() => setAttempt((value) => value + 1)}>重试定位</button></div>
      : data ? <>{continuous && data.target ? <ContinuousReader source={data.target} initial={data}/> : <ContextMessages data={data} />}
        {data.target&&<button type="button" onClick={()=>setContinuous(value=>!value)}>{continuous?"返回定位摘要":"连续阅读与逐级引用"}</button>}
        {data.status==="not_found"&&<RecoverContext source={source}/>}<ReferenceEditor source={source} method={data.match_method} onSaved={method => {setAttempt(value => value + 1);onReferenceChange?.(method);}} /></> : <p role="status">正在定位引用…</p>}
  </dialog>, document.body);
}

export function QuoteLookup({ source, label = "查看引用原文", onReferenceChange }: { source: Message; label?: string; onReferenceChange?: (method: QuoteResult["match_method"]) => void }) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  function close() {
    setOpen(false);
    trigger.current?.focus({ preventScroll: true });
  }
  return <>
    <button ref={trigger} type="button" className="chat-quote-jump quote-lookup-trigger"
      onClick={() => setOpen(true)}>{label}</button>
    {open && <QuoteDialog source={source} onClose={close} onReferenceChange={onReferenceChange} />}
  </>;
}
