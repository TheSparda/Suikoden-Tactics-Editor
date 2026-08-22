/* Static validation — fast, no browser. Asserts the app is wired correctly so
 * a broken deploy is caught before it ships. Mirrors the S3 playbook's
 * validate.mjs. Run: node tests/validate.mjs   (from web/)
 */
import { readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));
const read = (p) => readFileSync(join(WEB, p), 'utf8');

let pass = 0, fail = 0;
const ok = (cond, label) => { if (cond) { pass++; console.log('  PASS ' + label); } else { fail++; console.log('  FAIL ' + label); } };

console.log('=== JS files parse ===');
for (const f of ['app.js', 'sw.js', 'tests/validate.mjs', 'tests/e2e.mjs']) {
  try { execFileSync(process.execPath, ['--check', join(WEB, f)]); ok(true, f + ' parses'); }
  catch (e) { ok(false, f + ' parses (' + (e.stderr || e.message).toString().split('\n')[0] + ')'); }
}

console.log('\n=== index.html wiring ===');
const html = read('index.html');
ok(html.includes('src="app.js"'), 'loads app.js');
ok(html.includes('cdn.jsdelivr.net/pyodide/v0.26.2/'), 'pins Pyodide v0.26.2');
ok(html.includes('viewport-fit=cover'), 'viewport-fit=cover (notch-safe)');
ok(html.includes('rel="manifest"'), 'links the manifest');
for (const id of ['boot', 'bootBar', 'drop', 'file', 'fsBtn', 'lastChip', 'installBtn',
  'unsaved', 'char', 'stats', 'equip', 'runes', 'invBody', 'target', 'delivery',
  'applyBtn', 'pickModal', 'pickSearch', 'pickList', 'reviewModal', 'reviewBody',
  'reviewConfirm', 'actionbar']) {
  ok(html.includes(`id="${id}"`), `has #${id}`);
}

console.log('\n=== app.js features present ===');
const app = read('app.js');
for (const [needle, label] of [
  ['openPicker', 'searchable picker'],
  ['buildDiff', 'diff / review builder'],
  ['updateBadge', 'unsaved badge'],
  ['beforeunload', 'unsaved-changes guard'],
  ['beforeinstallprompt', 'install prompt'],
  ['showOpenFilePicker', 'File System Access load'],
  ['createWritable', 'save-in-place'],
  ['navigator.share', 'Web Share (out)'],
  ['shared', 'Web Share (in) pickup'],
  ['indexedDB', 'IndexedDB last-opened'],
  ['bootProgress', 'staged boot progress'],
]) ok(app.includes(needle), label);

console.log('\n=== service worker ===');
const sw = read('sw.js');
for (const f of ['./index.html', './app.js', './style.css', './st_glue.py',
  '../st-editor/stsaveio.py', '../st-editor/stsavefields.py',
  '../st-editor/data/st_shop_items.json', '../st-editor/data/st_runes.json']) {
  ok(sw.includes(`'${f}'`), 'precaches ' + f);
}
ok(/CACHE\s*=\s*'st-save-editor-v\d+'/.test(sw), 'has a versioned CACHE name');
ok(sw.includes("'POST'") && sw.includes('shared=1'), 'handles the Web Share POST');
ok(sw.includes('SHARE_CACHE') && sw.includes("k !== SHARE_CACHE"), 'never purges the share cache');

console.log('\n=== manifest ===');
const mani = JSON.parse(read('manifest.webmanifest'));
ok(mani.display === 'standalone', 'display: standalone');
ok(mani.orientation === 'portrait', 'orientation: portrait');
ok(mani.share_target && mani.share_target.method === 'POST', 'declares a share_target (POST)');
ok(mani.icons.some((i) => (i.purpose || '').includes('maskable')), 'has a maskable icon');
ok(mani.icons.some((i) => i.sizes === '192x192') && mani.icons.some((i) => i.sizes === '512x512'), 'has 192 + 512 icons');

console.log('\n=== reference data ===');
const items = JSON.parse(read('../st-editor/data/st_shop_items.json'));
const runes = JSON.parse(read('../st-editor/data/st_runes.json'));
ok(Object.keys(items).length > 100, `shop_items has ${Object.keys(items).length} entries`);
ok(Object.keys(runes).length > 20, `runes has ${Object.keys(runes).length} entries`);

console.log('\n================ SUMMARY ================');
console.log(`PASS: ${pass}   FAIL: ${fail}`);
process.exit(fail ? 1 : 0);
