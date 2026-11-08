import {describe, expect, it, vi} from 'vitest';
import {focusMessageInList} from './scrollMessage';

describe('消息定位滚动边界', () => {
  it.each([100, 700])('高度 %i 的消息仅调整自身列表，保留外层滚动位置', height => {
    const outer = document.createElement('section');
    outer.innerHTML = '<div class="message-list"><article><button>详情</button></article></div>';
    document.body.append(outer);
    const list = outer.querySelector<HTMLElement>('.message-list')!;
    const article = outer.querySelector('article')!;
    const button = outer.querySelector('button')!;
    outer.scrollTop = 17;
    list.scrollTop = 100;
    Object.defineProperty(list, 'clientHeight', {value: 500});
    vi.spyOn(list, 'getBoundingClientRect').mockReturnValue({top: 200} as DOMRect);
    vi.spyOn(article, 'getBoundingClientRect').mockReturnValue({top: 850, height} as DOMRect);
    const focus = vi.spyOn(button, 'focus');
    focusMessageInList(button);
    expect(list.scrollTop).toBe(height > 500 ? 750 : 550);
    expect(outer.scrollTop).toBe(17);
    expect(focus).toHaveBeenCalledWith({preventScroll: true});
    expect(document.activeElement).toBe(button);
    outer.remove();
  });
});
