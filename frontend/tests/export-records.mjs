import {readFile, writeFile} from 'node:fs/promises';

const START = '<!-- export-records:start -->';
const END = '<!-- export-records:end -->';
const HEADER = '| 记录时间（UTC） | 内容 |\n| --- | --- |';

export function withExportRecord(document, content, recordedAt) {
  if (content !== '测试链接 https://fixture.invalid/article') throw new Error('只允许记录固定模拟导出内容，拒绝真实或未知数据');
  if (new Date(recordedAt).toISOString() !== recordedAt) throw new Error('记录时间必须为标准 ISO UTC 时间');
  const start = document.indexOf(START);
  const end = document.indexOf(END);
  if (start < 0 || end < start) throw new Error('记录文档缺少边界标记，拒绝覆盖');
  const rows = document.slice(start + START.length, end).trim().split('\n')
    .filter(line => line && !HEADER.split('\n').includes(line));
  rows.push(`| ${recordedAt} | ${content} |`);
  return document.slice(0, start + START.length) + '\n\n' + HEADER + '\n' + [...new Set(rows)].sort().join('\n') + '\n\n' + document.slice(end);
}

export async function recordTestExport(file, content, recordedAt) {
  const previous = await readFile(file, 'utf8');
  await writeFile(file, withExportRecord(previous, content, recordedAt), 'utf8');
}
