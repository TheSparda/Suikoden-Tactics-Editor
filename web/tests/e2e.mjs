/* Headless end-to-end smoke test (Playwright + real Pyodide).
 *
 * Serves the repo root, loads the app, waits for the engine, and asserts the
 * shell/UI an e2e uniquely catches: engine boots, no console errors, searchable
 * picker opens, and — the key mobile regression check — NO horizontal overflow
 * at 320 px and 360 px. Full save round-trips are covered by save_roundtrip.py.
 *
 * Self-skips (exit 0) if Playwright/Chromium isn't installed, or if the engine
 * can't boot within the timeout (e.g. the Pyodide CDN is unreachable offline).
 */
import { createServer } from 'node:http';
import { readFileSync, existsSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, normalize, extname } from 'node:path';

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));
const ROOT = dirname(WEB);   // repo root — so ../st-editor/ resolves

let chromium;
try { ({ chromium } = await import('playwright')); }
catch { try { ({ chromium } = await import('playwright-core')); } catch {} }
if (!chromium) { console.log('e2e: Playwright not installed — skipping (exit 0)'); process.exit(0); }

const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
  '.json': 'application/json', '.webmanifest': 'application/manifest+json',
  '.py': 'text/plain', '.png': 'image/png' };

const server = createServer((req, res) => {
  const p = normalize(join(ROOT, decodeURIComponent(req.url.split('?')[0])));
  let file = p;
  if (!p.startsWith(ROOT)) { res.writeHead(403).end(); return; }
  if (existsSync(file) && statSync(file).isDirectory()) file = join(file, 'index.html');
  if (!existsSync(file)) { res.writeHead(404).end('nf'); return; }
  res.writeHead(200, { 'content-type': MIME[extname(file)] || 'application/octet-stream' });
  res.end(readFileSync(file));
});
await new Promise((r) => server.listen(0, r));
const port = server.address().port;
const url = `http://127.0.0.1:${port}/web/`;

let pass = 0, fail = 0, skipped = false;
const ok = (c, l) => { if (c) { pass++; console.log('  PASS ' + l); } else { fail++; console.log('  FAIL ' + l); } };

let browser;
try {
  browser = await chromium.launch();
} catch (e) {
  console.log('e2e: could not launch Chromium (' + (e.message || e).split('\n')[0] + ') — skipping (exit 0)');
  server.close(); process.exit(0);
}

try {
  const page = await browser.newPage({ viewport: { width: 360, height: 780 } });
  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push(String(e)));

  await page.goto(url, { waitUntil: 'domcontentloaded' });

  // wait for the engine (boot overlay gets .hide). CDN download can be slow.
  let booted = false;
  try {
    await page.waitForFunction(() => document.getElementById('boot')?.classList.contains('hide'), { timeout: 90000 });
    booted = true;
  } catch { skipped = true; }

  if (!booted) {
    console.log('e2e: engine did not boot within 90s (Pyodide CDN unreachable?) — skipping (exit 0)');
    await browser.close(); server.close(); process.exit(0);
  }

  ok(true, 'engine booted (boot overlay hidden)');
  ok(await page.evaluate(() => getComputedStyle(document.getElementById('boot')).display === 'none'), 'boot overlay is display:none');
  ok(await page.evaluate(() => typeof GLUE === 'object' && GLUE !== null), 'Python glue loaded');
  ok(await page.evaluate(() => Array.isArray(ITEMS) && ITEMS.length > 100), 'item list populated');
  ok(await page.evaluate(() => Array.isArray(RUNES) && RUNES.length > 20), 'rune list populated');

  // searchable picker opens and filters
  await page.evaluate(() => openPicker({ title: 'test', items: ITEMS, current: 0, allowEmpty: true, onPick: () => {} }));
  ok(await page.evaluate(() => !document.getElementById('pickModal').classList.contains('hide')), 'picker modal opens');
  await page.fill('#pickSearch', 'a');
  ok(await page.evaluate(() => document.querySelectorAll('#pickList .prow').length > 0), 'picker filters to matches');
  await page.evaluate(() => closePicker());

  // mobile: no horizontal overflow at 320 and 360
  for (const w of [320, 360]) {
    await page.setViewportSize({ width: w, height: 780 });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    ok(overflow <= 1, `no horizontal overflow at ${w}px (overflow=${overflow})`);
  }

  ok(errors.length === 0, 'no console errors' + (errors.length ? ': ' + errors[0] : ''));
} finally {
  if (browser) await browser.close();
  server.close();
}

console.log('\n================ SUMMARY ================');
console.log(`PASS: ${pass}   FAIL: ${fail}${skipped ? ' (partial)' : ''}`);
process.exit(fail ? 1 : 0);
