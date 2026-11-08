import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import {readFileSync} from 'node:fs';
const preview = readFileSync(new URL('./fixtures/archive-preview.png', import.meta.url));

export function archivedPictures() {
  return [71, 72].map((id, index) => ({id, message_id: 1, kind: 'image', status: 'stored',
    source_url: `https://fixture.invalid/attachment-${index+1}`}));
}

export function archiveImageResponse(url, res) {
  if (!/^\/api\/archives\/7[12]\/(thumbnail|content)$/.test(url.pathname)) return false;
  res.setHeader('Content-Type', 'image/png');
  res.end(preview);
  return true;
}

export async function checkArchiveImprovements(page, artifacts, state) {
  state.archivedImages = true;
  const button = label => `xpath///button[normalize-space(.)='${label}']`;
  await page.setViewport({width: 1440, height: 1000});
  await page.locator('[aria-label="查看消息 #2"]').click();
  await page.locator('[aria-label="查看消息 #1"]').click();
  await page.waitForSelector('.reader-detail-wrap .archive-gallery button');
  await page.locator('.reader-detail-wrap .archive-gallery button').click();
  await page.waitForSelector('.archive-lightbox[open]');
  await page.waitForFunction(() => document.querySelector('.archive-lightbox img')?.naturalWidth > 0);
  await page.locator(button('下一张')).click();
  assert.equal(await page.$eval('.archive-lightbox header span', el => el.textContent), '2 / 2');
  await page.locator(button('原尺寸')).click();
  assert.ok(await page.$('.archive-image-stage.original-size'));
  await page.screenshot({path: fileURLToPath(new URL('archive-gallery-desktop.png', artifacts))});
  await page.locator(button('适应窗口')).click();
  await page.setViewport({width: 390, height: 844});
  assert.equal(await page.$eval('.archive-lightbox', el => el.scrollWidth <= el.clientWidth), true);
  await page.screenshot({path: fileURLToPath(new URL('archive-gallery-mobile.png', artifacts))});
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => !document.querySelector('.archive-lightbox'));
  assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')), '放大图片');
  // On mobile the details drawer covers the message toolbar; close it first.
  await page.locator(button('关闭详情')).click();
  await page.waitForFunction(() => !document.querySelector('.reader-detail-wrap'));
  await page.locator(button('本地归档')).click();
  await page.waitForSelector('.archive-dialog[open]');
  await page.locator('.archive-maintenance summary').click();
  await page.$eval('.archive-maintenance', el => el.scrollIntoView({block: 'end'}));
  assert.equal(await page.$eval('.archive-dialog', el => el.scrollWidth <= el.clientWidth), true);
  await page.screenshot({path: fileURLToPath(new URL('archive-maintenance-mobile.png', artifacts))});
  await page.keyboard.press('Escape');
}
