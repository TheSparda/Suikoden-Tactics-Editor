/* Suikoden Tactics — client-side web save editor.
 *
 * Loads Pyodide, writes the desktop editor's Python save modules into the
 * in-memory FS, and drives them from the browser. The uploaded save is written
 * to /save.bin, decoded/edited by the (unchanged) Python code, and the result
 * is read back out of the FS as a download. Nothing is uploaded anywhere.
 */
'use strict';

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

// Python modules + data copied from the desktop editor into Pyodide's FS.
const PYPKG = ['stsaveio.py', 'stsaveedit.py', 'stsave.py', 'stsavefields.py'];
const PYDATA = ['st_ram_party_map.json'];      // needed by stsavefields.roster()
const EDITOR_BASE = '../st-editor/';           // desktop module folder (repo layout)

let PY = null;      // pyodide instance
let GLUE = null;    // st_glue python module proxy
let LISTS = null;   // { shop_items, runes } for JS dropdowns
let SVD = null;     // current decoded save state (plain JS object)
const DIRTY_CH = new Set();
const DIRTY_INV = new Set();

/* ---------------------------------------------------------------- theme --- */
const th = localStorage.getItem('st_theme') || 'dark';
document.body.dataset.theme = th;
$('#themeBtn').onclick = () => {
  const t = document.body.dataset.theme === 'dark' ? 'light' : 'dark';
  document.body.dataset.theme = t;
  localStorage.setItem('st_theme', t);
};

function msg(el, m, kind) { el.textContent = m; el.className = 'msg ' + (kind || 'info'); }

/* ------------------------------------------------------------- bootstrap -- */
async function boot() {
  const bm = $('#bootMsg');
  try {
    bm.textContent = 'Loading Python runtime…';
    PY = await loadPyodide();

    bm.textContent = 'Fetching save modules…';
    PY.FS.mkdirTree('/st/data');
    for (const f of PYPKG) {
      PY.FS.writeFile('/st/' + f, await fetchText(EDITOR_BASE + f));
    }
    for (const f of PYDATA) {
      PY.FS.writeFile('/st/data/' + f, await fetchText(EDITOR_BASE + 'data/' + f));
    }
    PY.FS.writeFile('/st/st_glue.py', await fetchText('st_glue.py'));

    bm.textContent = 'Loading dropdown data…';
    LISTS = {
      shop_items: await fetchJSON(EDITOR_BASE + 'data/st_shop_items.json'),
      runes: await fetchJSON(EDITOR_BASE + 'data/st_runes.json'),
    };

    bm.textContent = 'Starting editor…';
    await PY.runPythonAsync("import sys\nif '/st' not in sys.path: sys.path.insert(0, '/st')");
    GLUE = PY.pyimport('st_glue');

    ensureItemsDL();
    $('#boot').classList.add('hide');
    initUI();
    registerSW();
  } catch (e) {
    bm.innerHTML = 'Failed to start: ' + escapeHtml(String(e && e.message || e)) +
      '<br><small>Check your connection and reload.</small>';
    console.error(e);
  }
}

