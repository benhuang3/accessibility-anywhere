// Build with tests/phone-preview.config.mjs, then run vite preview on port 4178.
// Run: node tests/mini-ended.preview.mjs (requires Playwright; PLAYWRIGHT_MODULE may point to it).
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || 'playwright');
const browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH });
const errors = [], external = [];
try {
  const page = await browser.newPage({ viewport: { width: 380, height: 290 }, deviceScaleFactor: 1 });
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.hostname === '127.0.0.1' || url.protocol === 'data:') return route.continue();
    external.push(url.origin);
    return route.abort();
  });
  const base = 'http://127.0.0.1:4178/#room=0123456789abcdef0123456789abcdef&name=Priya';
  await page.goto(base + '&role=elder&debug=1');
  await page.locator('html[data-ally-call="connected"]').waitFor();
  await page.getByRole('button', { name: 'End call', exact: true }).click();
  await page.locator('html[data-ally-call="ended"]').waitFor();
  assert.equal(await page.title(), 'Call ended \u00b7 Ally Call');
  assert.equal((await page.locator('main').innerText()).replace(/\s+/g, ' ').trim(), '\u2713 Call ended');
  const bounds = await page.locator('.ended-card').boundingBox();
  assert.ok(bounds && bounds.x >= 0 && bounds.y >= 0 && bounds.x + bounds.width <= 380 && bounds.y + bounds.height <= 290);
  const size = await page.evaluate(() => ({
    width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight
  }));
  assert.deepEqual(size, { width: 380, height: 290 });
  await page.screenshot({ path: fileURLToPath(new URL('./shots/mini-ended.png', import.meta.url)) });

  // The phone helper retains its existing ended page and close-page guidance.
  await page.setViewportSize({ width: 390, height: 740 });
  await page.goto(base + '&role=helper');
  await page.getByRole('button', { name: 'Join call', exact: true }).click();
  await page.getByRole('button', { name: 'End call', exact: true }).click();
  await page.getByText('You can close this page now.', { exact: true }).waitFor();
  assert.equal(await page.locator('.ended-card').count(), 0);
  assert.deepEqual(external, []);
  assert.deepEqual(errors, []);
  console.log('PASS: elder ended card fits 380x290 with no scrolling; helper ended page unchanged.');
} finally {
  await browser.close();
}
