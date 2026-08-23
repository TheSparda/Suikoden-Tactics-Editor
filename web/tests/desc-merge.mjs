/* Behavioral test for the reference-data enrichment (B13): assert the generated
 * rune/item reference files actually contain the expected DESCRIPTIONS (not just
 * that the files exist), keyed the way the UI looks them up. Guards against the
 * generators silently producing empty or drifted data. */
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const DATA = join(dirname(dirname(dirname(fileURLToPath(import.meta.url)))), 'st-editor', 'data');
const load = (f) => (existsSync(join(DATA, f)) ? JSON.parse(readFileSync(join(DATA, f), 'utf8')) : null);

let pass = 0, fail = 0;
const ok = (c, l) => { if (c) { pass++; console.log('  PASS ' + l); } else { fail++; console.log('  FAIL ' + l); } };

console.log('=== rune reference (st_rune_ref.json) ===');
const rune = load('st_rune_ref.json');
ok(rune, 'st_rune_ref.json present');
if (rune) {
  ok(Object.keys(rune).length >= 40, `${Object.keys(rune).length} runes have notes`);
  // Fire Rune = id 0x01; description mentions fire magic
  ok(rune['01'] && /fire magic/i.test(rune['01'].desc || ''), `Fire Rune (01) desc = "${rune['01'] && rune['01'].desc}"`);
  ok(rune['02'] && /water magic/i.test(rune['02'].desc || ''), 'Water Rune (02) mentions water magic');
  // no entry should be blank/garbage (correct-or-blank)
  const blank = Object.entries(rune).filter(([, v]) => !v.desc && !(v.abilities && v.abilities.length));
  ok(blank.length === 0, `no empty rune entries (${blank.length})`);
}

console.log('\n=== item reference (st_item_ref.json) ===');
const item = load('st_item_ref.json');
ok(item, 'st_item_ref.json present');
if (item) {
  ok(Object.keys(item).length >= 100, `${Object.keys(item).length} items have descriptions`);
  ok(/heals\s*50\s*hp/i.test(item['001'] || ''), `Medicine (001) = "${item['001']}"`);
  ok(/poison/i.test(item['005'] || ''), `Antitoxin (005) = "${item['005']}"`);
}

console.log('\n=== keys align with UI lookup format ===');
const items = load('st_shop_items.json');
if (item && items) {
  // every item-ref key must be a real item id (3-digit decimal) present in shop_items
  const bad = Object.keys(item).filter((k) => !items[k]);
  ok(bad.length === 0, `all item-ref keys map to a real item (${bad.length} bad)`);
}
const runes = load('st_runes.json'), orbs = load('st_rune_orbs.json');
if (rune && runes) {
  const bad = Object.keys(rune).filter((k) => !(runes[k] || (orbs && orbs[k])));
  ok(bad.length === 0, `all rune-ref keys map to a real rune/orb (${bad.length} bad)`);
}

console.log('\n================ SUMMARY ================');
console.log(`PASS: ${pass}   FAIL: ${fail}`);
process.exit(fail ? 1 : 0);
