import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';

const source = {id: 1815, account_id: 1, group_id: 11, account_name: '模拟账号', group_name: '模拟群',
  user_id: 1, sender_name: '模拟用户', source_user_id: null, source_message_id: null,
  sent_at: '2026-09-08 18:33:40', message_type: 'text', content_text: '「缺失的模拟原文」 - - - 模拟回复',
  is_deleted: 0, deleted_at: null, attachment_count: 0};
export const recoveryResponses = {
  '/api/messages': {items: [source], total: 1, attachment_summary: {total_count: 0, message_count: 0, by_type: {}}, has_more: false, next_cursor: null},
  '/api/message-view/facets': {members: [], hours: Array(24).fill(0), hidden_noise: 0},
  '/api/messages/1815': {...source, attachments: []},
  '/api/messages/1815/quote-context': {source_message_id: 1815, account_id: 1, group_id: 11,
    deleted_only: false, status: 'not_found', match_method: 'full_text', target: null, items: [], has_older: false, has_newer: false},
  '/api/messages/1815/recover-context': {id: 100, message: '模拟任务已排队'},
};

export async function checkRecoveryTime(page, submitted) {
  await page.setViewport({width: 1366, height: 800});
  await page.locator('xpath///button[normalize-space(.)="消息"]').click();
  await page.locator('xpath///button[normalize-space(.)="查看引用原文"]').click();
  await page.locator('xpath///summary[contains(.,"创建有界补采集任务")]').click();
  for (const [label, value] of [['补采集开始小时', '09'], ['补采集开始分钟', '35'], ['补采集结束小时', '17'], ['补采集结束分钟', '59']]) {
    await page.click(`[aria-label="${label}选择"]`);
    const metrics = await page.$eval(`[aria-label="${label}"]`, el => {
      const rect = el.getBoundingClientRect();
      const dialog = el.closest('dialog').getBoundingClientRect();
      return {rows: el.size, height: el.clientHeight, row: el.options[0].getBoundingClientRect().height,
        top: rect.top, bottom: rect.bottom, dialogTop: dialog.top, dialogBottom: dialog.bottom,
        scrollable: el.scrollHeight > el.clientHeight};
    });
    assert.equal(metrics.rows, 12);
    assert.equal(metrics.height, metrics.row * 12);
    assert.equal(metrics.scrollable, true);
    assert.ok(metrics.top >= metrics.dialogTop && metrics.bottom <= metrics.dialogBottom,
      '补采集时间列表必须完整显示在引用弹窗内，不被边界裁切');
    await page.select(`[aria-label="${label}"]`, value);
    const option = await page.$(`[aria-label="${label}"] option[value="${value}"]`);
    await option.evaluate(el => el.scrollIntoView({block: 'nearest'}));
    await option.click();
    assert.equal(await page.$(`[aria-label="${label}"]`), null);
  }
  await page.click('.recovery-confirm input');
  await page.locator('xpath///button[normalize-space(.)="创建补采集任务（等待启动确认）"]').click();
  await page.waitForFunction(() => document.body.textContent.includes('任务 #100：模拟任务已排队'));
  assert.deepEqual(submitted.at(-1), {account_id: 1, group_id: 11,
    range_start: '2026-09-08T09:35', range_end: '2026-09-08T17:59', confirmed: true});
  await page.click('[aria-label="补采集结束分钟选择"]');
  const artifacts = new URL('../../.tdd-results/ui/', import.meta.url);
  await mkdir(artifacts, {recursive: true});
  await page.screenshot({path: fileURLToPath(new URL('recovery-time-desktop.png', artifacts))});
  await page.keyboard.press('Escape');
  assert.ok(await page.$('dialog[open]'), '关闭时间列表不应关闭引用弹窗');
  await page.locator('xpath///button[normalize-space(.)="返回原列表"]').click();
}
