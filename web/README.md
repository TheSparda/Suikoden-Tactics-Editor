# Suikoden Tactics — Web Editor

A browser-based twin of the desktop editor, in two modes — a **Save editor** and
an **ISO editor** — that run **100% in your browser**. Your files are never
uploaded anywhere.

**Live:** https://thesparda.github.io/Suikoden-Tactics-Editor/web/

The **Save editor** supports every container the desktop does:
`.sps` · `.xps` · `.cbs` · `.max` · `.psu` · raw `.ps2` memory-card images.

The **ISO editor** edits a USA *Suikoden Tactics* (SLUS-21245) disc image
**in place**.

## Depth features

- **Inline guide notes.** Item and rune fields/pickers show a short
  guide-derived description ("Heals 50 HP", "Magic Rune that enables fire
  magic"). Derived from the local GameFAQs/Neoseeker guides into small committed
  JSON (`st_rune_ref.json`, `st_item_ref.json`) — the guide prose itself is not
  committed (copyright); regenerate with `st-editor/build_rune_ref.py` /
  `build_item_ref.py`.
- **Real field labels.** The ISO data tables show V11's own field names
  (recovered from the decompiled editor), not `f1 @+0x00`.
- **ISO undo/redo** (Ctrl+Z / Ctrl+Y) — one user action per step, via a single
  write-hook + microtask transaction.
- **Presets** in the Characters view: Max growth (SS), Max all skills, Clear
  skills — staged like any edit (reviewable, revertible, undoable).
- **`.xdelta` export** — a standard VCDIFF patch synthesized directly from your
  staged edits (no full-disc diff), alongside the `.stmod` recipe. Apply to a
  **pristine** disc: `xdelta3 -d -s disc.iso edits.xdelta out.iso` (a synthesized
  patch has no source checksum).
- **Force refresh + version-behind check** in the footer, so a stuck service
  worker can never pin you to an old build.
- Two themes (**Crimson & Gold** / **Parchment**), matching the desktop editor.

## ISO editor

Edit the disc's character growth ranks, weapon type/growth, runes, innate
element and 29 battle-skill affinities, plus the item, shop, rune-shop, price,
skill and enemy tables — the same fields as the desktop ISO editor.

- **Never loads the whole disc.** It reads only the bounded editable region
  (~0.6 MB across two windows) via the File System Access API `Blob.slice`,
  edits it in memory, and writes back **only the changed byte-runs** at their
  absolute offsets (`createWritable({keepExistingData:true})`) — the multi-GB
  disc is never copied or uploaded.
- **Version + fingerprint gate:** refuses anything without the `SLUS_212.45`
  marker and the Kyril character-table fingerprint, so you can't corrupt a
  PAL/JP disc.
- **Recipe (`.stmod`):** export your staged edits as a tiny JSON of byte-runs
  (with original bytes) to share a rebalance without passing around the disc;
  import replays it, version-checked and warning on any byte that doesn't match
  the recipe's recorded original.
- **Desktop-only:** needs the File System Access API (desktop Chrome / Edge /
  Brave / Opera). On mobile/Firefox/Safari the ISO tab shows a clear "needs
  desktop" card while the Save editor keeps working everywhere.

Offsets are never guessed: character offsets come from `st_characters.json`,
table offsets from `st_tables.json`, and the record field schema is generated
from `stfields.py` into `st_char_schema.json` (regenerate with
`python3 st-editor/gen_iso_schema.py`) — one source of truth for the JS port.

## Features

- **Searchable pickers** for every item, rune and equipment slot (type a name or
  id) — usable with 286 items on a phone, no giant native dropdowns.
- **Review-changes confirmation** — an explicit *old → new* list of only the
  fields you actually changed, before anything is written.
- **Per-field dirty highlight + one-tap revert (↺)**, a live **"N unsaved"**
  badge, and a **beforeunload guard** so you never lose edits by accident.
- **Multi-slot memory cards** — pick which save folder on the card to edit; ECC
  is recomputed and re-verified on write.
- **Last opened** — one tap to reopen your previous save (stored in IndexedDB,
  never uploaded).
- **Deliver three ways:** download, **save in place** to the file you opened
  (desktop Chromium, File System Access), or **Share…** to another app (Android).
  Missing capabilities degrade gracefully to download.
- **Installable PWA** with a **Share-target** — share a save *into* the app from
  your file manager — and offline support after first load.
- Staged **boot progress**, light/dark themes, and a mobile-first layout with
  safe-area insets and no horizontal overflow down to 320 px.

## How it works

The desktop editor's Python save code (`st-editor/stsaveio.py`,
`stsaveedit.py`, `stsave.py`, `stsavefields.py`) runs **unchanged** inside
[Pyodide](https://pyodide.org) (CPython compiled to WebAssembly). The save you
pick is written into Pyodide's in-memory filesystem, decoded and edited by the
same verified field map the desktop uses (party stats, equipment, runes, magic
levels, recruitment incl. the Suikoden IV hero unlock, gold, skill points,
inventory), and read back out for download. Both game checksums (CRC-32 + MD5)
are recomputed on write. Nothing leaves the device.

```
file pick ──► /save.bin (Pyodide MEMFS) ──► stsaveio.open_any / open_ps2_card
          ──► decode → edit in the UI → stsavefields.write_* + fix_checksums
          ──► write_psu / repack / inject-into-card ──► read bytes ──► download
```

## Install on Android (offline PWA)

Open the live URL in Chrome, then browser menu → **Install app** / **Add to Home
screen**. A service worker precaches the app shell and the Pyodide runtime on
first load, so it works offline afterward. The one-time first load fetches the
Pyodide runtime (~a few MB) from a CDN; every load after that is cached.

## Files

| File | Purpose |
|---|---|
| `index.html` | UI shell (Save \| ISO mode tabs) + PWA meta |
| `style.css` | theme (ported from the desktop editor), mobile-first |
| `app.js` | Save editor: Pyodide bootstrap, pickers, diff/review, delivery |
| `iso.js` | ISO editor: ranged read, field views, diff-run in-place write, recipes |
| `st_glue.py` | thin Python glue that drives the unchanged desktop save modules |
| `manifest.webmanifest`, `sw.js`, `icons/` | PWA / offline / share-target |
| `tests/` | `validate.mjs`, `save_roundtrip.py`, `iso_engine.py`, `e2e.mjs` |

## Tests

```bash
cd web
npm test        # static validation + save & ISO engine round-trips (skips e2e)
npm run test:all # + headless Chromium e2e (needs Playwright installed)
```

`save_roundtrip.py` ships **no game data** — it builds a synthetic, valid save
from the engine's own offset constants, so the tests can't drift from the code.
Each layer self-skips cleanly if its heavy dependency (python3 / Playwright) is
absent, so minimal CI never breaks.

`app.js` fetches the save modules and the data tables it needs
(`st_ram_party_map.json`, `st_shop_items.json`, `st_runes.json`) from
`../st-editor/`, so this folder is not standalone — it must be served from
within the repo (as it is on GitHub Pages).

## Local development

Serve the **repository root** (so `../st-editor/` resolves) over HTTP — a
service worker and `fetch` need `http://`, not `file://`:

```bash
cd Suikoden-Tactics-Editor
python3 -m http.server 8000
# open http://127.0.0.1:8000/web/
```

Always keep a backup of your original save.
