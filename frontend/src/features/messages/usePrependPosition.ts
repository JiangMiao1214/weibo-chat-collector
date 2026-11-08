import {useLayoutEffect, useRef} from 'react';
import type {Message} from '../../shared/contracts';

type Anchor = {label:string; offset:number};
function visibleAnchor(box:HTMLDivElement):Anchor|null {
  const viewport=box.getBoundingClientRect();
  const card=[...box.querySelectorAll<HTMLElement>('.chat-message')].find(el=>{
    const rect=el.getBoundingClientRect();
    return rect.bottom>viewport.top && rect.top<viewport.bottom;
  });
  return card ? {label:card.getAttribute('aria-label')!,offset:card.getBoundingClientRect().top-viewport.top} : null;
}

export function usePrependPosition(messages:Message[], scope:string, loading:boolean, loadingMore:boolean, loadMore:()=>void) {
  const list=useRef<HTMLDivElement>(null);
  const pending=useRef<{messages:Message[];scope:string;anchor:Anchor|null}|null>(null);
  function loadEarlier() {
    if(loading || loadingMore || pending.current)return;
    pending.current={messages,scope,anchor:list.current ? visibleAnchor(list.current) : null};
    loadMore();
  }
  function onScroll() {
    // The user can continue reading during the request; preserve their latest position.
    if(pending.current && list.current)pending.current.anchor=visibleAnchor(list.current);
  }
  useLayoutEffect(()=>{
    const saved=pending.current;
    if(!saved)return;
    if(loading || saved.scope!==scope){pending.current=null;return;}
    if(messages!==saved.messages){
      pending.current=null;
      const appended=messages.length>saved.messages.length &&
        saved.messages.every((item,index)=>messages[index].id===item.id);
      const box=list.current;
      if(!appended || !box || !saved.anchor)return;
      const card=[...box.querySelectorAll<HTMLElement>('.chat-message')]
        .find(el=>el.getAttribute('aria-label')===saved.anchor!.label);
      if(card)box.scrollTop+=card.getBoundingClientRect().top-box.getBoundingClientRect().top-saved.anchor.offset;
    } else if(!loadingMore)pending.current=null; // Failed/empty request: no stale restore on a later refresh.
  },[messages,scope,loading,loadingMore]);
  return {list,loadEarlier,onScroll};
}
