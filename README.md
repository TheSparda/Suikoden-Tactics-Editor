# Suikoden Tactics Editor (Web)

A browser-based **save** and **ISO** editor for *Suikoden Tactics*
(USA `SLUS-21245`, PAL `SLES-53769`). Everything runs **100% in your browser** —
your files never leave your device — so there's nothing to install.

### ▶ Open it: https://thesparda.github.io/Suikoden-Tactics-Editor/web/

Works on desktop and Android; installable as an offline PWA. You supply your own
legally-obtained game files — no game data, saves, or copyrighted binaries are in
this repo.

> **Feature requests / support:** Toran Castle Discord — https://discord.gg/KesHMX5P2Z

> **Desktop app:** the old local desktop editor has been **retired** — the web
> editor is the only supported version now. Past desktop releases remain under
> [Releases](https://github.com/TheSparda/Suikoden-Tactics-Editor/releases) and in
> git history.

---

## What it does

**Save editor** (works everywhere, including mobile) — opens every common export
format (`.sps` / `.xps` / `.cbs` / `.max` / `.psu`) and raw `.ps2` memory cards
(multi-slot). Edit party stats, equipment, runes, magic levels, recruitment
(incl. the Suikoden IV hero unlock), gold, skill points and the full inventory —
all as named dropdowns. Both game checksums (CRC-32 + MD5) and memory-card ECC
are recomputed on write.

**ISO editor** (desktop Chromium — needs the File System Access API) — edits the
disc **in place**: character growth ranks (with the "special" curves explained),
weapon type/growth, runes, innate element, battle-skill affinities, plus the
item / shop / rune-shop / price / skill / enemy tables. Reads only the bounded
editable region, writes back only the changed byte-runs, and can export a
`.stmod` recipe or a standard `.xdelta` patch.

See [`web/README.md`](web/README.md) for the full feature list and the depth
layer (guide-derived item/rune notes, shop identification, undo/redo, presets,
PWA/offline, tests).

## How it works

The save editor runs the project's Python save modules **unchanged** in
[Pyodide](https://pyodide.org) (CPython → WebAssembly). The ISO editor is a
JavaScript port of the same field tables (the disc is too large to load into
Pyodide). Nothing is uploaded; there is no server.

## Repository layout

| Path | Role |
|---|---|
| [`web/`](web/) | The editor — the only thing that ships. HTML/CSS/JS + PWA + tests |
| `st-editor/stsaveio.py`, `stsaveedit.py`, `stsave.py`, `stsavefields.py` | Save engine, run in Pyodide by the web save editor |
| `st-editor/stfields.py`, `stpatch.py` | ISO field schema + engine — the reference the web ISO editor is ported from (and its offset-verification test's oracle) |
| `st-editor/data/st_*.json` | Verified offsets + ID→name / reference data the web fetches |
| `st-editor/gen_iso_schema.py`, `build_*.py`, `apply_v11_labels.py` | Generators that produce the committed reference data |
| `st-editor/Suikoden_Tactics_ISO_offsets.md` | Reverse-engineering log |

## Development

Serve the **repository root** over HTTP (so `web/` can fetch `../st-editor/…`):

```bash
python3 -m http.server 8000
# open http://127.0.0.1:8000/web/
```

Tests live in `web/tests/` (`cd web && npm test`).

## Safety & privacy

Local-only; nothing leaves your device. The repo ships **no** game data — you
supply your own files. Editing game files can corrupt them: **back up your ISO,
saves, and memory cards** first. The ISO editor writes only changed bytes and can
export a recipe/patch, but keep your own backups regardless.

## Legal / disclaimer

Unofficial fan-made tool, **not affiliated with, endorsed by, or associated with
Konami** or any rights holder. *Suikoden* and *Suikoden Tactics* are trademarks
of their respective owners. You must **legally own** the game. Provided **"as is,"
without warranty of any kind** (see the license); use at your own risk.

## License

Released under the [MIT License](LICENSE).