async function fetchText(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error('fetch ' + url + ' → ' + r.status);
  return r.text();
}
async function fetchJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error('fetch ' + url + ' → ' + r.status);
  return r.json();
}
function escapeHtml(s) { return s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

/* ------------------------------------------------------------- open save -- */
let CURRENT_NAME = '';

async function openFile(file) {
  msg($('#openMsg'), 'Opening ' + file.name + '…', 'info');
  try {
    const buf = new Uint8Array(await file.arrayBuffer());
    PY.FS.writeFile('/save.bin', buf);
    CURRENT_NAME = file.name;
    await openFromFS(file.name, '');
  } catch (e) {
    msg($('#openMsg'), 'Could not read file: ' + (e && e.message || e), 'err');
    console.error(e);
  }
}

async function openFromFS(name, folder) {
  let r;
  try {
    r = JSON.parse(GLUE.open_save(name, folder || ''));
  } catch (e) {
    msg($('#openMsg'), 'Open failed: ' + (e && e.message || e), 'err');
    console.error(e);
    return;
  }
  if (r.error) { msg($('#openMsg'), r.error, 'err'); $('#body').classList.add('hide'); return; }

  const chk = (r.crc_ok && r.md5_ok)
    ? '<span class="pill ok">checksums OK</span>'
    : '<span class="pill bad">checksums BAD</span>';
  $('#openMsg').className = 'msg ok';
  $('#openMsg').innerHTML = escapeHtml(r.message) + ' &nbsp;' + chk;

  // target availability + hints
  const spsOpt = $('#target option[value=sps]');
  const cardOpt = $('#target option[value=card]');
  spsOpt.disabled = !r.can_sps;
  cardOpt.disabled = !r.is_card;
  if (r.is_card) $('#target').value = 'card';
  else if ($('#target').value === 'card') $('#target').value = 'psu';

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

  $('#edits').innerHTML = '';
  applyState(r.state);
  updateTargetHint();
  $('#body').classList.remove('hide');
  $('#body').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/* --------------------------------------------------------- dropdown data -- */
function itemName(id) { return LISTS.shop_items[String(id).padStart(3, '0')] || ''; }
function itemLabel(id) { const nm = itemName(id); return id ? (nm ? `${nm} [${id}]` : `unknown [${id}]`) : ''; }
function itemOpts(sel) {
  let o = `<option value="0" ${sel === 0 ? 'selected' : ''}>— empty —</option>`;
  const m = LISTS.shop_items;
  Object.keys(m).sort((a, b) => (+a) - (+b)).forEach((k) => {
    const v = parseInt(k, 10); if (!v) return;
    o += `<option value="${v}" ${v === sel ? 'selected' : ''}>${m[k]} [${v}]</option>`;
  });
  if (sel && !itemName(sel)) o += `<option value="${sel}" selected>unknown [${sel}]</option>`;
  return o;
}
function parseItem(v) {
  v = (v || '').trim();
  if (v === '') return 0;
  const m = v.match(/\[(\d+)\]\s*$/); if (m) return +m[1];
  if (/^\d+$/.test(v)) return +v;
  const hit = Object.entries(LISTS.shop_items).find(([, n]) => n.toLowerCase() === v.toLowerCase());
  return hit ? parseInt(hit[0], 10) : null;
}
function ensureItemsDL() {
  if (document.getElementById('itemsDL')) return;
  const dl = document.createElement('datalist'); dl.id = 'itemsDL';
  dl.innerHTML = Object.keys(LISTS.shop_items).sort((a, b) => (+a) - (+b)).map((k) => {
    const v = parseInt(k, 10); return v ? `<option value="${LISTS.shop_items[k]} [${v}]"></option>` : '';
  }).join('');
  document.body.appendChild(dl);
}
function runeOpts(sel) {
  let o = '<option value="0">— none —</option>';
  const m = LISTS.runes;
  Object.keys(m).sort((a, b) => parseInt(a, 16) - parseInt(b, 16)).forEach((k) => {
    const v = parseInt(k, 16);
    o += `<option value="${v}" ${v === sel ? 'selected' : ''}>${m[k]} (0x${k})</option>`;
  });
  return o;
}

/* -------------------------------------------------------------- render ---- */
function current() { return SVD.chars[+$('#char').value]; }

function renderChar() {
  const c = current(); if (!c) return;
  $('#recruited').checked = c.recruited; $('#exp').value = c.exp;
  $('#hpc').value = c.hp_cur; $('#hpm').value = c.hp_max;
  $('#stats').innerHTML = SVD.stat_keys.map((k) => `<div class="fld"><label>${k}</label><input data-sk="${k}" type="number" min="0" max="999" value="${c.stats[k]}"></div>`).join('');
  $('#plus').innerHTML = SVD.plus_keys.map((k) => `<div class="fld"><label>${k}</label><input data-pk="${k}" type="number" min="0" max="999" value="${c.plus[k]}"></div>`).join('');
  $('#equip').innerHTML = SVD.equip_keys.map((k) => `<div class="fld"><label>${k}</label><select data-ek="${k}">${itemOpts(c.equip[k])}</select></div>`).join('');
  $('#runes').innerHTML = SVD.rune_keys.map((k) => `<div class="fld"><label>${k}</label><select data-rk="${k}">${runeOpts(c.runes[k])}</select></div>`).join('');
  $('#magic').innerHTML = [0, 1, 2, 3].map((i) => `<div class="fld"><label>Slot ${i + 1} all/cur</label><div class="row"><input data-mo="${i}" type="number" min="0" max="9" value="${c.magic_overall[i]}" style="width:74px"><input data-mc="${i}" type="number" min="0" max="9" value="${c.magic_current[i]}" style="width:74px"></div></div>`).join('');

  const idx = +$('#char').value;
  const mark = () => DIRTY_CH.add(idx);
  $('#recruited').onchange = () => { c.recruited = $('#recruited').checked; mark(); };
  $('#exp').oninput = () => { c.exp = +$('#exp').value || 0; mark(); };
  $('#hpc').oninput = () => { c.hp_cur = +$('#hpc').value || 0; mark(); };
  $('#hpm').oninput = () => { c.hp_max = +$('#hpm').value || 0; mark(); };
  $$('#stats input').forEach((e) => e.oninput = () => { c.stats[e.dataset.sk] = +e.value || 0; mark(); });
  $$('#plus input').forEach((e) => e.oninput = () => { c.plus[e.dataset.pk] = +e.value || 0; mark(); });
  $$('#equip select').forEach((e) => e.onchange = () => { c.equip[e.dataset.ek] = +e.value || 0; mark(); });
  $$('#runes select').forEach((e) => e.onchange = () => { c.runes[e.dataset.rk] = +e.value || 0; mark(); });
  $$('#magic input[data-mo]').forEach((e) => e.oninput = () => { c.magic_overall[+e.dataset.mo] = +e.value || 0; mark(); });
  $$('#magic input[data-mc]').forEach((e) => e.oninput = () => { c.magic_current[+e.dataset.mc] = +e.value || 0; mark(); });
}

function renderInv() {
  const f = ($('#invFilter').value || '').toLowerCase();
  const showEmpty = $('#invEmpty').checked;
  $('#invBody').innerHTML = SVD.inventory.filter((s) => {
    if (!showEmpty && !s.id) return false;
    if (f) { const nm = itemName(s.id).toLowerCase(); if (!nm.includes(f) && String(s.id) !== f) return false; }
    return true;
  }).slice(0, 250).map((s) =>
    `<tr><td>${s.slot}</td><td><input data-inv="${s.slot}" data-f="id" list="itemsDL" value="${itemLabel(s.id)}" placeholder="— empty — (type an item name)" style="width:260px"></td><td><input data-inv="${s.slot}" data-f="qty" type="number" min="0" max="99" value="${s.qty}" style="width:74px"></td></tr>`
  ).join('');
  $$('#invBody input').forEach((e) => e.onchange = () => {
    const s = SVD.inventory[+e.dataset.inv];
    if (e.dataset.f === 'id') {
      const id = parseItem(e.value);
      if (id === null) { e.style.borderColor = 'var(--chg)'; return; }
      e.style.borderColor = ''; s.id = id; e.value = itemLabel(id);
    } else { s.qty = +e.value || 0; }
    DIRTY_INV.add(+e.dataset.inv);
  });
}

function applyState(st) {
  SVD = st; DIRTY_CH.clear(); DIRTY_INV.clear();
  $('#gold').value = st.globals.gold; $('#sp').value = st.globals.skill_points;
  $('#hero').value = st.hero_name || ''; if (st.hero_name_max) $('#hero').maxLength = st.hero_name_max;
  $('#import').checked = !!st.s4_import;
  $('#char').innerHTML = st.chars.map((c, i) => `<option value="${i}">${c.name}${c.recruited ? '' : ' (not recruited)'}</option>`).join('');
  renderChar(); renderInv();
}

/* --------------------------------------------------------------- write ---- */
function editRow() {
  return `<div class="row erow" style="margin-top:6px"><input class="eoff" placeholder="offset (hex, e.g. 1a3f)" style="width:180px"><input class="eval" type="number" min="0" max="255" placeholder="byte 0-255" style="width:140px"><button class="ghost edel" type="button">✕</button></div>`;
}
function bindEditDel() { $$('#edits .edel').forEach((b) => b.onclick = () => b.closest('.erow').remove()); }

async function write() {
  const edits = $$('#edits .erow')
    .map((r) => ({ off: parseInt(r.querySelector('.eoff').value, 16), val: parseInt(r.querySelector('.eval').value || 0) }))
    .filter((e) => !isNaN(e.off));
  const char_edits = [...DIRTY_CH].map((i) => ({ index: i, char: SVD.chars[i] }));
  const inv_edits = [...DIRTY_INV].map((s) => ({ slot: s, id: SVD.inventory[s].id, qty: SVD.inventory[s].qty }));
  const globals = { gold: +$('#gold').value || 0, skill_points: +$('#sp').value || 0 };
  const payload = {
    edits, char_edits, inv_edits, globals,
    hero_name: $('#hero').value, s4_import: $('#import').checked,
    target: $('#target').value,
  };
  let r;
  try {
    r = JSON.parse(GLUE.write_save(JSON.stringify(payload)));
  } catch (e) {
    msg($('#writeMsg'), 'Write failed: ' + (e && e.message || e), 'err');
    console.error(e); return;
  }
  if (r.error) { msg($('#writeMsg'), r.error, 'err'); return; }

  // pull the written bytes back out of the in-memory FS → download
  const bytes = PY.FS.readFile(r.outpath);
  downloadBytes(bytes, r.filename);
  msg($('#writeMsg'), r.message + ' → downloaded ' + r.filename, 'ok');
  if (r.state) applyState(r.state);
}

function downloadBytes(u8, filename) {
  const blob = new Blob([u8], { type: 'application/octet-stream' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

function updateTargetHint() {
  const t = $('#target').value;
  const h = {
    psu: 'Exports a .psu folder — import it with mymc or uLaunchELF onto your memory card.',
    sps: 'Rebuilds the original SharkPort/X-Port container (same layout, edited game file).',
    card: 'Injects the edited save into a copy of the uploaded .ps2 card image and downloads the whole card. ECC is recomputed and re-verified.',
  }[t] || '';
  $('#targetHint').textContent = h;
}

/* ----------------------------------------------------------------- UI ----- */
function initUI() {
  const drop = $('#drop'), file = $('#file');
  drop.onclick = () => file.click();
  file.onchange = () => { if (file.files[0]) openFile(file.files[0]); file.value = ''; };
  ['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('hot'); }));
  ['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('hot'); }));
  drop.addEventListener('drop', (e) => { const f = e.dataTransfer.files[0]; if (f) openFile(f); });

  $('#char').onchange = renderChar;
  $('#invFilter').oninput = renderInv;
  $('#invEmpty').onchange = renderInv;
  $('#addEdit').onclick = () => { $('#edits').insertAdjacentHTML('beforeend', editRow()); bindEditDel(); };
  $('#target').onchange = updateTargetHint;
  $('#write').onclick = write;
}

/* ---------------------------------------------------------- service wrkr -- */
function registerSW() {
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('sw.js').catch((e) => console.warn('SW register failed', e));
  }
}

boot();
