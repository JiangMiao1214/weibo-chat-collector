/** Move only the message scroller, never the clipped workspace or page. */
export function focusMessageInList(button: HTMLButtonElement | null | undefined) {
  if (!button) return;
  button.focus({preventScroll: true});
  const list = button.closest<HTMLElement>('.message-list');
  const article = button.closest('article');
  if (!list || !article) return;
  const target = article.getBoundingClientRect();
  const viewport = list.getBoundingClientRect();
  // Tall messages align at their start so the sender and quote remain visible.
  const offset = Math.max(0, (list.clientHeight - target.height) / 2);
  list.scrollTop += target.top - viewport.top - list.clientTop - offset;
}
