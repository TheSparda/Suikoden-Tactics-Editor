/* Suikoden Tactics — client-side ISO editor.
 *
 * A JS port of the desktop ISO editor (stpatch.py / stfields.py). The disc is
 * multi-GB, so — unlike the Save editor — this does NOT use Pyodide: it reads
 * only the bounded editable windows via File System Access `Blob.slice`, edits
 * them in memory, and writes back ONLY the changed byte-runs at their absolute
 * offsets with `createWritable({keepExistingData:true})`. Nothing is uploaded.
 *
 * Offsets are never guessed: character offsets come from st_characters.json,
 * table offsets from st_tables.json, and the record field schema is generated
 * from stfields.py into st_char_schema.json (one source of truth).
 *
 * Shares helpers ($, $$, clone, escapeHtml, openPicker, idb*) with app.js.
 */
'use strict';
(function () {
  const EDITOR_BASE = '../st-editor/';
  const SLUS_MARKER = [0x53, 0x4C, 0x55, 0x53, 0x5F, 0x32, 0x31, 0x32, 0x2E, 0x34, 0x35]; // "SLUS_212.45"
  const GAME_ID = 'SLUS-21245';
  const MERGE_GAP = 1 << 20;          // merge editable ranges within 1 MiB into one window
  const HAS_FS = 'showOpenFilePicker' in window;
  const RECORD_LEN = 0x280;

  const D = {                          // ISO state
    handle: null, name: '', fileSize: 0, chars: [], tables: {}, schema: null, maps: {},
    itemNames: {}, segs: [], loaded: false, view: 'chars', charIdx: 0, tableId: 0,
    edits: new Map(),                  // key "abs:w" -> {group,label,oldDisp,newDisp}
  };

  /* ------------------------------------------------------------ data load - */
  // no-cache: always revalidate (304 when unchanged) so edited data tables are
  // never served stale; still falls back to the SW cache when offline.
  async function loadJSON(url) { const r = await fetch(url, { cache: 'no-cache' }); if (!r.ok) throw new Error('fetch ' + url + ' → ' + r.status); return r.json(); }

  let dataPromise = null;
  function ensureData() {
    if (dataPromise) return dataPromise;
    dataPromise = (async () => {
      const base = EDITOR_BASE + 'data/';
      const [chars, tables, schema, weapon_types, runes, rune_orbs, skills, enemies, charNames, itemNames] =
        await Promise.all([
          loadJSON(base + 'st_characters.json'), loadJSON(base + 'st_tables.json'),
          loadJSON(base + 'st_char_schema.json'), loadJSON(base + 'st_weapon_types.json'),
          loadJSON(base + 'st_runes.json'), loadJSON(base + 'st_rune_orbs.json'),
          loadJSON(base + 'st_skills.json'), loadJSON(base + 'st_enemies.json'),
          loadJSON(base + 'st_character_names.json'), loadJSON(base + 'st_shop_items.json'),
        ]);
      // disambiguate duplicate character names (adult/child), mirroring stpatch.load_characters
      const seen = {};
      chars.forEach((c) => { if (seen[c.name]) { const note = (c.doc_note || '').trim().replace(/[()]/g, '') || 'alt'; c.name = `${c.name} (${note})`; } seen[c.name] = true; });
      D.chars = chars; D.tables = tables; D.schema = schema; D.itemNames = itemNames;
      D.maps = { weapon_types, runes, rune_orbs, skills, enemies, character_names: charNames };
      // optional: per-location shop stock, for best-effort shop identification
      try { D.shopLocs = await loadJSON(base + 'st_shop_locations.json'); } catch (e) { D.shopLocs = {}; }
    })();
    return dataPromise;
  }

  /* --------------------------------------------------------------- lookups - */
  const hex2 = (n) => n.toString(16).toUpperCase().padStart(2, '0');
  const hex4 = (n) => n.toString(16).toUpperCase().padStart(4, '0');
  function mapLabel(name, val) {
    const m = D.maps[name] || {};
    return m[hex2(val)] || m[hex4(val)] || `0x${hex2(val)} (?)`;
  }
  function rankLabel(v) { return D.schema.growth_ranks[String(v)] || `0x${hex2(v)} (?)`; }
  function elemLabel(v) { return D.schema.elements[String(v)] || `0x${hex2(v)} (?)`; }
  function itemName(id) { return D.itemNames[String(id).padStart(3, '0')] || ''; }

  // picker item lists from a hex-keyed name map (+ guide note for runes)
  function mapPickerList(name) {
    const m = D.maps[name] || {};
    return Object.keys(m).map((k) => ({ id: parseInt(k, 16), label: m[k], sub: '0x' + k,
      desc: name === 'runes' && typeof runeDesc === 'function' ? runeDesc(parseInt(k, 16)) : '' }))
      .sort((a, b) => a.id - b.id);
  }
  // guide note for a value, if the shared reference helpers are loaded
  function noteFor(kind, val) {
    if (kind === 'runes' && typeof runeDesc === 'function') return runeDesc(val);
    if (kind === 'item' && typeof itemDesc === 'function') return itemDesc(val);
    return '';
  }

  /* ---------------------------------------------------------- segment I/O -- */
  function collectRanges() {
    const r = [];
    D.chars.forEach((c) => { if (typeof c.offset === 'number') r.push([c.offset, c.offset + RECORD_LEN]); });
    Object.values(D.tables).forEach((t) => r.push([t.base, t.base + t.count * t.stride]));
    return r.sort((a, b) => a[0] - b[0]);
  }
  function mergeRanges(ranges) {
    const out = [];
    for (const [s, e] of ranges) {
      const last = out[out.length - 1];
      if (last && s - last[1] <= MERGE_GAP) last[1] = Math.max(last[1], e);
      else out.push([s, e]);
    }
    return out;
  }
  function segFor(abs) { return D.segs.find((s) => abs >= s.base && abs < s.end); }
  function rd(abs, w) { const s = segFor(abs); if (!s) throw new Error('offset 0x' + abs.toString(16) + ' outside editable region'); let v = 0; for (let i = 0; i < w; i++) v |= s.buf[abs - s.base + i] << (8 * i); return v >>> 0; }
  function rdOrig(abs, w) { const s = segFor(abs); let v = 0; for (let i = 0; i < w; i++) v |= s.orig[abs - s.base + i] << (8 * i); return v >>> 0; }

  /* -------- undo/redo: one write-hook + microtask-committed transaction ----
   * Every edit path funnels through wrBytes. It records each byte's before-image
   * into a pending map and schedules a commit on the next microtask, so one user
   * action = one undo step, with zero per-call-site wrapping. undo/redo restore
   * bytes directly (bypassing wrBytes) so they don't re-record. */
  const UNDO = [], REDO = [];
  let pending = null;
  function wrBytes(abs, bytes) {
    const s = segFor(abs); if (!s) return;
    if (!pending) { pending = new Map(); queueMicrotask(commitPending); }
    for (let i = 0; i < bytes.length; i++) {
      const a = abs + i, li = a - s.base;
      if (!pending.has(a)) pending.set(a, s.buf[li]);
      s.buf[li] = bytes[i] & 0xFF;
    }
  }
  function commitPending() {
    if (pending && pending.size) { UNDO.push(pending); REDO.length = 0; if (UNDO.length > 200) UNDO.shift(); updateUndoUI(); }
    pending = null;
  }
  function invertAndApply(step) {           // apply byte map directly, return inverse
    const inv = new Map();
    for (const [abs, val] of step) { const s = segFor(abs); if (!s) continue; const li = abs - s.base; inv.set(abs, s.buf[li]); s.buf[li] = val & 0xFF; }
    return inv;
  }
  function undo() { if (!UNDO.length) return; REDO.push(invertAndApply(UNDO.pop())); afterUndoRedo(); }
  function redo() { if (!REDO.length) return; UNDO.push(invertAndApply(REDO.pop())); afterUndoRedo(); }
  function afterUndoRedo() { rebuildEditsFromBuf(); rerender(); updateISOBadge(); updateUndoUI(); }
  function updateUndoUI() {
    const u = $('#isoUndo'), r = $('#isoRedo');
    if (u) u.disabled = !UNDO.length;
    if (r) r.disabled = !REDO.length;
  }
  function wrVal(abs, w, val) { const b = []; for (let i = 0; i < w; i++) b.push((val >> (8 * i)) & 0xFF); wrBytes(abs, b); }

  /* --------------------------------------------------------------- open ---- */
  function findMarker(u8) {
    for (let i = 0; i + SLUS_MARKER.length <= u8.length; i++) {
      let ok = true; for (let j = 0; j < SLUS_MARKER.length; j++) if (u8[i + j] !== SLUS_MARKER[j]) { ok = false; break; }
      if (ok) return i;
    }
    return -1;
  }

  async function openISO(handle) {
    const om = $('#isoOpenMsg');
    msg(om, 'Reading ISO…', 'info');
    try {
      await ensureData();
      const file = await handle.getFile();
      // version/region gate — SLUS marker in the first 2 MiB
      const head = new Uint8Array(await file.slice(0, 2 * 1024 * 1024).arrayBuffer());
      if (findMarker(head) < 0) {
        msg(om, `Could not find ${GAME_ID} (SLUS_212.45). This may not be the USA version of Suikoden Tactics.`, 'err');
        return;
      }
      // read only the bounded editable windows
      const windows = mergeRanges(collectRanges());
      D.segs = [];
      for (const [base, end] of windows) {
        const buf = new Uint8Array(await file.slice(base, end).arrayBuffer());
        D.segs.push({ base, end, buf, orig: buf.slice() });
      }
      // fingerprint — Kyril weapon-type byte must be 0x0E
      const kyril = D.chars.find((c) => c.name.toLowerCase() === 'kyril');
      if (kyril) {
        const wt = rd(kyril.offset + 0x0B, 1);
        if (wt !== 0x0E) {
          msg(om, `SLUS marker found, but the character-table fingerprint failed (Kyril weapon byte 0x${hex2(wt)}, expected 0x0E). Offsets don't match this dump.`, 'err');
          D.segs = []; return;
        }
      }
      D.handle = handle; D.name = file.name; D.fileSize = file.size; D.loaded = true; D.edits.clear();
      UNDO.length = 0; REDO.length = 0; updateUndoUI();
      const totalKB = Math.round(D.segs.reduce((n, s) => n + s.buf.length, 0) / 1024);
      msg(om, `Opened ${escapeHtml(file.name)} — ${GAME_ID} verified, ${D.segs.length} region(s), ${totalKB} KB mapped.`, 'ok');
      try { await idbSet('lastISO', { name: file.name, handle }); $('#isoLastName').textContent = file.name; $('#isoLastChip').classList.remove('hide'); } catch (e) {}
      $('#isoBody').classList.remove('hide');
      $('#isoActionbar').classList.remove('hide');
      buildViewTabs();
      setView(D.view);
      updateISOBadge();
    } catch (e) { msg(om, 'Open failed: ' + (e && e.message || e), 'err'); console.error(e); }
  }

  /* --------------------------------------------------------- edit tracking - */
  function editKey(abs, w) { return abs + ':' + w; }
  function reconcileEdit(abs, w, group, label, fmt) {
    const cur = rd(abs, w), orig = rdOrig(abs, w), k = editKey(abs, w);
    if (cur === orig) D.edits.delete(k);
    else D.edits.set(k, { group, label, oldDisp: fmt(orig), newDisp: fmt(cur) });
    updateISOBadge();
  }
  function isoDirtyCount() { return D.edits.size; }

  let isoBadgeRAF = 0;
  function updateISOBadge() {
    if (isoBadgeRAF) return;
    isoBadgeRAF = requestAnimationFrame(() => {
      isoBadgeRAF = 0;
      const n = isoDirtyCount();
      const bs = $('#isoBarStatus'); if (bs) bs.textContent = n ? `${n} unsaved change${n === 1 ? '' : 's'}` : 'No unsaved changes';
      // reflect in the shared header badge when ISO mode is active
      if (!$('#isoMode').classList.contains('hide')) {
        const b = $('#unsaved'); b.textContent = n ? `${n} unsaved` : ''; b.classList.toggle('hide', !n);
      }
      $('#isoApplyBtn').classList.toggle('has-dirty', n > 0);
    });
  }

  /* ---------------------------------------------------------------- views -- */
  function buildViewTabs() {
    const tabs = [
      ['chars', 'Characters'], ['items', 'Items'], ['shop', 'Shops'], ['runeshop', 'Rune shops'],
      ['itemprice', 'Prices'], ['skills', 'Skills'], ['enemies', 'Enemies'], ['ref', 'Reference'],
    ].filter(([k]) => k === 'chars' || k === 'ref' || D.tables[k]);
    $('#isoViewTabs').innerHTML = tabs.map(([k, l]) =>
      `<button class="viewtab${k === D.view ? ' active' : ''}" data-view="${k}">${l}</button>`).join('');
    $$('#isoViewTabs .viewtab').forEach((b) => b.onclick = () => setView(b.dataset.view));
  }
  function setView(v) {
    D.view = v;
    $$('#isoViewTabs .viewtab').forEach((b) => b.classList.toggle('active', b.dataset.view === v));
    if (v === 'chars') renderChars();
    else if (v === 'ref') renderReference();
    else renderTable(v);
  }

  // a labeled field control with dirty highlight + revert
  function fieldRow(label, absOff, w, group, control) { return { label, absOff, w, group, control }; }

  function numControl(abs, w, group, label, min, max, fmt) {
    const d = document.createElement('div'); d.className = 'fld';
    const dirty = rd(abs, w) !== rdOrig(abs, w);
    d.innerHTML = `<label>${escapeHtml(label)}${dirty ? '<button class="revert" title="revert" type="button">↺</button>' : ''}</label>
      <input type="number" min="${min}" max="${max}" value="${rd(abs, w)}">`;
    if (dirty) d.classList.add('chg');
    const inp = d.querySelector('input');
    inp.oninput = () => {
      let v = parseInt(inp.value, 10); if (isNaN(v)) v = 0; v = Math.max(min, Math.min(max, v));
      wrVal(abs, w, v);
      d.classList.toggle('chg', rd(abs, w) !== rdOrig(abs, w));
      reconcileEdit(abs, w, group, label, fmt || String);
    };
    if (dirty) d.querySelector('.revert').onclick = () => { wrVal(abs, w, rdOrig(abs, w)); reconcileEdit(abs, w, group, label, fmt || String); rerender(); };
    return d;
  }

  function selectControl(abs, w, group, label, options, fmt) {
    const d = document.createElement('div'); d.className = 'fld';
    const cur = rd(abs, w), dirty = cur !== rdOrig(abs, w);
    const opts = options.map((o) => `<option value="${o.v}"${o.v === cur ? ' selected' : ''}>${escapeHtml(o.t)}</option>`).join('');
    d.innerHTML = `<label>${escapeHtml(label)}${dirty ? '<button class="revert" title="revert" type="button">↺</button>' : ''}</label><select>${opts}</select>`;
    if (dirty) d.classList.add('chg');
    const sel = d.querySelector('select');
    sel.onchange = () => { wrVal(abs, w, parseInt(sel.value, 10) || 0); d.classList.toggle('chg', rd(abs, w) !== rdOrig(abs, w)); reconcileEdit(abs, w, group, label, fmt); };
    if (dirty) d.querySelector('.revert').onclick = () => { wrVal(abs, w, rdOrig(abs, w)); reconcileEdit(abs, w, group, label, fmt); rerender(); };
    return d;
  }

  function pickerControl(abs, w, group, label, mapName, fmt) {
    const d = document.createElement('div'); d.className = 'fld';
    const cur = rd(abs, w), dirty = cur !== rdOrig(abs, w);
    const note = noteFor(mapName, cur);
    d.innerHTML = `<label>${escapeHtml(label)}${dirty ? '<button class="revert" title="revert" type="button">↺</button>' : ''}</label>
      <button class="pick" type="button">${escapeHtml(fmt(cur))}</button>${note ? `<div class="fnote">${escapeHtml(note)}</div>` : ''}`;
    if (dirty) d.classList.add('chg');
    d.querySelector('.pick').onclick = () => openPicker({
      title: `${label}`, items: mapPickerList(mapName), current: cur, allowEmpty: true,
      onPick: (id) => { wrVal(abs, w, id); reconcileEdit(abs, w, group, label, fmt); rerender(); },
    });
    if (dirty) d.querySelector('.revert').onclick = () => { wrVal(abs, w, rdOrig(abs, w)); reconcileEdit(abs, w, group, label, fmt); rerender(); };
    return d;
  }

  // picker over an explicit item list (each row can carry a description),
  // like the rune picker — used for growth ranks so the special curves explain
  // themselves in the dropdown.
  function listPickerControl(abs, w, group, label, items, fmt) {
    const d = document.createElement('div'); d.className = 'fld';
    const cur = rd(abs, w), dirty = cur !== rdOrig(abs, w);
    d.innerHTML = `<label>${escapeHtml(label)}${dirty ? '<button class="revert" title="revert" type="button">↺</button>' : ''}</label>
      <button class="pick" type="button">${escapeHtml(fmt(cur))}</button>`;
    if (dirty) d.classList.add('chg');
    d.querySelector('.pick').onclick = () => openPicker({
      title: label, items, current: cur, allowEmpty: false,
      onPick: (id) => { wrVal(abs, w, id); reconcileEdit(abs, w, group, label, fmt); rerender(); },
    });
    if (dirty) d.querySelector('.revert').onclick = () => { wrVal(abs, w, rdOrig(abs, w)); reconcileEdit(abs, w, group, label, fmt); rerender(); };
    return d;
  }

  function rerender() { setView(D.view); }

  function grid(children) { const g = document.createElement('div'); g.className = 'grid'; children.forEach((c) => g.appendChild(c)); return g; }
  function section(title) { const h = document.createElement('h3'); h.className = 'sub-h'; h.textContent = title; return h; }

  function renderChars() {
    const host = $('#isoView'); host.innerHTML = '';
    const c = D.chars[D.charIdx];
    const sel = document.createElement('div'); sel.className = 'row';
    sel.innerHTML = `<div style="flex:1;min-width:220px"><label>Character</label>
      <select id="isoCharSel">${D.chars.map((ch, i) => `<option value="${i}"${i === D.charIdx ? ' selected' : ''}>${escapeHtml(ch.name)}</option>`).join('')}</select></div>
      <div class="muted" style="align-self:end">record @0x${c.offset.toString(16).toUpperCase()}</div>`;
    host.appendChild(sel);
    sel.querySelector('#isoCharSel').onchange = (e) => { D.charIdx = +e.target.value; renderChars(); };

    const base = c.offset, F = D.schema.fields;

    // presets — staged like any edit (reviewable + undoable via the write hook)
    const pbar = document.createElement('div'); pbar.className = 'row'; pbar.style.margin = '8px 0';
    pbar.innerHTML = `<button class="ghost" data-p="maxgrowth">Max growth (SS)</button>
      <button class="ghost" data-p="maxskills">Max all skills</button>
      <button class="ghost" data-p="clearskills">Clear skills</button>`;
    host.appendChild(pbar);
    const afterPreset = () => { rebuildEditsFromBuf(); rerender(); updateISOBadge(); };
    pbar.querySelector('[data-p=maxgrowth]').onclick = () => { F.filter((f) => f.kind === 'rank').forEach((f) => wrVal(base + f.offset, f.w, 0x08)); afterPreset(); };
    pbar.querySelector('[data-p=maxskills]').onclick = () => { F.filter((f) => f.kind === 'skillrank').forEach((f) => wrVal(base + f.offset, f.w, D.schema.skill_block.rank_max)); afterPreset(); };
    pbar.querySelector('[data-p=clearskills]').onclick = () => { F.filter((f) => f.kind === 'skillrank').forEach((f) => wrVal(base + f.offset, f.w, 0)); afterPreset(); };
    const G = (kind) => F.filter((f) => f.kind === kind);
    const grp = (name) => 'char:' + c.name + ':' + name;

    // Growth ranks — searchable picker with per-rank descriptions (incl. the
    // "special" non-linear growth curves).
    host.appendChild(section('Growth ranks'));
    const notes = D.schema.growth_rank_notes || {};
    const rankItems = Object.keys(D.schema.growth_ranks).map((k) => ({ id: +k, label: rankLabel(+k), desc: notes[k] || '' }))
      .sort((a, b) => a.id - b.id);
    host.appendChild(grid(G('rank').map((f) => listPickerControl(base + f.offset, f.w, grp('growth'), f.label, rankItems, rankLabel))));

    // Weapon
    host.appendChild(section('Weapon'));
    const wt = F.find((f) => f.key === 'weapon_type');
    const wchildren = [pickerControl(base + wt.offset, wt.w, grp('weapon'), wt.label, 'weapon_types', (v) => mapLabel('weapon_types', v))];
    const wg = D.schema.weapon_growth;
    for (let lv = 0; lv < wg.levels; lv++) wchildren.push(numControl(base + wg.offset + lv, 1, grp('weapon'), `Wpn growth Lv${lv + 1}`, 0, 255));
    host.appendChild(grid(wchildren));

    // Runes + Element
    host.appendChild(section('Runes & element'));
    const re = [];
    F.filter((f) => f.kind === 'enum' && f.data === 'runes').forEach((f) => re.push(pickerControl(base + f.offset, f.w, grp('runes'), f.label, 'runes', (v) => mapLabel('runes', v))));
    const el = F.find((f) => f.kind === 'element');
    if (el) re.push(selectControl(base + el.offset, el.w, grp('element'), el.label, Object.keys(D.schema.elements).map((k) => ({ v: +k, t: elemLabel(+k) })), elemLabel));
    const mv = F.find((f) => f.key === 'move'); if (mv) re.push(numControl(base + mv.offset, 1, grp('misc'), mv.label, 0, 255));
    host.appendChild(grid(re));

    // Skills — affinity uses the game's rank ladder (HexEditing.rtf: 01=F..08=SS;
    // guide confirms skill levels are lettered E..S). 0 = off, 1..7 = F..S.
    // Searchable picker with proficiency notes, like the growth ranks.
    host.appendChild(section('Battle skill affinities (rank the character has)'));
    const affNote = (k) => {
      const L = rankLabel(k);
      return L === 'F' ? 'Proficiency F — lowest.' : L === 'S' ? 'Proficiency S — highest.' : 'Proficiency ' + L + '.';
    };
    const affItems = [{ id: 0, label: 'off', desc: "Not learned — the character can't use this skill." }].concat(
      Object.keys(D.schema.growth_ranks).map(Number).filter((k) => k >= 1 && k <= D.schema.skill_block.rank_max)
        .sort((a, b) => a - b).map((k) => ({ id: k, label: rankLabel(k), desc: affNote(k) })));
    const skillFmt = (v) => (v ? rankLabel(v) : 'off');
    host.appendChild(grid(G('skillrank').map((f) => listPickerControl(base + f.offset, f.w, grp('skills'), f.label, affItems, skillFmt))));

    // Extra u16 (unconfirmed) — collapsible
    const extras = G('u16');
    if (extras.length) {
      const det = document.createElement('details'); det.style.marginTop = '12px';
      det.innerHTML = `<summary style="color:var(--muted);cursor:pointer">Advanced: unconfirmed u16 fields (${extras.length})</summary>`;
      det.appendChild(grid(extras.map((f) => numControl(base + f.offset, f.w, grp('extra'), f.label, 0, 65535))));
      host.appendChild(det);
    }
  }

  // Record-id → English name. Name maps are 3-digit DECIMAL-keyed and the table
  // record index equals the name id (RE-verified: itemprice rec_id 1 = Medicine,
  // rec_id 2 = Mega Medicine). Shops/rune-shops have no name table.
  function decName(mapName, id) { const m = D.maps[mapName] || {}; return m[String(id).padStart(3, '0')] || m[String(id)] || ''; }
  function recordName(name, id) {
    if (name === 'items' || name === 'itemprice') return itemName(id);
    if (name === 'skills') return decName('skills', id);
    if (name === 'enemies') return decName('enemies', id);
    return '';
  }
  function tableRowName(name, id) { const n = recordName(name, id); return n ? `${id} — ${n}` : `${id}`; }

  // A table field that holds an item / rune-orb ID → render a searchable named
  // picker instead of a raw number. Detected from the (V11-recovered) label:
  // "1st item"/"Dropped item #1" → item; "1st Orb" → rune orb. Percentage /
  // price fields that merely contain "item" are excluded.
  function fieldRef(label) {
    if (/chance|%|price/i.test(label)) return null;
    if (/\borb\b/i.test(label)) return 'orb';
    if (/\bitem\b/i.test(label)) return 'item';
    return null;
  }
  function refItems(ref) {
    if (ref === 'orb') return mapPickerList('rune_orbs');
    return (typeof ITEMS !== 'undefined') ? ITEMS : [];
  }
  function refLabel(ref, v) {
    if (!v) return '— none —';
    if (ref === 'orb') return mapLabel('rune_orbs', v);
    const n = typeof itemName === 'function' ? itemName(v) : '';
    return n ? `${n} [${v}]` : `unknown [${v}]`;
  }
  function tblPicker(abs, w, group, label, ref) {
    const d = document.createElement('div'); d.className = 'fld';
    const cur = rd(abs, w), dirty = cur !== rdOrig(abs, w);
    const fmt = (v) => refLabel(ref, v);
    d.innerHTML = `<label>${escapeHtml(label)}${dirty ? '<button class="revert" title="revert" type="button">↺</button>' : ''}</label>
      <button class="pick" type="button">${escapeHtml(fmt(cur))}</button>`;
    if (dirty) d.classList.add('chg');
    d.querySelector('.pick').onclick = () => openPicker({
      title: label, items: refItems(ref), current: cur, allowEmpty: true,
      onPick: (id) => { wrVal(abs, w, id); reconcileEdit(abs, w, group, label, fmt); rerender(); },
    });
    if (dirty) d.querySelector('.revert').onclick = () => { wrVal(abs, w, rdOrig(abs, w)); reconcileEdit(abs, w, group, label, fmt); rerender(); };
    return d;
  }

  function renderTable(name) {
    const host = $('#isoView'); host.innerHTML = '';
    const t = D.tables[name]; if (!t) { host.textContent = 'No such table.'; return; }
    if (D.tableId >= t.count) D.tableId = 0;
    const sel = document.createElement('div'); sel.className = 'row';
    sel.innerHTML = `<div style="flex:1;min-width:220px"><label>${name} record</label>
      <select id="isoTblSel">${Array.from({ length: t.count }, (_, i) => `<option value="${i}"${i === D.tableId ? ' selected' : ''}>${escapeHtml(tableRowName(name, i))}</option>`).join('')}</select></div>
      <div class="muted" style="align-self:end">${recordName(name, D.tableId) ? escapeHtml(recordName(name, D.tableId)) + ' · ' : ''}@0x${(t.base + D.tableId * t.stride).toString(16).toUpperCase()} · stride 0x${t.stride.toString(16).toUpperCase()}</div>`;
    host.appendChild(sel);
    sel.querySelector('#isoTblSel').onchange = (e) => { D.tableId = +e.target.value; renderTable(name); };

    // guide description for item / price records
    if ((name === 'items' || name === 'itemprice') && typeof itemDesc === 'function') {
      const nd = itemDesc(D.tableId);
      if (nd) { const p = document.createElement('div'); p.className = 'fnote'; p.style.marginBottom = '6px'; p.textContent = nd; host.appendChild(p); }
    }
    // what the skills values mean (per the V11 editor + Suikoden Tactics guides)
    if (name === 'skills') {
      const p = document.createElement('div'); p.className = 'fnote'; p.style.marginBottom = '6px';
      p.textContent = 'Each skill has ranks E→S. "How strong" = effect strength at that rank (higher = stronger); "secondary" = a second effect some skills add; "upgrade SP cost" = Skill Points needed to raise the skill to that rank.';
      host.appendChild(p);
    }

    const recOff = t.base + D.tableId * t.stride;
    const grp = `table:${name}:${D.tableId}`;

    // best-effort shop identification: match this record's live item list against
    // each location's guide stock (the shop table has no name field).
    if (name === 'shop' && D.shopLocs && Object.keys(D.shopLocs).length) {
      const have = new Set();
      t.fields.forEach((f) => { if (fieldRef(f.label) === 'item') { const v = rd(recOff + f.off, f.w); if (v) have.add(v); } });
      if (have.size) {
        let best = null;
        for (const [loc, ids] of Object.entries(D.shopLocs)) {
          let hit = 0; for (const v of have) if (ids.includes(v)) hit++;
          const frac = hit / have.size;
          if (!best || frac > best.frac) best = { loc, frac };
        }
        if (best && best.frac >= 0.5) {
          const p = document.createElement('div'); p.className = 'fnote'; p.style.marginBottom = '6px';
          p.textContent = `Likely ${best.loc}'s shop (${Math.round(best.frac * 100)}% of its ${have.size} items match this location's guide stock — inferred).`;
          host.appendChild(p);
        }
      }
    }

    host.appendChild(grid(t.fields.map((f) => {
      const ref = fieldRef(f.label);
      if (ref) return tblPicker(recOff + f.off, f.w, grp, f.label, ref);
      return numControl(recOff + f.off, f.w, grp, f.label, 0, (1 << (8 * f.w)) - 1);
    })));
  }

  // hexKeys: keys are hex (runes/weapon/orbs) → show 0xNN; else decimal id.
  function refTable(title, map, hexKeys) {
    const radix = hexKeys ? 16 : 10;
    const rows = Object.keys(map).sort((a, b) => parseInt(a, radix) - parseInt(b, radix))
      .map((k) => `<tr><td>${hexKeys ? '0x' + k : parseInt(k, 10)}</td><td>${escapeHtml(map[k])}</td></tr>`).join('');
    return `<details><summary style="color:var(--gold);cursor:pointer;font-weight:600">${title} (${Object.keys(map).length})</summary>
      <table style="margin-top:6px"><thead><tr><th>ID</th><th>Name</th></tr></thead><tbody>${rows}</tbody></table></details>`;
  }
  function renderReference() {
    const host = $('#isoView'); host.innerHTML = '<small class="muted">Read-only id → name lists used while editing.</small>';
    const wrap = document.createElement('div');
    wrap.innerHTML =
      refTable('Items', D.itemNames, false) +
      refTable('Skills', D.maps.skills, false) +
      refTable('Enemies', D.maps.enemies, false) +
      refTable('Runes', D.maps.runes, true) +
      refTable('Weapon types', D.maps.weapon_types, true) +
      refTable('Rune orbs', D.maps.rune_orbs, true) +
      refTable('Characters', D.maps.character_names, false);
    host.appendChild(wrap);
  }

  /* ------------------------------------------------------- diff / write ---- */
  function diffRuns() {
    const runs = [];
    for (const s of D.segs) {
      let i = 0;
      while (i < s.buf.length) {
        if (s.buf[i] !== s.orig[i]) { let j = i; while (j < s.buf.length && s.buf[j] !== s.orig[j]) j++; runs.push({ abs: s.base + i, cur: s.buf.slice(i, j), old: s.orig.slice(i, j) }); i = j; }
        else i++;
      }
    }
    return runs;
  }

  function openISOReview() {
    if (!D.loaded) return;
    if (!D.edits.size) { msg($('#isoOpenMsg'), 'No ISO changes to save.', 'info'); return; }
    const groups = {};
    for (const e of D.edits.values()) { (groups[e.group] = groups[e.group] || []).push(e); }
    let html = '';
    for (const g of Object.keys(groups)) {
      const title = g.replace(/^char:/, '').replace(/^table:/, '').replace(/:/g, ' · ');
      html += `<h4>${escapeHtml(title)}</h4><table class="rev">` +
        groups[g].map((e) => `<tr><td>${escapeHtml(e.label)}</td><td class="old">${escapeHtml(e.oldDisp)}</td><td class="arrow">→</td><td class="new">${escapeHtml(e.newDisp)}</td></tr>`).join('') + '</table>';
    }
    const bytes = diffRuns().reduce((n, r) => n + r.cur.length, 0);
    $('#isoReviewBody').innerHTML = html + `<p class="muted">${D.edits.size} field(s), ${bytes} byte(s) will be written to <b>${escapeHtml(D.name)}</b>.</p>`;
    $('#isoReviewModal').classList.remove('hide');
  }
  function closeISOReview() { $('#isoReviewModal').classList.add('hide'); }

  function isoProgress(pct, m) { $('#isoProgBar').style.width = Math.max(0, Math.min(100, pct)) + '%'; if (m) $('#isoProgMsg').textContent = m; }

  async function saveISO() {
    closeISOReview();
    const runs = diffRuns();
    if (!runs.length) return;
    $('#isoProgTitle').textContent = 'Saving to ISO…';
    $('#isoProgress').classList.remove('hide'); isoProgress(0, 'Requesting write permission…');
    try {
      const perm = D.handle.requestPermission ? await D.handle.requestPermission({ mode: 'readwrite' }) : 'granted';
      if (perm !== 'granted') throw new Error('write permission denied');
      const w = await D.handle.createWritable({ keepExistingData: true });
      for (let i = 0; i < runs.length; i++) {
        await w.write({ type: 'write', position: runs[i].abs, data: runs[i].cur });
        isoProgress(Math.round(((i + 1) / runs.length) * 100), `Writing run ${i + 1}/${runs.length}…`);
      }
      isoProgress(100, 'Finalizing…');
      await w.close();
      D.segs.forEach((s) => { s.orig = s.buf.slice(); });
      D.edits.clear(); updateISOBadge(); rerender();
      $('#isoProgress').classList.add('hide');
      msg($('#isoOpenMsg'), `Saved ${runs.length} change-run(s) to ${escapeHtml(D.name)}.`, 'ok');
    } catch (e) {
      $('#isoProgress').classList.add('hide');
      msg($('#isoOpenMsg'), 'Save failed: ' + (e && e.message || e), 'err'); console.error(e);
    }
  }

  /* -------------------------------------------------------------- recipe --- */
  const toHex = (u8) => [...u8].map((b) => b.toString(16).padStart(2, '0')).join('');
  const fromHex = (h) => new Uint8Array(h.match(/.{2}/g).map((x) => parseInt(x, 16)));

  function exportRecipe() {
    const runs = diffRuns();
    if (!runs.length) { msg($('#isoOpenMsg'), 'No changes to export.', 'info'); return; }
    const recipe = { format: 'stmod', version: 1, game: GAME_ID, patchCount: runs.length,
      patches: runs.map((r) => ({ off: r.abs, old: toHex(r.old), new: toHex(r.cur) })) };
    const blob = new Blob([JSON.stringify(recipe, null, 1)], { type: 'application/json' });
    const url = URL.createObjectURL(blob); const a = document.createElement('a');
    const stem = D.name.replace(/\.[^.]+$/, '') || 'suikoden-tactics';
    a.href = url; a.download = `${stem}.stmod`; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
    msg($('#isoOpenMsg'), `Exported recipe (${runs.length} patch-run(s)).`, 'ok');
  }

  // Export a standard .xdelta (VCDIFF) patch, synthesized from the staged edits
  // (no full-disc diff). Apply to a PRISTINE disc — a built patch has no checksum.
  function exportXdelta() {
    if (typeof VCDIFF === 'undefined') { msg($('#isoOpenMsg'), 'Patch encoder not loaded.', 'err'); return; }
    const runs = diffRuns();
    if (!runs.length) { msg($('#isoOpenMsg'), 'No changes to export.', 'info'); return; }
    const patch = VCDIFF.build(D.fileSize, runs.map((r) => ({ pos: r.abs, bytes: r.cur })), D.fileSize);
    const blob = new Blob([patch], { type: 'application/octet-stream' });
    const url = URL.createObjectURL(blob); const a = document.createElement('a');
    const stem = D.name.replace(/\.[^.]+$/, '') || 'suikoden-tactics';
    a.href = url; a.download = `${stem}.xdelta`; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
    msg($('#isoOpenMsg'), `Exported ${stem}.xdelta (${patch.length} bytes). Apply to a pristine disc: xdelta3 -d -s disc.iso ${stem}.xdelta out.iso`, 'ok');
  }

  async function importRecipe(file) {
    if (!D.loaded) { msg($('#isoOpenMsg'), 'Open an ISO first, then import a recipe onto it.', 'err'); return; }
    let recipe;
    try { recipe = JSON.parse(await file.text()); } catch (e) { msg($('#isoOpenMsg'), 'Not a valid recipe file.', 'err'); return; }
    if (recipe.format !== 'stmod') { msg($('#isoOpenMsg'), 'Not an .stmod recipe.', 'err'); return; }
    if (recipe.game && recipe.game !== GAME_ID) { msg($('#isoOpenMsg'), `Recipe is for ${recipe.game}, not ${GAME_ID}.`, 'err'); return; }
    let applied = 0, mismatch = 0, outside = 0;
    for (const p of recipe.patches || []) {
      const cur = fromHex(p.new), old = p.old ? fromHex(p.old) : null;
      if (!segFor(p.off) || !segFor(p.off + cur.length - 1)) { outside++; continue; }
      if (old) { for (let i = 0; i < old.length; i++) if (rd(p.off + i, 1) !== old[i]) { mismatch++; break; } }
      wrBytes(p.off, cur); applied++;
    }
    // rebuild the edit log from the new buf vs orig so review/badge reflect it
    rebuildEditsFromBuf();
    rerender(); updateISOBadge();
    let m = `Imported ${applied} patch-run(s).`;
    if (mismatch) m += ` ⚠ ${mismatch} did not match the recipe's recorded original bytes (wrong revision?).`;
    if (outside) m += ` ${outside} fell outside the editable region and were skipped.`;
    msg($('#isoOpenMsg'), m, mismatch ? 'err' : 'ok');
  }

  // After a recipe import, reconstruct the labeled edit log by re-decoding known
  // fields whose current bytes differ from orig (so review + badge are populated).
  function rebuildEditsFromBuf() {
    D.edits.clear();
    // characters
    D.chars.forEach((c) => {
      const base = c.offset, grp = 'char:' + c.name + ':import';
      D.schema.fields.forEach((f) => {
        const abs = base + f.offset;
        if (!segFor(abs)) return;
        if (rd(abs, f.w) !== rdOrig(abs, f.w)) D.edits.set(editKey(abs, f.w), { group: grp, label: f.label, oldDisp: String(rdOrig(abs, f.w)), newDisp: String(rd(abs, f.w)) });
      });
    });
    // tables
    Object.keys(D.tables).forEach((name) => {
      const t = D.tables[name];
      for (let id = 0; id < t.count; id++) {
        const recOff = t.base + id * t.stride, grp = `table:${name}:${id}`;
        t.fields.forEach((f) => {
          const abs = recOff + f.off;
          if (!segFor(abs)) return;
          if (rd(abs, f.w) !== rdOrig(abs, f.w)) D.edits.set(editKey(abs, f.w), { group: grp, label: f.label, oldDisp: String(rdOrig(abs, f.w)), newDisp: String(rd(abs, f.w)) });
        });
      }
    });
  }

  /* ----------------------------------------------------------------- init -- */
  function switchMode(mode) {
    const iso = mode === 'iso';
    $('#saveMode').classList.toggle('hide', iso);
    $('#isoMode').classList.toggle('hide', !iso);
    $$('#modeTabs .modetab').forEach((b) => b.classList.toggle('active', b.dataset.mode === mode));
    // action bars
    $('#actionbar').classList.toggle('hide', iso || $('#body').classList.contains('hide'));
    $('#isoActionbar').classList.toggle('hide', !iso || !D.loaded);
    // header badge reflects the active mode
    if (iso) updateISOBadge();
    else if (typeof updateBadge === 'function') updateBadge();
    if (iso && !HAS_FS) { $('#isoBlocked').classList.remove('hide'); $('#isoLoader').classList.add('hide'); }
  }

  function initISO() {
    // mode tabs
    $$('#modeTabs .modetab').forEach((b) => b.onclick = () => switchMode(b.dataset.mode));

    if (!HAS_FS) {
      $('#isoBlocked').classList.remove('hide');
      $('#isoLoader').classList.add('hide');
    } else {
      $('#isoOpenBtn').onclick = async () => {
        try {
          await ensureData();
          const [h] = await showOpenFilePicker({ types: [{ description: 'PS2 disc image', accept: { 'application/octet-stream': ['.iso', '.bin', '.img'] } }] });
          await openISO(h);
        } catch (e) { if (e && e.name !== 'AbortError') { msg($('#isoOpenMsg'), 'Could not open: ' + (e.message || e), 'err'); console.error(e); } }
      };
      // last-ISO chip (handle only — never the multi-GB bytes)
      idbGet('lastISO').then((last) => { if (last && last.name) { $('#isoLastName').textContent = last.name; $('#isoLastChip').classList.remove('hide'); } });
      $('#isoLastOpen').onclick = async () => {
        const last = await idbGet('lastISO'); if (!last || !last.handle) return;
        try {
          const perm = last.handle.queryPermission ? await last.handle.queryPermission({ mode: 'readwrite' }) : 'granted';
          if (perm !== 'granted' && last.handle.requestPermission) await last.handle.requestPermission({ mode: 'readwrite' });
          await ensureData(); await openISO(last.handle);
        } catch (e) { msg($('#isoOpenMsg'), 'Could not reopen: ' + (e.message || e), 'err'); }
      };
      $('#isoLastForget').onclick = async (e) => { e.stopPropagation(); await idbDel('lastISO'); $('#isoLastChip').classList.add('hide'); };
    }

    $('#isoApplyBtn').onclick = openISOReview;
    $('#isoReviewCancel').onclick = closeISOReview;
    $('#isoReviewCancel2').onclick = closeISOReview;
    $('#isoReviewConfirm').onclick = saveISO;
    $('#isoReviewModal').addEventListener('click', (e) => { if (e.target.id === 'isoReviewModal') closeISOReview(); });
    $('#isoRecipeExport').onclick = exportRecipe;
    $('#isoXdeltaExport').onclick = exportXdelta;
    $('#isoRecipeImport').onclick = () => $('#isoRecipeFile').click();
    $('#isoRecipeFile').onchange = () => { if ($('#isoRecipeFile').files[0]) importRecipe($('#isoRecipeFile').files[0]); $('#isoRecipeFile').value = ''; };

    // undo / redo
    $('#isoUndo').onclick = undo;
    $('#isoRedo').onclick = redo;
    updateUndoUI();
    document.addEventListener('keydown', (e) => {
      if ($('#isoMode').classList.contains('hide')) return;
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === 'z' && !e.shiftKey) { e.preventDefault(); undo(); }
      else if (mod && (e.key.toLowerCase() === 'y' || (e.key.toLowerCase() === 'z' && e.shiftKey))) { e.preventDefault(); redo(); }
    });

    // unsaved guard for ISO edits
    window.addEventListener('beforeunload', (e) => { if (D.loaded && isoDirtyCount() > 0) { e.preventDefault(); e.returnValue = ''; } });

    // prefetch data tables in the background so the first open is instant
    ensureData().catch(() => {});
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initISO);
  else initISO();
})();
