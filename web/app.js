/* Suikoden Tactics — client-side web save editor.
 *
 * Runs the desktop editor's Python save modules UNCHANGED inside Pyodide
 * (CPython → WebAssembly). The picked save is written to /save.bin in Pyodide's
 * in-memory FS, decoded/edited by the same verified field map the desktop uses,
 * and read back out as a download / save-in-place / share. Nothing is uploaded.
 *
 * Enhanced to the Suikoden III web-editor playbook: searchable pickers,
 * review-changes confirmation, per-field dirty highlight + revert, unsaved
 * badge + beforeunload guard, staged boot progress, IndexedDB last-opened,
 * Web Share (in + out), install button, and File System Access save-in-place.
 */
'use strict';

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const clone = (o) => JSON.parse(JSON.stringify(o));

// Python modules + data copied from the desktop editor into Pyodide's FS.
const PYPKG = ['stsaveio.py', 'stsaveedit.py', 'stsave.py', 'stsavefields.py'];
const PYDATA = ['st_ram_party_map.json'];      // needed by stsavefields.roster()
const EDITOR_BASE = '../st-editor/';           // desktop module folder (repo layout)

let PY = null;      // resolved pyodide instance (sync access after boot)
let pyReady = null; // boot promise
let GLUE = null;    // st_glue python module proxy
let LISTS = null;   // { shop_items, runes } raw maps
let ITEMS = [];     // [{id,label,sub}] sorted, for the item picker
let RUNES = [];     // [{id,label,sub}] sorted, for the rune picker
let SVD = null;     // live, editable decoded save state
let BASE = null;    // pristine baseline (for diff / dirty / revert)
let META = null;    // { crc_ok, md5_ok, is_card, can_sps, card_saves, folder, ... }
let CURRENT_NAME = '';
let CURRENT_HANDLE = null;   // FileSystemFileHandle when opened via FS Access
let CURRENT_EXT = '';        // original file extension (lower, no dot)
let deferredInstall = null;  // captured beforeinstallprompt

/* ---------------------------------------------------------------- theme --- */
function applyTheme(t) {
  document.body.dataset.theme = t;
  const meta = $('#themeColor');
  if (meta) meta.setAttribute('content', t === 'light' ? '#f5efe1' : '#1a1113');
  try { localStorage.setItem('st_theme', t); } catch (e) {}
}
applyTheme(localStorage.getItem('st_theme') || 'dark');
$('#themeBtn').onclick = () => applyTheme(document.body.dataset.theme === 'dark' ? 'light' : 'dark');

