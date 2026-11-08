import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';

async function assertReaderFrame(page) {
  const frame = await page.evaluate(() => {
    const workspace = document.querySelector('.compact-reader');
    const panel = workspace.querySelector('.message-panel');
    const list = panel.querySelector('.message-list');
    const heading = workspace.querySelector('.reader-heading');
    const members = workspace.querySelector('.member-list');
    return {workspaceScroll: workspace.scrollTop, panelScroll: panel.scrollTop,
      headingVisible: heading.getBoundingClientRect().top >= workspace.getBoundingClientRect().top,
      bottomGap: workspace.getBoundingClientRect().bottom - list.getBoundingClientRect().bottom,
      membersFit: members.getBoundingClientRect().bottom <= workspace.getBoundingClientRect().bottom,
      listScrollable: list.scrollHeight > list.clientHeight};
  });
  assert.equal(frame.workspaceScroll, 0, '引用定位不得滚动整个工作区、裁掉顶部筛选');
  assert.equal(frame.panelScroll, 0, '消息面板不应形成第二层滚动');
  assert.equal(frame.headingVisible, true, '账号、群、日期应始终可见');
  assert.ok(Math.abs(frame.bottomGap) <= 2, `列表应延伸到工作区底部，实际空白 ${frame.bottomGap}px`);
  assert.equal(frame.membersFit, true, '长成员列表应在侧栏内滚动，不撑破阅读区');
  assert.equal(frame.listScrollable, true, '长列表应保留独立滚动');
}

export async function checkLongReaderLayout(page, artifacts) {
  await page.setViewport({width: 1366, height: 800});
  // Switch scope to refresh both message data and member statistics.
  await page.select('xpath///label[span[normalize-space(.)="账号"]]/select', '1');
  await page.waitForFunction(() => document.querySelectorAll('.chat-message').length === 82 &&
    document.querySelectorAll('.member-choice').length === 40);
  await page.$eval('.message-list', el => {el.scrollTop = el.scrollHeight;});
  await page.locator('[aria-label="查看匹配消息 #2"]').click();
  await page.waitForFunction(() => document.activeElement?.getAttribute('aria-label') === '查看消息 #2');
  await assertReaderFrame(page);
  assert.ok(await page.$eval('.message-list', el => el.scrollTop < 100), '升序后的最早引用目标应滚动到列表顶部');
  await page.$eval('.message-list', el => {el.scrollTop = el.scrollHeight;});
  await page.locator('[aria-label="正文查找（保留上下文）"]').fill('测试链接');
  await page.waitForFunction(() => document.querySelector('[aria-label="匹配消息进度"]')?.textContent.includes('1 条匹配消息'));
  await page.locator('xpath///button[normalize-space(.)="下一个匹配"]').click();
  await assertReaderFrame(page);
  assert.ok(await page.$eval('.message-list', el => el.scrollTop < 100), '搜索也应定位到升序列表顶部');
  await page.locator('[aria-label="上下文 #2"]').click();
  await page.waitForSelector('[aria-label="消息上下文"]');
  await assertReaderFrame(page);
  await page.locator('xpath///button[normalize-space(.)="关闭上下文"]').click();
  await page.locator('[aria-label="查看消息 #2"]').click();
  await assertReaderFrame(page);
  await page.screenshot({path: fileURLToPath(new URL('reader-long-list.png', artifacts))});
  await page.setViewport({width: 390, height: 844});
  await page.locator('xpath///button[normalize-space(.)="关闭详情"]').click();
  await assertReaderFrame(page);
  await page.screenshot({path: fileURLToPath(new URL('reader-long-list-mobile.png', artifacts))});
}
