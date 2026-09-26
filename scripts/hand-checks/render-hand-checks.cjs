// Renders the hand-check page at 1920x1080 in light and dark, tests Copy.
// node scripts/hand-checks/render-hand-checks.cjs <out-dir>   (uses the Playwright
// that ships with @playwright/mcp and the installed Google Chrome, headless)
const { chromium } = require('/Users/ahamade/.npm-global/lib/node_modules/@playwright/mcp/node_modules/playwright-core');
const url = 'file:///Users/ahamade/Documents/GitHub/PixelSwitch/docs/decisions/pixelswitch-hand-checks-2026-09-25.html';
const out = process.argv[2];
(async () => {
  const browser = await chromium.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true });
  const errors = [];
  for (const scheme of ['light', 'dark']) {
    const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 }, colorScheme: scheme, permissions: ['clipboard-read', 'clipboard-write'] });
    const page = await ctx.newPage();
    page.on('console', m => { if (m.type() === 'error') errors.push(scheme + ': ' + m.text()); });
    page.on('pageerror', e => errors.push(scheme + ' pageerror: ' + e.message));
    await page.goto(url);
    await page.screenshot({ path: `${out}/hc-${scheme}-top.png` });
    await page.locator('#q-signin').scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${out}/hc-${scheme}-card.png` });
    const hscroll = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
    console.log(scheme, 'horizontal scroll:', hscroll, '| cards:', await page.locator('section.card').count());
    if (scheme === 'light') {
      await page.check('input[name="install"][value="0"]');
      await page.fill('#c-autoswitch', 'test comment');
      await page.click('#copy');
      await page.waitForTimeout(300);
      const clip = await page.evaluate(() => navigator.clipboard.readText());
      console.log('--- clipboard ---\n' + clip.split('\n').slice(0, 9).join('\n') + '\n...\n' + clip.split('\n').slice(-3).join('\n'));
    }
    await ctx.close();
  }
  console.log('errors:', errors.length ? errors : 'none');
  await browser.close();
})();
