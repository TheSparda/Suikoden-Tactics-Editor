/* Behavioral test for the pure VCDIFF encoder: build a patch from known runs and
 * round-trip it through the REAL xdelta3 to reconstruct the target byte-exactly.
 * Self-skips (exit 0) if xdelta3 isn't installed. */
import { spawnSync } from 'node:child_process';
import { writeFileSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

// vcdiff.js is a classic-script IIFE (no ESM exports) that assigns globalThis.VCDIFF;
// import it for its side effect, then read the global.
await import(pathToFileURL(join(dirname(dirname(fileURLToPath(import.meta.url))), 'vcdiff.js')).href);
const VCDIFF = globalThis.VCDIFF;

let pass = 0, fail = 0;
const ok = (c, l) => { if (c) { pass++; console.log('  PASS ' + l); } else { fail++; console.log('  FAIL ' + l); } };

// --- encInt sanity (RFC 3284 examples: 0->[0], 128->[0x81,0x00], 16384->[0x81,0x80,0x00])
console.log('=== encInt ===');
ok(JSON.stringify(VCDIFF.encInt(0)) === '[0]', 'encInt(0)');
ok(JSON.stringify(VCDIFF.encInt(127)) === '[127]', 'encInt(127)');
ok(JSON.stringify(VCDIFF.encInt(128)) === '[129,0]', 'encInt(128)');
ok(JSON.stringify(VCDIFF.encInt(16384)) === '[129,128,0]', 'encInt(16384)');

// --- build a source, apply edits, round-trip via xdelta3 ---
const has = spawnSync('xdelta3', ['-V'], { stdio: 'ignore' }).status === 0
  || spawnSync('xdelta3', ['-h'], { stdio: 'ignore' }).status !== null;
if (!has) { console.log('\nvcdiff: xdelta3 not installed — skipping round-trip (exit 0)'); process.exit(fail ? 1 : 0); }

console.log('\n=== xdelta3 round-trip ===');
const N = 5000;
const source = new Uint8Array(N);
for (let i = 0; i < N; i++) source[i] = (i * 37 + 11) & 0xff;   // deterministic filler

// edits: a 1-byte change, a multi-byte run, and an edit at the very end
const runs = [
  { pos: 100, bytes: Uint8Array.from([0xaa]) },
  { pos: 2000, bytes: Uint8Array.from([1, 2, 3, 4, 5]) },
  { pos: N - 3, bytes: Uint8Array.from([0xde, 0xad, 0xff]) },
];
const expected = source.slice();
for (const r of runs) expected.set(r.bytes, r.pos);

const patch = VCDIFF.build(N, runs, N);
ok(patch[0] === 0xd6 && patch[1] === 0xc3 && patch[2] === 0xc4, 'patch has VCDIFF magic');
ok(patch.length < 200, `patch is small (${patch.length} bytes for a ${N}-byte file)`);

const dir = mkdtempSync(join(tmpdir(), 'vcdiff-'));
try {
  const src = join(dir, 'src.bin'), pat = join(dir, 'p.xdelta'), out = join(dir, 'out.bin');
  writeFileSync(src, source); writeFileSync(pat, patch);
  const r = spawnSync('xdelta3', ['-d', '-f', '-s', src, pat, out], { encoding: 'utf8' });
  ok(r.status === 0, 'xdelta3 -d decodes the patch' + (r.status ? ': ' + (r.stderr || '').split('\n')[0] : ''));
  if (r.status === 0) {
    const got = new Uint8Array(readFileSync(out));
    ok(got.length === expected.length, `output length ${got.length} == ${expected.length}`);
    let same = got.length === expected.length;
    for (let i = 0; same && i < got.length; i++) if (got[i] !== expected[i]) same = false;
    ok(same, 'reconstructed target is byte-exact');
  }
} finally { rmSync(dir, { recursive: true, force: true }); }

console.log('\n================ SUMMARY ================');
console.log(`PASS: ${pass}   FAIL: ${fail}`);
process.exit(fail ? 1 : 0);
