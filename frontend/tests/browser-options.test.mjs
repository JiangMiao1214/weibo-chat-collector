import test from 'node:test';
import assert from 'node:assert/strict';
import {testBrowserOptions} from './browser-options.mjs';

test('隔离浏览器禁止向系统下载目录保存导出文件', () => {
  const options = testBrowserOptions('/fixture/chrome');
  assert.deepEqual(options.downloadBehavior, {policy: 'deny'}, '必须显式禁止测试浏览器落盘下载，不能沿用系统默认目录');
  assert.equal(options.userDataDir, undefined, '不得复用用户浏览器资料');
  assert.equal(options.headless, true);
});
