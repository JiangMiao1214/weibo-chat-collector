import test from 'node:test';
import assert from 'node:assert/strict';
import {withExportRecord} from './export-records.mjs';

const template = '# 记录\n<!-- export-records:start -->\n<!-- export-records:end -->\n保留备注';
const content = '测试链接 https://fixture.invalid/article';
test('模拟导出 Markdown 按时间排序、相同记录去重并保留文档其他内容', () => {
  const later = withExportRecord(template, content, '2026-09-12T11:00:00.000Z');
  const earlier = withExportRecord(later, content, '2026-09-12T10:00:00.000Z');
  assert.ok(earlier.indexOf('T10:00') < earlier.indexOf('T11:00'));
  assert.equal(withExportRecord(earlier, content, '2026-09-12T10:00:00.000Z'), earlier);
  assert.ok(earlier.startsWith('# 记录'));
  assert.ok(earlier.endsWith('保留备注'));
  assert.equal(earlier.match(/记录时间（UTC）/g).length, 1, '重复更新只保留一组 Markdown 表头');
});
test('模拟导出日志拒绝非夹具内容和无标记文档', () => {
  assert.throws(() => withExportRecord(template, '未知内容', '2026-09-12T10:00:00.000Z'), /只允许/);
  assert.throws(() => withExportRecord('用户文档', content, '2026-09-12T10:00:00.000Z'), /拒绝覆盖/);
});
