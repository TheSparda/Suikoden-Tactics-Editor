/* Pure VCDIFF (.xdelta) encoder — builds a standard RFC 3284 patch directly from
 * known edit runs, without diffing two multi-GB files. The disc is the source;
 * the patched disc is the target of the same length. We tile the target with
 * COPY windows (unchanged spans, referencing the source) + ADD windows (edited
 * bytes), so the patch is a few KB regardless of disc size.
 *
 * Uses only the RFC 3284 DEFAULT code table with two opcodes:
 *   1  = ADD, size 0 (size follows as an integer)
 *   19 = COPY, mode 0 (VCD_SELF), size 0 (size follows); address is absolute.
 * SELF-mode addresses are plain integers into [0,sourceLen)+target, so no
 * address cache is needed.
 *
 * Pure + isomorphic (no DOM): unit-tested in Node against real xdelta3.
 * Caveat: a synthesized patch has no source checksum — apply only to a pristine
 * disc. Exposed as global `VCDIFF` (classic script) and module export (Node).
 */
(function (root) {
  'use strict';

  // VCDIFF self-terminating variable-length integer: base-128, big-endian,
  // continuation bit (0x80) set on every byte except the last.
  function encInt(n) {
    if (n < 0) throw new Error('vcdiff int must be >= 0');
    const bytes = [n & 0x7f];
    n = Math.floor(n / 128);
    while (n > 0) { bytes.unshift((n & 0x7f) | 0x80); n = Math.floor(n / 128); }
    return bytes;
  }

  function concat(arrs) {
    let len = 0; for (const a of arrs) len += a.length;
    const out = new Uint8Array(len); let o = 0;
    for (const a of arrs) { out.set(a, o); o += a.length; }
    return out;
  }

  /* build a VCDIFF that turns `source` (length sourceLen) into the target that
   * equals source with `runs` applied. runs: [{pos, bytes}] (Uint8Array/array),
   * within [0,sourceLen), non-overlapping. targetLen defaults to sourceLen. */
  function build(sourceLen, runs, targetLen) {
    targetLen = targetLen == null ? sourceLen : targetLen;
    runs = runs.slice().sort((a, b) => a.pos - b.pos);

    const data = [];     // ADD/RUN data bytes
    const instr = [];    // instructions + inline sizes
    const addr = [];     // COPY addresses (SELF mode → absolute integers)

    const emitCopy = (size, address) => { if (size <= 0) return; instr.push(19); pushInt(instr, size); pushInt(addr, address); };
    const emitAdd = (bytes) => { instr.push(1); pushInt(instr, bytes.length); for (let i = 0; i < bytes.length; i++) data.push(bytes[i] & 0xff); };

    let cursor = 0;
    for (const r of runs) {
      const b = r.bytes;
      if (r.pos < cursor) throw new Error('overlapping/unsorted run at ' + r.pos);
      if (r.pos > cursor) emitCopy(r.pos - cursor, cursor);   // unchanged span from source
      if (b.length) emitAdd(b);
      cursor = r.pos + b.length;
    }
    if (cursor < targetLen) emitCopy(targetLen - cursor, cursor);

    const dataA = Uint8Array.from(data), instrA = Uint8Array.from(instr), addrA = Uint8Array.from(addr);

    // delta encoding (everything after the "length of delta encoding" field)
    const delta = concat([
      Uint8Array.from(encInt(targetLen)),   // size of the target window
      Uint8Array.from([0x00]),              // Delta_Indicator: no 2ndary compression
      Uint8Array.from(encInt(dataA.length)),
      Uint8Array.from(encInt(instrA.length)),
      Uint8Array.from(encInt(addrA.length)),
      dataA, instrA, addrA,
    ]);

    // window: VCD_SOURCE, whole source as the source segment
    const window = concat([
      Uint8Array.from([0x01]),              // Win_Indicator = VCD_SOURCE
      Uint8Array.from(encInt(sourceLen)),   // source segment length
      Uint8Array.from(encInt(0)),           // source segment position
      Uint8Array.from(encInt(delta.length)),// length of the delta encoding
      delta,
    ]);

    const header = Uint8Array.from([0xd6, 0xc3, 0xc4, 0x00, 0x00]); // 'VCD', ver 0, no indicators
    return concat([header, window]);
  }

  function pushInt(arr, n) { for (const b of encInt(n)) arr.push(b); }

  const api = { build, encInt };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.VCDIFF = api;
})(typeof self !== 'undefined' ? self : typeof globalThis !== 'undefined' ? globalThis : this);
