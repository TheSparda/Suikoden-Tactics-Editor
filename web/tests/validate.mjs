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
for (const f of ['app.js', 'iso.js', 'vcdiff.js', 'sw.js', 'tests/validate.mjs', 'tests/e2e.mjs']) {
  try { execFileSync(process.execPath, ['--check', join(WEB, f)]); ok(true, f + ' parses'); }
  catch (e) { ok(false, f + ' parses (' + (e.stderr || e.message).toString().split('\n')[0] + ')'); }
}

console.log('\n=== index.html wiring ===');
const html = read('index.html');
ok(html.includes('src="app.js"'), 'loads app.js');
ok(html.includes('cdn.jsdelivr.net/pyodide/v0.26.2/'), 'pins Pyodide v0.26.2');
ok(html.includes('viewport-fit=cover'), 'viewport-fit=cover (notch-safe)');
ok(html.includes('rel="manifest"'), 'links the manifest');
ok(html.includes('src="iso.js"'), 'loads iso.js');
ok(html.includes('src="vcdiff.js"'), 'loads vcdiff.js');
ok(html.includes('id="modeTabs"') && html.includes('data-mode="iso"'), 'has Save|ISO mode tabs');
for (const id of ['isoUndo', 'isoRedo', 'isoXdeltaExport', 'appVersion', 'forceRefresh', 'updatePrompt']) {
  ok(html.includes(`id="${id}"`), `has #${id}`);
}
for (const id of ['boot', 'bootBar', 'drop', 'file', 'fsBtn', 'lastChip', 'installBtn',
  'unsaved', 'char', 'stats', 'equip', 'runes', 'invBody', 'target', 'delivery',
  'applyBtn', 'pickModal', 'pickSearch', 'pickList', 'reviewModal', 'reviewBody',
  'reviewConfirm', 'actionbar',
  // ISO editor
  'isoMode', 'isoBlocked', 'isoLoader', 'isoOpenBtn', 'isoBody', 'isoViewTabs',
  'isoView', 'isoActionbar', 'isoApplyBtn', 'isoReviewModal', 'isoReviewConfirm',
  'isoProgress', 'isoProgBar', 'isoRecipeExport', 'isoRecipeImport']) {
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

console.log('\n=== iso.js features present ===');
const iso = read('iso.js');
for (const [needle, label] of [
  ['showOpenFilePicker', 'File System Access open'],
  ['keepExistingData', 'in-place ranged write'],
  ['diffRuns', 'changed-run diff'],
  ['SLUS_MARKER', 'version/region gate (SLUS marker)'],
  ['0x0E', 'Kyril fingerprint check'],
  ['mergeRanges', 'bounded-window ranged read'],
  ['stmod', 'recipe export/import'],
  ['requestPermission', 're-grant write permission'],
  ['beforeunload', 'ISO unsaved guard'],
  ['queueMicrotask', 'undo/redo transaction (microtask)'],
  ['function undo', 'undo'],
  ['function redo', 'redo'],
  ['exportXdelta', 'VCDIFF (.xdelta) export'],
  ['VCDIFF.build', 'uses the VCDIFF encoder'],
  ["data-p=maxgrowth", 'growth/skill presets'],
  ['noteFor', 'inline guide notes (enrichment)'],
]) ok(iso.includes(needle), label);

console.log('\n=== app.js depth features ===');
for (const [needle, label] of [
  ['initUpdateCheck', 'version-behind check'],
  ['forceRefresh', 'force-refresh escape hatch'],
  ['fetchJSONopt', 'defensive reference fetch'],
  ['itemDesc', 'item enrichment helper'],
  ['runeDesc', 'rune enrichment helper'],
]) ok(app.includes(needle), label);

console.log('\n=== vcdiff.js ===');
const vc = read('vcdiff.js');
ok(vc.includes('0xd6') && vc.includes('0xc3') && vc.includes('0xc4'), 'emits VCDIFF magic');
ok(vc.includes('function build') || vc.includes('build ='), 'has build()');
ok(vc.includes('VCD_SOURCE') || vc.includes('0x01'), 'uses a VCD_SOURCE window');

console.log('\n=== char schema (generated from stfields.py) ===');
const schema = JSON.parse(read('../st-editor/data/st_char_schema.json'));
ok(schema.record_len === 0x280, 'record_len = 0x280');
ok(schema.fields.length > 40, `schema has ${schema.fields.length} fields`);
ok(schema.fields.every((f) => f.offset + f.w <= schema.record_len), 'every field fits in the record');
const tables = JSON.parse(read('../st-editor/data/st_tables.json'));
ok(Object.keys(tables).length >= 6, `st_tables.json has ${Object.keys(tables).length} tables`);
ok(Object.values(tables).every((t) => t.base > 0 && t.stride > 0 && t.count > 0 && Array.isArray(t.fields)), 'every table has base/stride/count/fields');
const isoChars = JSON.parse(read('../st-editor/data/st_characters.json'));
ok(isoChars.filter((c) => typeof c.offset === 'number').length >= 60, `st_characters.json has ${isoChars.length} chars with offsets`);

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
ok(sw.includes("'./iso.js'"), 'precaches iso.js');
ok(sw.includes("'./vcdiff.js'"), 'precaches vcdiff.js');
for (const f of ['st_characters.json', 'st_tables.json', 'st_char_schema.json',
  'st_weapon_types.json', 'st_rune_orbs.json', 'st_skills.json', 'st_enemies.json',
  'st_rune_ref.json', 'st_item_ref.json']) {
  ok(sw.includes('/' + f), 'precaches ' + f);
}

console.log('\n=== version consistency (release = tag rule) ===');
const pkg = JSON.parse(read('package.json'));
const vm = html.match(/data-version="([^"]+)"/);
ok(vm && vm[1] === pkg.version, `index data-version (${vm && vm[1]}) matches package.json (${pkg.version})`);

console.log('\n=== reference enrichment data ===');
const runeRef = JSON.parse(read('../st-editor/data/st_rune_ref.json'));
const itemRef = JSON.parse(read('../st-editor/data/st_item_ref.json'));
ok(Object.keys(runeRef).length >= 40, `st_rune_ref.json has ${Object.keys(runeRef).length} runes`);
ok(Object.keys(itemRef).length >= 100, `st_item_ref.json has ${Object.keys(itemRef).length} items`);

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
