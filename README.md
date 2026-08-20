# Suikoden Tactics Editor

An **ISO** and **PS2 memory-card save** editor for *Suikoden Tactics*
(USA `SLUS-21245`, PAL `SLES-53769`), modeled on the
[Suikoden III Editor](https://github.com/TheSparda/Suikoden-3-Editor).

Local-only and stdlib-only: it runs a small web server on your machine and never
uploads anything. You supply your own legally-obtained game files — no game data,
saves, or copyrighted binaries are included in this repo.

> **Feature requests / Support** available on the Toran Castle Discord:
> https://discord.gg/KesHMX5P2Z

---

## Quick start

Requires Python 3.8+ (standard library only).

**Web UI** — double-click `st-editor/Start Editor (Mac).command` or
`st-editor/Start Editor (Windows).bat`, or:

```bash
cd st-editor && python3 steditor.py
```

Opens `http://127.0.0.1:8748/` (port 8748 so it can run alongside the S3 editor
on 8747). The page has two independent tools, selected by the **View:** tabs:

- **ISO editor** — enter/pick an ISO to edit the game's static data tables.
- **Save editor** — open a memory-card save (no ISO needed) to edit a playthrough.

Both **Open** buttons pop a native file picker if you leave the path empty.

---

## Save editor (fully reverse-engineered)

The save format is cracked and verified: a `0x20`-byte header (CRC-32 + a
byte-reversed MD5 of the payload, both recomputed on every write) followed by a
dump of the game-state RAM block. See
[`stsavefields.py`](st-editor/stsavefields.py) for the complete, commented map.

**Opens every common export format**, all decoders cross-verified against each
other (the same PAL save as `.xps`, `.cbs`, and `.max` decodes byte-identically):

| Format | Notes |
|---|---|
| `.sps` / `.xps` | SharkPort / X-Port (plain) — also repackable in place |
| `.cbs` | CodeBreaker (custom RC4 + zlib) |
| `.max` | Action Replay MAX / MAXDrive (`lzari`, ported to Python 3) |
| `.psu` | EMS / uLaunchELF export |

**Labeled editing** (names, not raw IDs, everywhere):

- **Party stats** — EXP, current/max HP, STR/SKL/MAG/EVA/PDF/MDF/SPD/LUC and the
  9 "plus" (equipment-bonus) stats.
- **Equipment** — 10 slots as item-name dropdowns.
- **Runes** — Head / Right / Left as rune-name dropdowns.
- **Magic levels** — overall + current, 4 slots each.
- **Recruitment** — per-character, **including the Suikoden IV hero unlock**
  (Lazlo's recruit flag; the game's import-unlocked secret characters).
- **Gold**, **skill points**, and the full **500-slot inventory** (type-ahead
  item names + quantities).

**Find your saves automatically** — *Scan for saves* walks the project folder and
the common PCSX2 memory-card directories, lists every ST save and `.ps2` card it
finds, and lets you click a result to open it or target a card folder for
injection.

**Write it back** three ways:

- **`.psu`** — import via mymc / uLaunchELF (works for any slot, incl. new ones).
- **`.sps` / `.xps`** — repack in place (byte-identical when unedited).
- **Inject into a memory card** — overwrite the game file inside an existing
  folder on a `.ps2` card, writing a **new** card image (your original is never
  touched). ECC is recomputed and the header slot number is matched to the
  target folder automatically.

> Format-level integrity (CRC-32 + MD5) is always recomputed, so edited saves are
> well-formed. As a final acceptance step, load one edited save in PCSX2 to
> confirm the game accepts it.

### Save CLI

```bash
cd st-editor
# inspect / verify any container (and cross-check decoders)
python3 stsaveio.py "save.xps"
# convert any format to .psu (universal import)
python3 stsaveio.py convert "save.max" out.psu
# verified field map: checksums, roster, gold, edit round-trip
python3 stsavefields.py "save.xps"
# memory card: list / find ST saves / inspect / verify ECC
python3 stsave.py "Mcd001.ps2" list
python3 stsave.py "Mcd001.ps2" find
python3 stsave.py "Mcd001.ps2" files "BASLUS-21245-00"
python3 stsave.py "Mcd001.ps2" ecc
# inject an edited (same-length) game file into an existing card folder
python3 stsave.py "Mcd001.ps2" putgame BASLUS-21245-00 edited.bin out.ps2
```

---

## ISO editor

Edits the game's static data, which all lives uncompressed inside
`FILEDATA.BIN` on the disc (the ELF holds no data-table constants; tables are
loaded to RAM at runtime, and for this dump the documented RAM addresses equal
the ISO file offsets).

- **Characters** — verified `0x280`-byte records (63+): move, stat-growth ranks,
  weapon type, weapon-growth curve, rune slots, **innate element** (`+0x5E`), and
  all **29 battle-skill ranks** (`+0xF1`, e.g. grant Extra Move to anyone). All
  shown as named dropdowns.
- **Data tables** — items, item prices, shops, rune shop, skills, enemies
  (recovered from the V11 community editor, addressed `base + id × stride`,
  verified against the ISO). Per-field write offsets are V11-authoritative;
  unverified field *meanings* are shown generically (`f{n}@+0xNN`) and edited at
  the byte level, records picked by name.
- **Hard Mode** — scale every character's growth ranks by a multiplier
  (idempotent; ×1 restores).
- **Reference** — the ID→name lookup lists used throughout the editor.

Staged edits with Save/Revert, changed-from-baseline highlighting + per-field
restore, search on every list, and light/dark themes. The first save copies the
ISO to `.bak`.

### ISO CLI

```bash
cd st-editor
python3 stpatch.py --iso "GAME.iso" validate
python3 stpatch.py --iso "GAME.iso" show Kyril
python3 stpatch.py --iso "GAME.iso" set Kyril str_growth 8
python3 stpatch.py --iso "GAME.iso" table-list
python3 stpatch.py --iso "GAME.iso" table-show itemprice 2
python3 stpatch.py --iso "GAME.iso" table-set itemprice 2 0x0 2 150
# also: list, find-bytes, dump-region, ids
```

---

## Modules

| File | Role |
|---|---|
| `steditor.py` | Web UI (stdlib HTTP server) — ISO + Save editors |
| `stpatch.py` | ISO validate / read / patch engine + CLI (`.bak` on first write) |
| `stfields.py` | `0x280` character-record schema + growth-rank / enum maps |
| `stsaveio.py` | Save-container I/O: read `.sps/.xps/.cbs/.max/.psu`, write `.psu`, convert |
| `stsaveedit.py` | SharkPort/X-Port open + in-place repack; game-file extraction |
| `stsavefields.py` | Verified save field map: read/write chars, globals, inventory + checksums |
| `stsave.py` | PS2 memory-card reader/writer: FAT, dir walk, Hamming ECC, `putgame` |
| `data/st_*.json` | Verified character offsets + ID→name reference lists |
| `Suikoden_Tactics_ISO_offsets.md` | Running reverse-engineering log |

---

## Building a release

The editor is packaged as a single stdlib **zipapp** (`.pyz`) — no PyInstaller,
no dependencies, just Python 3:

```bash
python3 make_release.py 1.0.0
```

This writes to `dist/`:

- `SuikodenTacticsEditorPackage.pyz` — the whole editor (modules + data) in one file
- `Start Editor (Mac, Linux).command` and `Start Editor (Windows).bat` — double-click launchers
- `SuikodenTacticsEditor-<version>.zip` — those three files, flat, ready to attach to a GitHub Release

End users just need Python 3 installed; they double-click the launcher for their
OS (or run `python3 SuikodenTacticsEditorPackage.pyz`). On first launch the
bundle self-extracts to a temp folder so its data tables load normally.

## Not yet done

- **Named** (vs generic) labels for the item/gear/skill/enemy **ISO** tables —
  needs a stat guide to confirm meanings; byte-level editing already works.
- **Rune spell effects** (power / range / area, e.g. Rune of Punishment) — these
  live in compressed archives on the disc, so they aren't editable directly in
  the ISO; the tractable path is a PCSX2 EE-RAM dump → locate → `.pnach` patch.

## Safety & privacy

Local-only; nothing leaves your machine. The repo's `.gitignore` excludes the
ISO, saves, memory cards, the community editor, and any decompiled artifacts —
only original code and reference data are committed.

## Legal / disclaimer

This is an unofficial fan-made tool. It is **not affiliated with, endorsed by, or
associated with Konami** or any rights holder. *Suikoden* and *Suikoden Tactics*
are trademarks of their respective owners.

- You must **legally own** a copy of the game. This project ships **no** game
  data, ROMs, ISOs, or saves — you supply your own files.
- Editing game files can corrupt them. **Back up your ISO, saves, and memory
  cards** before use. The ISO editor writes a `.bak` on first save, and the save
  editor writes to new files, but keep your own backups regardless.
- Provided **"as is," without warranty of any kind** (see the license). You use
  it at your own risk; the authors are not liable for lost data or damage.

## License

Released under the [MIT License](LICENSE).
