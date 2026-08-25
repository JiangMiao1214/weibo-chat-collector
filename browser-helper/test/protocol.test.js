'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const {
  extractGroupId,
  hasUsableSubCookie,
  isLoggedInText,
  waitForUsableSubCookies,
} = require('../src/protocol');

test('extractGroupId only accepts the target path and a numeric id', () => {
  assert.equal(
    extractGroupId('https://api.weibo.com/webim/groupchat/query_messages.json?id=123456&max_mid=0'),
    '123456',
  );
  assert.equal(extractGroupId('https://api.weibo.com/other/query_messages.json?id=123456'), null);
  assert.equal(
    extractGroupId('https://example.com/webim/groupchat/query_messages.json?id=123456'),
    null,
  );
  assert.equal(
    extractGroupId('http://api.weibo.com/webim/groupchat/query_messages.json?id=123456'),
    null,
  );
  assert.equal(
    extractGroupId('https://api.weibo.com/webim/groupchat/query_messages.json?id=abc'),
    null,
  );
  assert.equal(extractGroupId('not-a-url'), null);
});

test('isLoggedInText keeps scan pages pending', () => {
  assert.equal(isLoggedInText('扫描登录'.padEnd(800, 'x')), false);
  assert.equal(isLoggedInText('立即注册'.padEnd(800, 'x')), false);
  assert.equal(isLoggedInText('聊天列表'.padEnd(800, 'x')), true);
  assert.equal(isLoggedInText('短文本'), false);
});

test('hasUsableSubCookie waits for a non-expired SUB that can reach the API', () => {
  const now = 1_800_000_000;
  assert.equal(hasUsableSubCookie([], now), false);
  assert.equal(
    hasUsableSubCookie([{ name: 'SUBP', value: 'not-enough', domain: '.weibo.com' }], now),
    false,
  );
  assert.equal(
    hasUsableSubCookie([{ name: 'SUB', value: 'expired', domain: '.weibo.com', expires: now }], now),
    false,
  );
  assert.equal(
    hasUsableSubCookie([{ name: 'SUB', value: 'wrong-domain', domain: '.sina.com.cn' }], now),
    false,
  );
  assert.equal(
    hasUsableSubCookie([
      { name: 'SUB', value: 'ready', domain: '.weibo.com', path: '/', expires: now + 60 },
    ], now),
    true,
  );
});

test('waitForUsableSubCookies retries until the browser exposes SUB', async () => {
  const readyCookies = [{ name: 'SUB', value: 'ready', domain: '.weibo.com', path: '/' }];
  const probes = [[], [{ name: 'SUBP', value: 'pending', domain: '.weibo.com' }], readyCookies];
  let readCount = 0;
  const result = await waitForUsableSubCookies(
    async () => probes[readCount++],
    { intervalMs: 0, sleep: async () => {} },
  );
  assert.equal(readCount, 3);
  assert.equal(result, readyCookies);
});