function msg(el, m, kind) { el.textContent = m; el.className = 'msg ' + (kind || 'info'); }
function escapeHtml(s) { return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

/* ---------------------------------------------------------- IndexedDB kv -- */
// Tiny promise wrapper — stores last-opened save (bytes + name + optional handle).
function idb() {
  return new Promise((res, rej) => {
    const r = indexedDB.open('st-editor', 1);
    r.onupgradeneeded = () => r.result.createObjectStore('kv');
    r.onsuccess = () => res(r.result);
    r.onerror = () => rej(r.error);
  });
}
async function idbSet(k, v) {
  try { const db = await idb(); await new Promise((res, rej) => { const tx = db.transaction('kv', 'readwrite'); tx.objectStore('kv').put(v, k); tx.oncomplete = res; tx.onerror = () => rej(tx.error); }); } catch (e) { /* best-effort */ }
}
async function idbGet(k) {
  try { const db = await idb(); return await new Promise((res, rej) => { const tx = db.transaction('kv', 'readonly'); const rq = tx.objectStore('kv').get(k); rq.onsuccess = () => res(rq.result); rq.onerror = () => rej(rq.error); }); } catch (e) { return undefined; }
}
async function idbDel(k) {
  try { const db = await idb(); await new Promise((res) => { const tx = db.transaction('kv', 'readwrite'); tx.objectStore('kv').delete(k); tx.oncomplete = res; tx.onerror = res; }); } catch (e) {}
}

/* ------------------------------------------------------------- bootstrap -- */
function bootProgress(pct, m) {
  const bar = $('#bootBar'), lbl = $('#bootMsg');
  if (bar) bar.style.width = Math.max(0, Math.min(100, pct)) + '%';
  if (lbl && m) lbl.textContent = m;
}

async function boot() {
  try {
    bootProgress(5, 'Loading Python runtime…');
    PY = await loadPyodide();

    bootProgress(55, 'Fetching save modules…');
    PY.FS.mkdirTree('/st/data');
    for (const f of PYPKG) PY.FS.writeFile('/st/' + f, await fetchText(EDITOR_BASE + f));
    for (const f of PYDATA) PY.FS.writeFile('/st/data/' + f, await fetchText(EDITOR_BASE + 'data/' + f));
    PY.FS.writeFile('/st/st_glue.py', await fetchText('st_glue.py'));

    bootProgress(75, 'Loading item & rune data…');
    LISTS = {
      shop_items: await fetchJSON(EDITOR_BASE + 'data/st_shop_items.json'),
      runes: await fetchJSON(EDITOR_BASE + 'data/st_runes.json'),
    };
    buildLists();

    bootProgress(90, 'Starting editor…');
    await PY.runPythonAsync("import sys\nif '/st' not in sys.path: sys.path.insert(0, '/st')");
    GLUE = PY.pyimport('st_glue');

    bootProgress(100, 'Ready');
    $('#boot').classList.add('hide');
    initUI();
    registerSW();
    await postBoot();          // shared-in file, last-opened chip
  } catch (e) {
    $('#bootMsg').innerHTML = 'Failed to start: ' + escapeHtml(String(e && e.message || e)) +
      '<br><small>Check your connection and reload.</small>';
    console.error(e);
  }
}

async function fetchText(url) { const r = await fetch(url); if (!r.ok) throw new Error('fetch ' + url + ' → ' + r.status); return r.text(); }
async function fetchJSON(url) { const r = await fetch(url); if (!r.ok) throw new Error('fetch ' + url + ' → ' + r.status); return r.json(); }

function buildLists() {
  ITEMS = Object.keys(LISTS.shop_items)
    .map((k) => ({ id: parseInt(k, 10), label: LISTS.shop_items[k], sub: 'id ' + parseInt(k, 10) }))
    .filter((x) => x.id > 0)
    .sort((a, b) => a.id - b.id);
  RUNES = Object.keys(LISTS.runes)
    .map((k) => ({ id: parseInt(k, 16), label: LISTS.runes[k], sub: '0x' + k }))
    .sort((a, b) => a.id - b.id);
}

function itemName(id) { return LISTS.shop_items[String(id).padStart(3, '0')] || ''; }
function itemLabel(id) { const n = itemName(id); return id ? (n ? `${n} [${id}]` : `unknown [${id}]`) : '— empty —'; }
function runeName(id) { const hit = RUNES.find((r) => r.id === id); return hit ? hit.label : (id ? `unknown [${id}]` : ''); }
function runeLabel(id) { return id ? (runeName(id) + ` [${id}]`) : '— none —'; }

/* ---------------------------------------------------------- shared boot --- */
async function postBoot() {
  // 1) file shared INTO the installed PWA (?shared=1 → cached by the SW)
  if (new URLSearchParams(location.search).has('shared')) {
    try {
      const c = await caches.open('st-share');
      const resp = await c.match('shared-file');
      if (resp) {
        const blob = await resp.blob();
        const name = resp.headers.get('x-filename') || 'shared.save';
        await c.delete('shared-file');
        history.replaceState(null, '', location.pathname);
        await handleFile(new File([blob], name));
        return;
      }
    } catch (e) { console.warn('shared-in failed', e); }
  }
  // 2) last-opened chip
  const last = await idbGet('last');
  if (last && last.name) {
    $('#lastName').textContent = last.name;
    $('#lastChip').classList.remove('hide');
  }
}

/* ------------------------------------------------------------- open save -- */
// Single ingest funnel: file input, drag-drop, FS picker, share-in, last-opened.
async function handleFile(file, handle) {
  msg($('#openMsg'), 'Opening ' + file.name + '…', 'info');
  try {
    const buf = new Uint8Array(await file.arrayBuffer());
    PY.FS.writeFile('/save.bin', buf);
    CURRENT_NAME = file.name;
    CURRENT_HANDLE = handle || null;
    CURRENT_EXT = (file.name.split('.').pop() || '').toLowerCase();
    await openFromFS(file.name, '');
    // remember last-opened (small saves → store bytes; keep the handle if any)
    await idbSet('last', { name: file.name, bytes: buf, handle: handle || null });
    $('#lastName').textContent = file.name;
    $('#lastChip').classList.remove('hide');
  } catch (e) {
    msg($('#openMsg'), 'Could not read file: ' + (e && e.message || e), 'err');
    console.error(e);
  }
}

async function openFromFS(name, folder) {
  let r;
  try { r = JSON.parse(GLUE.open_save(name, folder || '')); }
  catch (e) { msg($('#openMsg'), 'Open failed: ' + (e && e.message || e), 'err'); console.error(e); return; }
  if (r.error) { msg($('#openMsg'), r.error, 'err'); $('#body').classList.add('hide'); return; }

  META = r;
  const chk = (r.crc_ok && r.md5_ok) ? '<span class="pill ok">checksums OK</span>' : '<span class="pill bad">checksums BAD</span>';
  $('#openMsg').className = 'msg ok';
  $('#openMsg').innerHTML = escapeHtml(r.message.split(' — ')[0]) + ' &nbsp;' + chk;

  // card folder switcher
  const others = r.card_saves || [];
  if (r.is_card && others.length > 1) {
    $('#cardSwitchCard').classList.remove('hide');
    const sel = $('#folderSel');
    sel.innerHTML = others.map((f) => `<option value="${f}"${f === r.folder ? ' selected' : ''}>${f}</option>`).join('');
    sel.onchange = () => openFromFS(CURRENT_NAME, sel.value);
  } else {
    $('#cardSwitchCard').classList.add('hide');
  }

  applyState(r.state);
  refreshDestinations();
  $('#edits').innerHTML = '';
  $('#body').classList.remove('hide');
  $('#actionbar').classList.remove('hide');
  $('#body').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function applyState(st) {
  SVD = clone(st);
  BASE = clone(st);
  $('#char').innerHTML = SVD.chars.map((c, i) => `<option value="${i}">${escapeHtml(c.name)}${c.recruited ? '' : ' (not recruited)'}</option>`).join('');
  renderGlobals();
  renderChar();
  renderInv();
  updateBadge();
}

/* ---------------------------------------------------------- diff / dirty -- */
// Effective changes only (SVD vs BASE), grouped for the review list + badge.
function buildDiff() {
  const g = [];
  if (SVD.globals.gold !== BASE.globals.gold) g.push({ label: 'Gold', old: BASE.globals.gold, now: SVD.globals.gold });
  if (SVD.globals.skill_points !== BASE.globals.skill_points) g.push({ label: 'Skill points', old: BASE.globals.skill_points, now: SVD.globals.skill_points });
  if (SVD.hero_name !== BASE.hero_name) g.push({ label: 'S4 hero name', old: BASE.hero_name || '—', now: SVD.hero_name || '—' });
  if (SVD.s4_import !== BASE.s4_import) g.push({ label: 'S4 data imported', old: BASE.s4_import ? 'yes' : 'no', now: SVD.s4_import ? 'yes' : 'no' });

  const chars = [];
  SVD.chars.forEach((c, i) => {
    const b = BASE.chars[i], ch = [];
    if (c.recruited !== b.recruited) ch.push({ label: 'Recruited', old: b.recruited ? 'yes' : 'no', now: c.recruited ? 'yes' : 'no' });
    if (c.exp !== b.exp) ch.push({ label: 'EXP', old: b.exp, now: c.exp });
    if (c.hp_cur !== b.hp_cur) ch.push({ label: 'HP', old: b.hp_cur, now: c.hp_cur });
    if (c.hp_max !== b.hp_max) ch.push({ label: 'HP max', old: b.hp_max, now: c.hp_max });
    SVD.stat_keys.forEach((k) => { if (c.stats[k] !== b.stats[k]) ch.push({ label: k, old: b.stats[k], now: c.stats[k] }); });
    SVD.plus_keys.forEach((k) => { if (c.plus[k] !== b.plus[k]) ch.push({ label: '+' + k, old: b.plus[k], now: c.plus[k] }); });
    SVD.equip_keys.forEach((k) => { if (c.equip[k] !== b.equip[k]) ch.push({ label: k, old: itemLabel(b.equip[k]), now: itemLabel(c.equip[k]) }); });
    SVD.rune_keys.forEach((k) => { if (c.runes[k] !== b.runes[k]) ch.push({ label: 'Rune ' + k, old: runeLabel(b.runes[k]), now: runeLabel(c.runes[k]) }); });
    [0, 1, 2, 3].forEach((s) => {
      if (c.magic_overall[s] !== b.magic_overall[s]) ch.push({ label: `Magic ${s + 1} all`, old: b.magic_overall[s], now: c.magic_overall[s] });
      if (c.magic_current[s] !== b.magic_current[s]) ch.push({ label: `Magic ${s + 1} cur`, old: b.magic_current[s], now: c.magic_current[s] });
    });
    if (ch.length) chars.push({ index: i, name: c.name, changes: ch });
  });

  const inv = [];
  SVD.inventory.forEach((s, i) => {
    const b = BASE.inventory[i];
    if (s.id !== b.id || s.qty !== b.qty) inv.push({ slot: i, old: { id: b.id, qty: b.qty }, now: { id: s.id, qty: s.qty } });
  });
  return { globals: g, chars, inv };
}

function countUnsaved() { const d = buildDiff(); return d.globals.length + d.chars.reduce((n, c) => n + c.changes.length, 0) + d.inv.length; }

let badgeRAF = 0;
function updateBadge() {
  if (badgeRAF) return;
  badgeRAF = requestAnimationFrame(() => {
    badgeRAF = 0;
    const n = countUnsaved();
    const b = $('#unsaved');
    b.textContent = n ? `${n} unsaved` : '';
    b.classList.toggle('hide', !n);
    $('#applyBtn').classList.toggle('has-dirty', n > 0);
    const bs = $('#barStatus');
    if (bs) bs.textContent = n ? `${n} unsaved change${n === 1 ? '' : 's'}` : (SVD ? 'No unsaved changes' : '');
  });
}

/* --------------------------------------------------------- render globals - */
function renderGlobals() {
  bindNum($('#gold'), SVD.globals, 'gold', BASE.globals.gold);
  bindNum($('#sp'), SVD.globals, 'skill_points', BASE.globals.skill_points);
  const hero = $('#hero');
  if (SVD.hero_name_max) hero.maxLength = SVD.hero_name_max;
  hero.value = SVD.hero_name || '';
  hero.oninput = () => { SVD.hero_name = hero.value; hero.classList.toggle('chg', SVD.hero_name !== BASE.hero_name); updateBadge(); };
  hero.classList.toggle('chg', SVD.hero_name !== BASE.hero_name);
  const imp = $('#import');
  imp.checked = !!SVD.s4_import;
  imp.classList.toggle('chg', !!SVD.s4_import !== !!BASE.s4_import);
  imp.onchange = () => { SVD.s4_import = imp.checked; imp.classList.toggle('chg', !!SVD.s4_import !== !!BASE.s4_import); updateBadge(); };
}

// Bind a numeric input to obj[key] with live dirty class vs baseline value.
function bindNum(el, obj, key, base) {
  el.value = obj[key];
  el.oninput = () => { obj[key] = +el.value || 0; el.classList.toggle('chg', obj[key] !== base); updateBadge(); };
  el.classList.toggle('chg', obj[key] !== base);
}

/* ------------------------------------------------------------ render char - */
function current() { return SVD.chars[+$('#char').value]; }
function baseChar() { return BASE.chars[+$('#char').value]; }

function fld(label, inner, dirty, onRevert) {
  const rev = dirty ? `<button class="revert" title="revert" type="button">↺</button>` : '';
  const d = document.createElement('div');
  d.className = 'fld' + (dirty ? ' chg' : '');
  d.innerHTML = `<label>${escapeHtml(label)}${rev}</label>${inner}`;
  if (dirty && onRevert) d.querySelector('.revert').onclick = onRevert;
  return d;
}

function renderChar() {
  const c = current(), b = baseChar(); if (!c) return;
  const idx = +$('#char').value;

  // recruited / exp / hp
  $('#recruited').checked = c.recruited;
  $('#recruited').classList.toggle('chg', c.recruited !== b.recruited);
  $('#recruited').onchange = () => { c.recruited = $('#recruited').checked; $('#recruited').classList.toggle('chg', c.recruited !== b.recruited); updateBadge(); };
  bindNum($('#exp'), c, 'exp', b.exp);
  bindNum($('#hpc'), c, 'hp_cur', b.hp_cur);
  bindNum($('#hpm'), c, 'hp_max', b.hp_max);

  // stats / plus — number grids
  const stats = $('#stats'); stats.innerHTML = '';
  SVD.stat_keys.forEach((k) => stats.appendChild(numFld(k, c.stats, k, b.stats[k])));
  const plus = $('#plus'); plus.innerHTML = '';
  SVD.plus_keys.forEach((k) => plus.appendChild(numFld(k, c.plus, k, b.plus[k])));

  // equipment — searchable item pickers
  const equip = $('#equip'); equip.innerHTML = '';
  SVD.equip_keys.forEach((k) => equip.appendChild(pickerFld(k, c.equip, k, b.equip[k], 'item')));

  // runes — searchable rune pickers
  const runes = $('#runes'); runes.innerHTML = '';
  SVD.rune_keys.forEach((k) => runes.appendChild(pickerFld(k, c.runes, k, b.runes[k], 'rune')));

  // magic overall/current
  const magic = $('#magic'); magic.innerHTML = '';
  [0, 1, 2, 3].forEach((i) => {
    const dirty = c.magic_overall[i] !== b.magic_overall[i] || c.magic_current[i] !== b.magic_current[i];
    const d = fld(`Slot ${i + 1} all / cur`,
      `<div class="row"><input data-mo="${i}" type="number" min="0" max="9" value="${c.magic_overall[i]}" style="width:70px">
       <input data-mc="${i}" type="number" min="0" max="9" value="${c.magic_current[i]}" style="width:70px"></div>`,
      dirty, () => { c.magic_overall[i] = b.magic_overall[i]; c.magic_current[i] = b.magic_current[i]; renderChar(); updateBadge(); });
    d.querySelector('[data-mo]').oninput = (e) => { c.magic_overall[i] = +e.target.value || 0; updateBadge(); };
    d.querySelector('[data-mc]').oninput = (e) => { c.magic_current[i] = +e.target.value || 0; updateBadge(); };
    magic.appendChild(d);
  });
}

function numFld(label, obj, key, base) {
  const d = fld(label, `<input type="number" min="0" max="999" value="${obj[key]}">`,
    obj[key] !== base, () => { obj[key] = base; renderChar(); updateBadge(); });
  const inp = d.querySelector('input');
  inp.oninput = () => { obj[key] = +inp.value || 0; inp.parentElement.classList.toggle('chg', obj[key] !== base); updateBadge(); };
  return d;
}

function pickerFld(label, obj, key, base, kind) {
  const cur = obj[key];
  const disp = kind === 'item' ? itemLabel(cur) : runeLabel(cur);
  const d = fld(label, `<button class="pick" type="button">${escapeHtml(disp)}</button>`,
    cur !== base, () => { obj[key] = base; renderChar(); updateBadge(); });
  d.querySelector('.pick').onclick = () => {
    openPicker({
      title: (kind === 'item' ? 'Choose item — ' : 'Choose rune — ') + label,
      items: kind === 'item' ? ITEMS : RUNES,
      current: obj[key], allowEmpty: true,
      onPick: (id) => { obj[key] = id; renderChar(); updateBadge(); },
    });
  };
  return d;
}

/* ------------------------------------------------------------- inventory -- */
function renderInv() {
  const f = ($('#invFilter').value || '').toLowerCase();
  const showEmpty = $('#invEmpty').checked;
  const body = $('#invBody'); body.innerHTML = '';
  for (let i = 0; i < SVD.inventory.length; i++) {
    if (body.children.length >= 300) break;
    const s = SVD.inventory[i];
    if (!showEmpty && !s.id) continue;
    if (f) { const nm = itemName(s.id).toLowerCase(); if (!nm.includes(f) && String(s.id) !== f) continue; }
    const b = BASE.inventory[i];
    const dirty = s.id !== b.id || s.qty !== b.qty;
    const tr = document.createElement('tr');
    if (dirty) tr.className = 'chg';
    tr.innerHTML = `<td>${s.slot}</td>
      <td><button class="pick" type="button" style="width:100%;text-align:left">${escapeHtml(itemLabel(s.id))}</button></td>
      <td><input type="number" min="0" max="99" value="${s.qty}" style="width:70px"></td>
      <td>${dirty ? '<button class="revert" title="revert" type="button">↺</button>' : ''}</td>`;
    tr.querySelector('.pick').onclick = () => openPicker({
      title: `Inventory slot ${s.slot}`, items: ITEMS, current: s.id, allowEmpty: true,
      onPick: (id) => { s.id = id; renderInv(); updateBadge(); },
    });
    tr.querySelector('input').oninput = (e) => {
      s.qty = +e.target.value || 0;
      const nowDirty = s.id !== b.id || s.qty !== b.qty;
      if (nowDirty !== dirty) renderInv();
      updateBadge();
    };
    if (dirty) tr.querySelector('.revert').onclick = () => { s.id = b.id; s.qty = b.qty; renderInv(); updateBadge(); };
    body.appendChild(tr);
  }
}

/* ------------------------------------------------------- searchable picker */
let pickerState = null;
function openPicker({ title, items, current, allowEmpty, onPick }) {
  pickerState = { items, current, allowEmpty, onPick };
  $('#pickTitle').textContent = title;
  const inp = $('#pickSearch'); inp.value = '';
  renderPickerList('');
  $('#pickModal').classList.remove('hide');
  setTimeout(() => inp.focus(), 30);
  inp.oninput = () => renderPickerList(inp.value);
}
function renderPickerList(q) {
  const { items, current, allowEmpty } = pickerState;
  q = (q || '').trim().toLowerCase();
  let rows = items;
  if (q) rows = items.filter((it) => it.label.toLowerCase().includes(q) || String(it.id) === q || (it.sub || '').toLowerCase().includes(q));
  const capped = rows.length > 300;
  let html = '';
  if (allowEmpty && !q) html += `<div class="prow${!current ? ' sel' : ''}" data-id="0"><b>— empty —</b></div>`;
  html += rows.slice(0, 300).map((it) =>
    `<div class="prow${it.id === current ? ' sel' : ''}" data-id="${it.id}"><b>${escapeHtml(it.label)}</b><span class="psub">${escapeHtml(it.sub || '')}</span></div>`).join('');
  if (capped) html += `<div class="phint">${rows.length} matches — keep typing to narrow…</div>`;
  if (!rows.length && q) html += `<div class="phint">No match. Type an id number to force a raw value.</div>`;
  const list = $('#pickList');
  list.innerHTML = html;
  $$('#pickList .prow').forEach((el) => el.onclick = () => { pickerState.onPick(parseInt(el.dataset.id, 10)); closePicker(); });
}
function closePicker() { $('#pickModal').classList.add('hide'); pickerState = null; }

/* ---------------------------------------------------------- destinations -- */
// Which write targets + delivery methods are available for the opened file.
function refreshDestinations() {
  const t = $('#target');
  t.querySelector('option[value=sps]').disabled = !META.can_sps;
  t.querySelector('option[value=card]').disabled = !META.is_card;
  if (META.is_card) t.value = 'card';
  else if (t.value === 'card') t.value = META.can_sps ? 'sps' : 'psu';
  updateTargetHint();

  // delivery: download always; save-in-place if handle; share if canShare
  const dsel = $('#delivery');
  const canFs = !!CURRENT_HANDLE;
  dsel.querySelector('option[value=file]').disabled = !canFs;
  const canShare = typeof navigator.canShare === 'function';
  dsel.querySelector('option[value=share]').disabled = !canShare;
  if (dsel.value === 'file' && !canFs) dsel.value = 'download';
  if (dsel.value === 'share' && !canShare) dsel.value = 'download';
}

function updateTargetHint() {
  const t = $('#target').value;
  $('#targetHint').textContent = {
    psu: 'Exports a .psu folder — import it with mymc or uLaunchELF onto your memory card.',
    sps: 'Rebuilds the original SharkPort/X-Port container (same layout, edited game file).',
    card: 'Injects the edited save into a copy of the uploaded .ps2 card image. ECC is recomputed and re-verified.',
  }[t] || '';
}

/* ------------------------------------------------------- review + write --- */
function openReview() {
  if (!SVD) return;
  const d = buildDiff();
  const n = d.globals.length + d.chars.reduce((s, c) => s + c.changes.length, 0) + d.inv.length;
  const rawEdits = $$('#edits .erow').filter((r) => r.querySelector('.eoff').value.trim() !== '').length;
  if (!n && !rawEdits) { msg($('#writeMsg'), 'No changes to apply.', 'info'); return; }

  const row = (x) => `<tr><td>${escapeHtml(x.label)}</td><td class="old">${escapeHtml(x.old)}</td><td class="arrow">→</td><td class="new">${escapeHtml(x.now)}</td></tr>`;
  let html = '';
  if (d.globals.length) html += `<h4>Globals</h4><table class="rev">${d.globals.map(row).join('')}</table>`;
  d.chars.forEach((c) => { html += `<h4>${escapeHtml(c.name)} <small>(slot ${c.index})</small></h4><table class="rev">${c.changes.map(row).join('')}</table>`; });
  if (d.inv.length) {
    html += `<h4>Inventory</h4><table class="rev">` + d.inv.map((s) =>
      `<tr><td>Slot ${s.slot}</td><td class="old">${escapeHtml(itemLabel(s.old.id))} ×${s.old.qty}</td><td class="arrow">→</td><td class="new">${escapeHtml(itemLabel(s.now.id))} ×${s.now.qty}</td></tr>`).join('') + `</table>`;
  }
  if (rawEdits) html += `<h4>Raw byte edits</h4><table class="rev"><tr><td colspan="4">${rawEdits} manual byte edit(s)</td></tr></table>`;

  $('#reviewBody').innerHTML = html;
  const deliv = $('#delivery').value;
  const dest = deliv === 'file' ? `save to ${escapeHtml(CURRENT_NAME)}` : deliv === 'share' ? 'share…' : 'download';
  $('#reviewConfirm').textContent = `Apply & ${dest}`;
  $('#reviewModal').classList.remove('hide');
}
function closeReview() { $('#reviewModal').classList.add('hide'); }

function assemblePayload() {
  const d = buildDiff();
  const edits = $$('#edits .erow')
    .map((r) => ({ off: parseInt(r.querySelector('.eoff').value, 16), val: parseInt(r.querySelector('.eval').value || 0) }))
    .filter((e) => !isNaN(e.off));
  return {
    edits,
    char_edits: d.chars.map((c) => ({ index: c.index, char: SVD.chars[c.index] })),
    inv_edits: d.inv.map((s) => ({ slot: s.slot, id: SVD.inventory[s.slot].id, qty: SVD.inventory[s.slot].qty })),
    globals: { gold: SVD.globals.gold, skill_points: SVD.globals.skill_points },
    hero_name: SVD.hero_name, s4_import: SVD.s4_import,
    target: $('#target').value,
  };
}

// Runs the engine synchronously (no await) before any share, preserving the gesture.
async function doWrite(deliv) {
  closeReview();
  let r;
  try { r = JSON.parse(GLUE.write_save(JSON.stringify(assemblePayload()))); }
  catch (e) { msg($('#writeMsg'), 'Write failed: ' + (e && e.message || e), 'err'); console.error(e); return; }
  if (r.error) { msg($('#writeMsg'), r.error, 'err'); return; }

  const bytes = PY.FS.readFile(r.outpath);      // Uint8Array

  if (deliv === 'file' && CURRENT_HANDLE) {
    try {
      const perm = CURRENT_HANDLE.requestPermission ? await CURRENT_HANDLE.requestPermission({ mode: 'readwrite' }) : 'granted';
      if (perm !== 'granted') throw new Error('write permission denied');
      const w = await CURRENT_HANDLE.createWritable();
      await w.write(bytes); await w.close();
      msg($('#writeMsg'), `${r.message} → saved to ${CURRENT_NAME}`, 'ok');
    } catch (e) { msg($('#writeMsg'), 'Save-to-file failed, downloading instead: ' + (e.message || e), 'err'); downloadBytes(bytes, r.filename); }
  } else if (deliv === 'share') {
    try {
      const file = new File([bytes], r.filename, { type: 'application/octet-stream' });
      if (navigator.canShare && navigator.canShare({ files: [file] })) { await navigator.share({ files: [file], title: r.filename }); msg($('#writeMsg'), `${r.message} → shared ${r.filename}`, 'ok'); }
      else { downloadBytes(bytes, r.filename); msg($('#writeMsg'), `${r.message} → downloaded ${r.filename} (share unavailable)`, 'ok'); }
    } catch (e) { if (e && e.name === 'AbortError') { msg($('#writeMsg'), 'Share cancelled.', 'info'); } else { downloadBytes(bytes, r.filename); msg($('#writeMsg'), `${r.message} → downloaded ${r.filename}`, 'ok'); } }
  } else {
    downloadBytes(bytes, r.filename);
    msg($('#writeMsg'), `${r.message} → downloaded ${r.filename}`, 'ok');
  }

  if (r.state) applyState(r.state);   // resets BASE, clears dirty/badge
  idbSet('last', { name: CURRENT_NAME, bytes, handle: CURRENT_HANDLE || null });
}

function downloadBytes(u8, filename) {
  const blob = new Blob([u8], { type: 'application/octet-stream' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

/* --------------------------------------------------------- raw byte edits - */
function editRow() {
  return `<div class="row erow" style="margin-top:6px"><input class="eoff" placeholder="offset (hex, e.g. 1a3f)" style="width:180px"><input class="eval" type="number" min="0" max="255" placeholder="byte 0-255" style="width:140px"><button class="ghost edel" type="button">✕</button></div>`;
}
function bindEditDel() { $$('#edits .edel').forEach((b) => b.onclick = () => { b.closest('.erow').remove(); updateBadge(); }); }

/* ----------------------------------------------------------------- UI ----- */
function initUI() {
  const drop = $('#drop'), file = $('#file');
  drop.onclick = () => file.click();
  file.onchange = () => { if (file.files[0]) handleFile(file.files[0]); file.value = ''; };
  ['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('hot'); }));
  ['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('hot'); }));
  drop.addEventListener('drop', (e) => { const f = e.dataTransfer.files[0]; if (f) handleFile(f); });

  // File System Access picker (desktop) — retains a writable handle
  const fsBtn = $('#fsBtn');
  if ('showOpenFilePicker' in window) {
    fsBtn.classList.remove('hide');
    fsBtn.onclick = async () => {
      try {
        const [h] = await showOpenFilePicker({ types: [{ description: 'Suikoden Tactics save', accept: { 'application/octet-stream': ['.sps', '.xps', '.cbs', '.max', '.psu', '.ps2'] } }] });
        const f = await h.getFile();
        await handleFile(f, h);
      } catch (e) { if (e && e.name !== 'AbortError') console.warn(e); }
    };
  }

  // last-opened chip
  $('#lastOpen').onclick = async () => {
    const last = await idbGet('last');
    if (!last || !last.bytes) return;
    await handleFile(new File([last.bytes], last.name), last.handle || null);
  };
  $('#lastForget').onclick = async (e) => { e.stopPropagation(); await idbDel('last'); $('#lastChip').classList.add('hide'); };

  $('#char').onchange = renderChar;
  $('#invFilter').oninput = renderInv;
  $('#invEmpty').onchange = renderInv;
  $('#addEdit').onclick = () => { $('#edits').insertAdjacentHTML('beforeend', editRow()); bindEditDel(); };
  $('#target').onchange = updateTargetHint;
  $('#applyBtn').onclick = openReview;
  $('#reviewCancel').onclick = closeReview;
  $('#reviewCancel2').onclick = closeReview;
  $('#reviewConfirm').onclick = () => doWrite($('#delivery').value);

  // picker + review modals
  $('#pickClose').onclick = closePicker;
  $('#pickModal').addEventListener('click', (e) => { if (e.target.id === 'pickModal') closePicker(); });
  $('#reviewModal').addEventListener('click', (e) => { if (e.target.id === 'reviewModal') closeReview(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { closePicker(); closeReview(); } });

  // install button (Chromium)
  window.addEventListener('beforeinstallprompt', (e) => { e.preventDefault(); deferredInstall = e; $('#installBtn').classList.remove('hide'); });
  $('#installBtn').onclick = async () => { if (!deferredInstall) return; deferredInstall.prompt(); await deferredInstall.userChoice; deferredInstall = null; $('#installBtn').classList.add('hide'); };
  window.addEventListener('appinstalled', () => $('#installBtn').classList.add('hide'));

  // unsaved guard
  window.addEventListener('beforeunload', (e) => { if (SVD && countUnsaved() > 0) { e.preventDefault(); e.returnValue = ''; } });
}

/* ---------------------------------------------------------- service wrkr -- */
function registerSW() {
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch((e) => console.warn('SW register failed', e));
}

pyReady = boot();
