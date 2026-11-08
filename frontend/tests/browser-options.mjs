// Keep all browser-test process settings together; never reuse a user profile.
export function testBrowserOptions(executablePath) {
  return {executablePath, headless: true, downloadBehavior: {policy: 'deny'},
    args: ['--disable-background-networking', '--disable-component-update']};
}
