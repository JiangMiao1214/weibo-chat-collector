import React, {useEffect, useRef, useState} from 'react';
import {createPortal} from 'react-dom';
import {archiveHref, type ArchiveMessage} from './archiveModel';
import './archive-gallery.css';

type Picture = {id: number; href: string};

function Lightbox({pictures, initial, close}: {pictures: Picture[]; initial: number; close: () => void}) {
  const [index, setIndex] = useState(initial);
  const [zoom, setZoom] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const picture = pictures[index];
  useEffect(() => {
    if (dialog.current?.showModal) dialog.current.showModal();
    else if (dialog.current) dialog.current.open = true;
    return () => dialog.current?.close?.();
  }, []);
  function dismiss() {dialog.current?.close?.(); close();}
  function move(delta: number) {setIndex(value => (value + delta + pictures.length) % pictures.length); setZoom(false);}
  return createPortal(<dialog ref={dialog} className="archive-lightbox" aria-label="本地图片浏览"
    onCancel={event => {event.preventDefault(); dismiss();}}
    onKeyDown={event => {if (event.key === 'ArrowLeft') move(-1); if (event.key === 'ArrowRight') move(1);}}>
    <header><button type="button" onClick={dismiss}>关闭图片</button><span>{index + 1} / {pictures.length}</span>
      <button type="button" onClick={() => setZoom(value => !value)}>{zoom ? '适应窗口' : '原尺寸'}</button>
      <a href={picture.href} target="_blank" rel="noopener noreferrer">打开原图</a></header>
    <div className={`archive-image-stage${zoom ? ' original-size' : ''}`}><img src={picture.href} alt={`本地图片 ${index+1}`} /></div>
    <footer><button type="button" disabled={pictures.length < 2} onClick={() => move(-1)}>上一张</button>
      <button type="button" disabled={pictures.length < 2} onClick={() => move(1)}>下一张</button></footer>
  </dialog>, document.body);
}

export function ArchiveGallery({message}: {message: ArchiveMessage}) {
  const pictures = (message.archives || []).filter(item => item.kind === 'image' && item.status === 'stored')
    .map(item => ({id: item.id, href: archiveHref(item, message)}));
  const [index, setIndex] = useState<number | null>(null);
  const trigger = useRef<HTMLButtonElement | null>(null);
  const key = pictures.map(item => item.href).join('|');
  useEffect(() => setIndex(null), [key]);
  return <div className="archive-gallery">{pictures.map((picture, position) =>
    <button key={picture.id} type="button" aria-label="放大图片" onClick={event => {trigger.current = event.currentTarget; setIndex(position);}}>
      <img loading="lazy" src={picture.href.replace('/content?', '/thumbnail?')} alt="已归档图片缩略图"
        onError={event => {event.currentTarget.alt = '缩略图不可用，点击查看原图';}} />
    </button>)}
    {index !== null && pictures[index] && <Lightbox key={key} pictures={pictures} initial={index}
      close={() => {setIndex(null); trigger.current?.focus({preventScroll: true});}} />}
  </div>;
}
