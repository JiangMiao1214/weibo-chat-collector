'use strict';

const fs = require('fs');

const CANDIDATES = {
  darwin: [
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
    '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
  ],
  linux: [
    '/usr/bin/google-chrome',
    '/usr/bin/google-chrome-stable',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
    '/snap/bin/chromium',
    '/usr/bin/microsoft-edge',
  ],
  win32: [
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  ],
};

const WSL_CANDIDATES = [
  '/mnt/c/Program Files/Google/Chrome/Application/chrome.exe',
  '/mnt/c/Program Files (x86)/Google/Chrome/Application/chrome.exe',
];

function isWSL() {
  try {
    return /microsoft|wsl/i.test(fs.readFileSync('/proc/version', 'utf8'));
  } catch {
    return false;
  }
}

function chromeCandidates(platform = process.platform) {
  const candidates = [...(CANDIDATES[platform] || [])];
  if (platform === 'linux' && isWSL()) candidates.push(...WSL_CANDIDATES);
  return candidates;
}

function resolveChromePath(configuredPath = '', platform = process.platform) {
  if (configuredPath && fs.existsSync(configuredPath)) return configuredPath;
  for (const candidate of chromeCandidates(platform)) {
    if (fs.existsSync(candidate)) return candidate;
  }
  if (configuredPath) return configuredPath;
  const error = new Error(`No supported Chrome, Chromium, or Edge executable was found for ${platform}.`);
  error.code = 'browser_not_found';
  throw error;
}

module.exports = { chromeCandidates, isWSL, resolveChromePath };
