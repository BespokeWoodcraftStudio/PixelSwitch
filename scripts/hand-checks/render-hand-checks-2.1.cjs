// Renders the 2.1 hand-check page at 1920x1080 in light and dark, and tests the Copy button.
//   node scripts/hand-checks/render-hand-checks-2.1.cjs <out-dir>
// Writes <out-dir>/hc21-<light|dark>-{top,full,readable,ultra,signin-expiry}.png and prints what Copy put on the clipboard.
// Needs a playwright-core (set PLAYWRIGHT_CORE to its folder, or it looks in ~/.npm/_npx/*/node_modules and the global
// npm folder) and Google Chrome. Unlike render-hand-checks.cjs (the 2026-09-25 page), nothing here names one user's home.
const fs = require('fs'), os = require('os'), path = require('path');
function findPlaywright() {
  const cands = [];
  if (process.env.PLAYWRIGHT_CORE) cands.push(process.env.PLAYWRIGHT_CORE);
  const npx = path.join(os.homedir(), '.npm', '_npx');
  if (fs.existsSync(npx)) for (const d of fs.readdirSync(npx)) cands.push(path.join(npx, d, 'node_modules', 'playwright-core'));
  cands.push(path.join(os.homedir(), '.npm-global/lib/node_modules/@playwright/mcp/node_modules/playwright-core'));
  cands.push('/opt/homebrew/lib/node_modules/playwright-core');
  for (const c of cands) if (fs.existsSync(path.join(c, 'package.json'))) return c;
  throw new Error('playwright-core not found; set PLAYWRIGHT_CORE');
}
const { chromium } = require(findPlaywright());
const out = process.argv[2];
if (!out) { console.error('usage: node render-hand-checks-2.1.cjs <out-dir>'); process.exit(2); }
fs.mkdirSync(out, { recursive: true });
const page21 = path.resolve(__dirname, '../../docs/decisions/pixelswitch-hand-checks-2.1-2026-11-10.html');
const url = 'file://' + page21;
const ids = ['readable', 'ultra', 'signin-expiry'];
(async () => {
  const browser = await chromium.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true });
  const errors = [];
  for (const scheme of ['light', 'dark']) {
    const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 }, colorScheme: scheme, permissions: ['clipboard-read', 'clipboard-write'] });
    const page = await ctx.newPage();
    page.on('console', m => { if (m.type() === 'error') errors.push(scheme + ': ' + m.text()); });
    page.on('pageerror', e => errors.push(scheme + ' pageerror: ' + e.message));
    page.on('requestfailed', r => errors.push(scheme + ' request failed: ' + r.url()));
    await page.goto(url);
    await page.waitForLoadState('load');
    await page.screenshot({ path: `${out}/hc21-${scheme}-top.png` });
    await page.screenshot({ path: `${out}/hc21-${scheme}-full.png`, fullPage: true });
    for (const id of ids) {
      await page.locator('#q-' + id).scrollIntoViewIfNeeded();
      await page.locator('#q-' + id).scrollIntoViewIfNeeded();
      await page.evaluate(i => document.getElementById('q-' + i).scrollIntoView({ block: 'start' }), id);
      await page.waitForTimeout(150);
      await page.screenshot({ path: `${out}/hc21-${scheme}-${id}.png` });
    }
    const hscroll = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
    const imgs = await page.evaluate(() => Array.from(document.images).map(i => ({ src: i.getAttribute('src').split('/').pop(), ok: i.complete && i.naturalWidth > 0 })));
    console.log(scheme, '| horizontal scroll:', hscroll, '| cards:', await page.locator('section.card').count(), '| images:', JSON.stringify(imgs));
    if (scheme === 'light') {
      await page.check('input[name="readable"][value="0"]');
      await page.fill('#c-ultra', 'test comment');
      await page.check('input[name="signin-expiry"][value="0"]');
      await page.click('#copy');
      await page.waitForTimeout(400);
      const clip = await page.evaluate(() => navigator.clipboard.readText());
      console.log('copy button text after click:', await page.locator('#copy').innerText());
      console.log('--- clipboard ---\n' + clip + '\n--- end clipboard ---');
    }
    await ctx.close();
  }
  console.log('errors:', errors.length ? errors : 'none');
  await browser.close();
})();
