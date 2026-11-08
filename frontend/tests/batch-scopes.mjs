import assert from 'node:assert/strict';

export function batchResponse(req, res, url, state) {
  if (!state.batchChecks || !/^\/api\/messages\/(delete-preview|restore-preview|hard-delete-preview|soft-delete|restore|hard-delete)$/.test(url.pathname)) return false;
  let text = '';
  req.on('data', chunk => {text += chunk;});
  req.on('end', () => {
    const body = JSON.parse(text);
    res.setHeader('Content-Type', 'application/json');
    if (url.pathname.endsWith('-preview')) {
      state.onBatchPreview({body, finish: count => res.end(JSON.stringify({preview_count: count,
        attachment_record_count: count, filters: body.filters}))});
    } else {
      state.batchWrites.push({path: url.pathname, body});
      res.end(JSON.stringify({deleted_count: 7, restored_count: 7}));
    }
  });
  return true;
}

export async function checkBatchScopes(page, state) {
  state.batchChecks = true; state.batchWrites = [];
  const button = label => `xpath///button[normalize-space(.)='${label}']`;
  const select = label => `xpath///label[span[normalize-space(.)='${label}']]/select`;
  for (const [action, view, preview, confirm] of [
    ['soft-delete', '消息', '预览删除数量', '确认软删除'],
    ['restore', '回收站', '预览恢复数量', '确认恢复'],
    ['hard-delete', '回收站', '预览彻底删除', '确认彻底删除'],
  ]) {
    state.batchStep = action + ': enter view';
    await page.locator(button(view)).click();
    await page.select(select('账号'), '1');
    if (!await page.$eval('.reader-advanced', el => el.open)) await page.locator('.reader-advanced summary').click();
    const oldRequest = new Promise(resolve => {state.onBatchPreview = resolve;});
    state.batchStep = action + ': old preview';
    await page.locator(button(preview)).click();
    const old = await oldRequest;
    assert.equal(old.body.filters.account_id, 1);
    state.batchStep = action + ': change account';
    await page.select(select('账号'), '2');
    assert.equal(await page.$(button(confirm)), null);
    // Selecting a scope closes the advanced popover; reopen it as a user would.
    if (!await page.$eval('.reader-advanced', el => el.open)) await page.locator('.reader-advanced summary').click();
    const nextRequest = new Promise(resolve => {state.onBatchPreview = resolve;});
    state.batchStep = action + ': new preview';
    await page.locator(button(preview)).click();
    const next = await nextRequest;
    assert.equal(next.body.filters.account_id, 2);
    next.finish(7);
    state.batchStep = action + ': await confirmation';
    await page.waitForSelector(button(confirm));
    old.finish(99);
    assert.equal(await page.$eval('.delete-preview strong', el => el.textContent), '7');
    const response = page.waitForResponse(response => response.url().endsWith('/api/messages/' + action));
    page.once('dialog', dialog => void dialog.accept());
    state.batchStep = action + ': confirm';
    await page.locator(button(confirm)).click();
    state.batchStep = action + ': await result';
    await response;
    await page.waitForFunction(() => !document.querySelector('.delete-preview'));
    assert.deepEqual(state.batchWrites.at(-1), {path: '/api/messages/' + action,
      body: {filters: next.body.filters, confirm: true, created_by: 'local_user',
        ...(action === 'soft-delete' ? {delete_attachments: false} : {}),
        ...(action === 'hard-delete' ? {delete_attachment_files: false} : {})}});
  }
  assert.equal(state.batchWrites.length, 3);
  state.batchChecks = false;
}
