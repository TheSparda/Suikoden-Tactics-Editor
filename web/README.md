# Suikoden Tactics — Web Save Editor

A browser-based twin of the desktop **Save editor**. Open a *Suikoden Tactics*
(PS2) memory-card save, edit it, and download the edited save — **100% in your
browser**. Your file is never uploaded anywhere.

**Live:** https://thesparda.github.io/Suikoden-Tactics-Editor/web/

Supports every container the desktop editor does:
`.sps` · `.xps` · `.cbs` · `.max` · `.psu` · raw `.ps2` memory-card images.

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
| `index.html` | UI |
| `style.css` | theme (ported from the desktop editor) |
| `app.js` | Pyodide bootstrap, module loading, UI wiring, download |
| `st_glue.py` | thin Python glue that drives the unchanged desktop modules |
| `manifest.webmanifest`, `sw.js`, `icons/` | PWA / offline |

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
