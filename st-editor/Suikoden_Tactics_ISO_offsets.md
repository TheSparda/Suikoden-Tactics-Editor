# Suikoden Tactics (SLUS-21245) — ISO offsets & RE log

Running reverse-engineering notes. Follow the methodology: **never write an
unverified field** — expose read-only or omit until its meaning is confirmed
against ground truth.

## ISO / ELF layout

- ISO: UDF, ~1.39 GB. Boot ELF `SLUS_212.45` at iso `0x92800`, entry `0x100008`,
  PT_LOADs: vaddr `0x401900` → iso `0x93100..0xF7CD8`; vaddr `0x100000` →
  iso `0xF8800..0x3FA0BC` (~3.5 MB total).
- **Editable data tables are NOT in the ELF** (unlike S3). They live in a large
  uncompressed data region ~`0x35Cxxxx`+ (≈56 MB into the ISO). For this dump the
  documented "addresses" equal **ISO file offsets directly** (verified).
- **No plain-ASCII name pool.** Item/char/rune names are not ASCII anywhere in the
  ISO or ELF (only debug/format strings in the ELF). String-anchoring (the S3
  method) does not apply — tables must be verified numerically / against an
  external stat guide.

## VERIFIED

### Characters — base `0x035C6CB8`, stride `0x280`, ~63+ documented
Verified across all 63 hex-doc entries via weapon-type fingerprint (54/54).
Field map (offsets into record) in `stfields.py` / `HexEditing.rtf`:
`+0x00` move, `+0x02..0x0A` growth ranks (HP,STR,SKL,MAG,EVA,PDF,MDF,SPD,LUCK),
`+0x0B` weapon type, `+0x0C..` weapon-growth curve, `+0x58/0x5A/0x5C` rune slots.
Growth-rank byte 0x01..0x0C (F..SS + special curves).

### Character record — MORE VERIFIED fields (2026-08-16, community hex post + walkthrough)
Source: suikosource thread t=14871 (AsbakBatu posted Jeremy's full record). Jeremy's
286 bytes matched our ISO at 0x035CCE38 exactly. Decoded + verified vs known chars:
- **Innate Element** at `+0x5E` (u8): 1=Fire, 2=Cyclone/Wind, 3=Earth, 4=Lightning,
  5=Water. Verified: Kika=2, Akaghi=1, Mizuki=5, Rita=4, Pablo=3.
- **29 battle-skill ranks** at `+0xF1` (29 × u8, one per skill, 0=off, 1-7 rank).
  Order = skill ids 1..29 (Counter Attack … Narcissism, `st_skills.json`). Verified
  vs guide: Kika idx0/1 (Counterattack/Parry), Akaghi idx6/27 (Battle Lust/Guard),
  Mizuki idx22/0 (Mind's Eye/Counterattack) — 6/6 constraints hit. This is how you
  "add Extra Move (skill_25) to a character": set its rank 1-7.
- Support skills (ids 30-35) exist but are noted useless by the community.
- Also per post: an affinity-rank byte for Rune of Punishment (01-07) exists — NOT
  yet pinned to an offset (no clean ground truth); left unexposed.
Both element + skills are now editable in the Characters tab (dropdowns), round-trip tested.

## RECOVERED FROM V11 (structure confirmed; field MEANINGS mostly UNVERIFIED)

Extracted from the V11 editor's IL. Addressing model per table:
`record_offset = base + id * stride`, where `id` = the decimal number in that
table's "NNN - name" reference list. Field widths from the load (`listN RTB_Click`)
methods. **Field meanings need empirical verification before enabling writes.**

| table | base | stride | record fields (width) |
|---|---|---|---|
| Items | `0x035F24A0` | `0x14` (20) | full 20-byte record exposed as u8 (supersets V11's item fields, which write across overlapping sub-blocks) |
| Item Price | `0x035FFF04` | `0x14` (20) | 1 × u16 (price; label "Price (x 10)") |
| Shop | `0x000B6560` | `0xA2` (162) | ~35 × u16 (item/price slots) |
| Rune Shop | `0x000B78C4` | `0x40` (64) | 24 × u16 |
| Skills | `0x03652E20` | `0x58` (88) | 18 × u16 |
| Enemies | `0x0363B8AC` | `0x11C` (284) | mixed u16 + u8 (list7_1..11, 41..46 u16; 49..54,61 u8) |

**VERIFIED (2026-08-13):** all six tables land on real, plausible ISO data
(read back via `stpatch.py table-show`). Self-verification examples:
- Item Price id2 (Mega Medicine)=15, id1 (Medicine)=2, id50=1000 → ×10 potch,
  cheap consumables vs expensive gear — confirms the "Price ×10" field.
- Shop rows = ascending item-id stock lists; Rune Shop rows = rune/orb id lists.
- Enemies fields are small (2-7) growth-rank-style bytes.
Per-field offsets extracted from `Patch1_Click` (single write handler for all
7 tables) and stored in `data/st_tables.json`. Exact write offsets are
V11-authoritative (byte-bounded). Field *labels* remain generic (`f{n}@+0xNN`)
except Price ×10 — meanings still need a stat guide to name confidently.
Editing is enabled (safe: byte-bounded + backup + round-trip tested), meanings
shown as generic until verified.

Character record extra fields (from Patch1_Click, cross-confirms stfields):
bytes `+0x00..0x11`, a 10×u16 block at `+0x2A..0x4E` (unknown; likely skill
caps / secondary stats), rune slots `+0x58/0x5A/0x5C`.

Known field labels seen in V11 UI: "Defense", "Attack Power", "Strength/Atk",
"Price (x 10)". Character growth tooltips: "How strong (S/A/B/C/D/E)".

### Field-naming verification attempt (2026-08-13, guides in `Guides/`)
Two GameFAQs guides supplied: Yeblos "Rune Abilities" + tumiwa "Orb/Rune".
These are **ability guides** (per-spell power/speed/range/area) and rune/orb
names — NOT stat-table guides (no per-item DEF/price, no in-game record IDs).
- Correlated the guide's magic-spell powers {30,35,45,50,55,60,70,80,100} vs the
  `skills` table fields: set-overlap was ambiguous (4 fields at 5-6/9), and the
  rigorous **ordered-sequence** test (Fire r2-4=35,30,55; Lightning r2-4=35,70,50)
  matched NOTHING; no field holds all of {55,70,80,100}. So the spell table isn't
  the `skills` table in a simple layout, or spells are keyed by IDs we lack.
- Verdict: cannot name numeric table fields to the ≥95% bar from these guides.
  Fields stay editable-but-generic. Rune ability data captured as reference
  (`data/st_rune_abilities.json` + `_flat.json`, in the Reference tab).
- To unlock naming: an item/equipment/enemy STATS guide (per-record DEF/ATK/
  price/HP numbers) OR a spell/skill list that includes in-game ID numbers.

### Character record sub-structure (mapped 2026-08-13 from records; NAMES unconfirmed)
Cross-confirmed: `+0x00` = MOV/movement range (Kika 6, Rita 5 per guide);
`+0x5C` left rune (Kika Falcon 0x11). New structural observations, all UNNAMED
candidates pending differential testing:
- `+0x1C..0x27`: three `(u16 const, u16 index)` pairs — const is 01/05/08 for
  every character; index is a per-character sequential trio (Kika 3E/3F/40,
  Akaghi 5C/5D/5E, Mizuki 61/62/63) → looks like pointers into another table.
- `+0x28..0x4F`: ten `(u16=0x0090, u16 value)` pairs. V11's editable "u16 block"
  (0x2A,0x2E,…,0x4E) is the *second* u16 of each pair; 0x0090 looks like a cap/marker.
- `+0x60..0x65`: six small bytes (2-4), candidate resistances.

### Why player guides don't name the table fields
The 3 guides in `Guides/` (2 rune-ability, 1 walkthrough) give DISPLAYED/computed
stats (per-level STR/SKL/HP, enemy HP-at-level) + ability text — but the ISO
tables store growth RANKS, caps, and indices, not displayed values. Verified
negative: skill-ID search in char records, and ordered power-sequence vs skills
table. **To name fields, need either a data-mining/hacking doc stating byte
meanings, or in-emulator differential testing (edit a byte on a clone, observe
in PCSX2).** Confirmed-meaning fields to date: character MOV/growth-ranks/weapon-
type/weapon-growth/rune-slots; Item Price (×10).

### RoP focused hunt (2026-08-16) — spell defs are NOT raw in the ISO
Ground truth (suikosource data page, user-supplied): RoP = Eternal Ordeal
(40 dmg, 1 unit), Double-Edged Sword (8x10 dmg, 3x3 zone), Voice of Death
(instant death, 1 unit), Everlasting Mercy (80 dmg enemies / 80 heal allies,
5x5 zone); ALL range 0-7; equipped Lazlo LH fixed.
Whole-ISO strided structural scans (power col 40,10,?,80 across 4 consecutive
records, any stride 4-128, cross-filtered by range col 6 or 7, area col
[?,3,?,5], hit-count 8, and other runes' power triples): 3983 raw power hits,
every cross-filter → 0 (one final candidate @0x2F1E914 disproved — high-entropy
noise). Combined with no-ASCII + absent skill curves + absent item DEF:
**spell/battle definitions are inside compressed/packed archives on disc** —
raw byte scans cannot see them, and in-place ISO editing of them may be
infeasible without cracking the archive format.
**Practical path to edit RoP range/area/damage:** find the table in EE RAM
(decompressed at runtime) — e.g. user dumps PCSX2 EE memory (32MB) during a
battle where Lazlo can cast RoP; run the same fingerprint scans on the dump
(should hit immediately); then either (a) locate the compressed source in the
ISO via the archive's directory, or (b) ship the edit as a .pnach RAM patch
(user already uses pnach cheats).

### Spell/rune-effect table — NOT located (2026-08-16)
Editing a rune's spells (power/range/cost/area) needs a spell-effect table like
S3's (S3 had one at file 0x3EC2A0, 94 records × 0x20). For ST:
- The `skills` table (0x3652E20) is VERIFIED NOT the spell table: no record holds
  the guide's (power,area) pairs together — (100,1),(55,13),(60,13),(80,13),(70,1)
  all absent. Single- and two-field correlations both failed.
- Blind fingerprint scan of the data region (0x3400000-0x3700000) for spell
  signatures = 306 noisy candidates, no clean stride/table. Inconclusive.
- Verdict: spell-effect table location unknown. To find it: a data-mining doc,
  or in-emulator differential testing (edit bytes, observe a spell in PCSX2), or
  a deeper dedicated hunt (S3's own rune→spell binding was only partially cracked).
- Follow-up (2026-08-16): correlated the guide's full (power,area) pair set
  across ALL 7 tables — noisy/no clean hit (small values coincide in items/shop/
  enemies). The `.pnach` only has Rune Modifier (equipped rune) + Max Magic Level
  codes, all in the save/party region 0x0067xxxx — NOT spell definitions.
  **Root cause content-search fails:** the guides give DISPLAYED results (area
  "13 panels", speed "Instant") but the game stores area as a shape-code and
  speed as an enum, so only `power` is near-literal and it's too common to
  pinpoint. Conclusion: the spell table can't be located from these guides;
  differential testing (or a byte-offset hacking doc) is required.

### Rune + ability tables — FOUND and editor-wired (2026-11-15) — supersedes the two sections above
The Aug verdicts above ("spell defs are NOT raw in the ISO", "table location
unknown") are **wrong**; both tables are plain, uncompressed disc data inside
FILEDATA's database segment, which sits verbatim in EE RAM from boot
(RAM = ISO − 0x2A45680). They were found by searching the RAM dumps for the
ability TEXT stream first (glyph encoding: u16 words, 0x8Axx = ASCII,
0xC400 = space, units end C100 FFFF), then solving the numeric table from
its string-index triples. Full detail: suikodentactics-debug/re/RE_NOTES.md
rounds 3–4.

- **Rune table @ ISO 0x35EDB80, 60 records × 24 bytes.** +0x00/+0x02 =
  (name, desc) unit pair (2k, 2k+1) into the rune text stream @ 0x35EF78A;
  +0x04 = value; +0x08 = packed field, semantics unknown (not exposed);
  **+0x12..+0x15 = the four ability ids the rune grants** (u8, 0 = none).
  Linkage verified against the ability table (elemental families exact;
  advanced runes share the basic rune's ladder shifted up; sword runes
  borrow elemental spells for levels 1–2; RoP lists (31, 33, 34, 35),
  skipping Double-Edged Sword 32 — observed, unexplained).
- **Ability table @ ISO 0x36105E0, 221 records × 80 bytes** (rune spells,
  techniques, unite attacks, enemy abilities). +0x30 = (name, short, long)
  text-unit triple (3n, 3n+1, 3n+2) into the abilities text stream
  @ 0x361EA74; **+0x44 = power** (u16), verified against the text stream's
  own "Effect: Power N" lines; heals store heal power. Three records
  disagree with their text (Furious Blow 110 vs 100, Double-Edged Sword
  30 vs 10, Everlasting Mercy 0) — flagged in the UI as untested.
  **Round 5 (2026-11-15): +0x3A = element** (u16; char-record codes
  1 Fire, 5 Water, 2 Wind, 3 Earth, 4 Lightning, 9 Punishment — perfect
  family blocks) and **+0x38 = effect class** (u16; 0x0101 terrain
  change, 0x0401 standard, 0x1601 terrain-wide, 0x1801 death, 0x1901
  random panels) — both editor-exposed. **Range, area/radius and casting
  speed are provably NOT fields of this record** (difference-set proof
  against guide + text ground truth; see RE_NOTES round 5) — they live
  in the effect data the record references. The editor shows verified
  range/area/speed as read-only reference data in st_rune_spells.json
  ("ref" per ability: speed from the game's own "Casting time:" text,
  range/area from the Neoseeker rune guide v1.6 cross-checked).
- **Skills table base corrected: 0x3652E10** (the V11 framing 0x3652E20
  was +0x10 off), 36 records × 0x58: string pair (2k, 2k+1), then 6 rank
  tiers × 12 bytes (effect A/B/C + SP cost; the rank-E tier's cost slot
  holds its C value, so only ranks D–S have SP costs).
- Editor wiring: `st_tables.json` gained `runes` + `abilities` and the
  corrected `skills`; names/descriptions are decoded from the game's own
  text streams by `build_rune_spells.py` into `data/st_rune_spells.json`;
  the web editor has Runes and Spells tabs (spell-assignment pickers,
  value, power) on top of the generic table machinery.

### Area shape table — FOUND and editor-wired (2026-11-15, round 7)
- ISO 0x35FDC00, 0x22F0 bytes, loaded in battle as segment [26] to RAM
  0xBB8580 (occurs exactly once in the ISO). 142 shapes. Layout: u32 zero,
  u32 count (142), count x u32 offsets relative to the first shape block,
  then per shape {u32 pair_count, pair_count x (s8 dx, s8 dy)} in a padded
  block; a shape can grow in place up to its block capacity
  ((next_offset - offset - 4) / 2 pairs; 345 spare pairs across the table).
- A spell's AREA is the offset list of the shape it maps to, expanded
  around the target tile by the targeting code (expander func_002C7E08 via
  the accessor func_002C6110; the action object it fills is a 0x321C-byte
  struct in a 16-slot pool, panel count at +0x00, pairs at +0x0E).
  Shape families: 0-8 = centered diamonds radius 0-8 (1/5/13/25/41/61/85/
  113/145 panels), 40/100/110/133/135 = 3x3 squares, 45/74/111/134 = 5x5
  squares, 46 = 7x7, plus lines, cones, fans and directional variants.
- Verified live in PCSX2 by repointing shape offsets in RAM: Eternal
  Ordeal (ability 31) uses shape 0 (repointed to shape 1's data, its
  targeting area became 5 panels); Double-Edged Sword (ability 32) uses
  shape 133 (repointed to shape 0's data, its area became 1; shapes 40,
  100, 110, 135 ruled out). The full table was byte-matched between the
  RAM dump and this ISO offset, so editor edits here are what the game
  loads.
- Which shape a spell uses is NOT stored per spell in any table found
  (ability record combo-patch proof in round 6; exhaustive RAM scan for a
  mapping array in round 7) — it is picked in code. Spell RANGE is also
  code-side: a flood-fill distance limit (builder func_002C7AA0 /
  func_002C9480), not a shape and not an ability-record field (idx8/9/14/
  16/17 patch-tested live in round 7; idx17 is a targeting-mode enum).
  Range therefore stays read-only reference in the editor.
  SUPERSEDED 2026-11-15 night (round 9, RE_NOTES.md): the range verdict
  above is wrong. Range IS stored per spell, in the descriptor table
  below; the flood-fill functions named here build the MOVEMENT zone,
  not the spell zone. Range is editor-writable as of round 9.
- Editor wiring: `stpatch.decode_shapes()` is the Python reference
  decoder; the web editor has a Shapes tab (selector, grid preview,
  panel-count + per-panel dx/dy editing within block capacity) reading
  the region via collectRanges (SHAPE_BASE/SHAPE_TOTAL in web/iso.js).

### Spell descriptor table (range) — FOUND and editor-wired (2026-11-15, round 9)
- Runtime segment 0xCD74. Header at ISO 0x3610610: s16 +0 = 1 (type),
  s16 +2 = 0x50 (stride), s16 +4 = 221 (count). Record for ability id
  (1-based; record 0 is a dummy) at 0x3610610 + id x 0x50. The grid is
  shifted +0x30 against the ability table (base 0x36105E0), which is why
  per-record hunts missed it: range(k) is the s16 at ability record
  (k+2) +0x10 in the ability grid. Address this table on its own base,
  never through the ability grid.
- **Spell RANGE = s16 at record +0x30** (ISO 0x3610640 + id x 0x50).
  The value is a shape-library index: the targeting zone is that
  diamond filtered by on-map + height difference < 3 (terrain-wide
  spells store 47). Verified three ways (RE_NOTES round 9): guide
  correlation 34/34; RAM A/B (Eternal Ordeal 6 -> 2 shrank its zone
  54 -> 13 panels); fresh-boot ISO A/B (1-byte patch at 0x3610FF0).
- Area shape index candidate = s16 at record +0x32: matches the live
  shape for 31/34 spells, but the three Rune of Punishment spells
  disagree (Double-Edged Sword field 0 vs live 133) and the consumer
  path is not cleanly proven, so this field is NOT editor-exposed.
- Game accessors: func_0031F3C8 (record resolve), func_0031F5E8
  (+0x30 mode 0 with a grade-up ladder = the likely S-rank +1 route;
  +0x32 mode 1), func_002BA1B0, area builder func_002C8528. In RAM the
  segment data is at 0xBCAF80 (records 0xBCAF90); segment pointers are
  cached in the hash table at RAM 0x4AAE8C.
- Editor wiring: st_tables.json "descriptors" + a Range number field
  on the Spells tab (web/iso.js); stpatch.py DESC_BASE/DESC_STRIDE.

### Open questions / to verify
- Exact per-field byte offsets within each record (parser saw explicit re-seeks;
  pull literal offsets from each table's patch/write method).
- Field *meanings* for Items/Skills/Enemies (mostly unlabeled NUDs) — verify
  against a suikosource/GameFAQs stat guide before writing.
- Record counts per table (how many valid entries before the table ends).
- Item table gaps (fields 3,4) — likely a u16 or reserved.

## Disc filesystem (ISO9660, parsed 2026-08-16)
20 files. All game data is in **`FILEDATA.BIN`** (495 MB, iso `0x4B0000`) — the
char/item/skills/enemy tables are uncompressed sub-regions of it (char table iso
`0x35C6CB8` falls inside FILEDATA.BIN's extent; the "RAM address == iso offset"
identity holds because that documented address IS the iso offset). `SD.BIN`
(816 MB) is audio; `SLUS_212.45` the ELF; `MOVIE/*.PSS` FMV; `IOP/*.IRX` modules.
- FILEDATA.BIN has **no simple front-loaded TOC**: it opens with an embedded
  SYSTEM.CNF (`BOOT2 = cdrom0:\SLPM_661.05` — the JP boot id) then binary; no
  monotonic u32 offset array at the head. Cracking its full sub-archive format is
  a large, uncertain job (deferred).
- **ELF references NO data-region address constant** (scanned every lui+addiu/ori/
  lw/sw pair for targets in 0x3400000-0x3A00000 → 0 hits). So tables are reached
  via a runtime-loaded buffer pointer, not compile-time constants — confirming the
  spell table can't be found by "which code constant points at it."

## RAM party map — RECOVERED from the PCSX2 pnach (2026-08-16)
The GameHacking.org pnach is a full **party-record RAM map** (EE work RAM, real
addr = code addr & 0x0FFFFFFF, all in `0x006xxxxx`). Two parallel arrays, **stride
`0x188` (392 B)**, indexed by party slot; 75 named characters mapped in
`data/st_ram_party_map.json`.
- **STAT block** (base = char's Have-Character addr − 0x50; Kyril-Adult `0x670CF8`,
  S4 Hero/Lazlo `0x676130`): `+0x00` EXP(u32), `+0x08` HP cur(u16), `+0x0A` HP
  max, `+0x0C..0x1A` STR/SKL/MAG/EVA/PDF/MDF/SPD/LUC (u16), `+0x1C..0x2C` Plus-
  stats (same order, u16), `+0x50` **Have-Character flag (u8, 0xE1=have)**.
- **EQUIP/RUNE/MAGIC block** (base = char's Equipment-Body addr, e.g. `0x678BEE`):
  `+0x00` Body, `+0x02` Hands, `+0x04..0x12` Other 1-8 (u16 item ids); `+0x14`
  Rune Head, `+0x15` Rune Right, `+0x16` Rune Left (u8 rune ids — same id space as
  the char-definition rune slots, e.g. RoP=0x0B); `+0x17..0x1A` Magic overall
  Slot1-4, `+0x1B..0x1E` Magic current Slot1-4 (u8).
- **Suikoden IV import / secret-character unlock = the Have-Character flags.** The
  S4 Hero (Lazlo) and the S4-crew characters each have a Have-Character byte;
  setting it to 0xE1 recruits them — this is the concrete mechanism behind the
  "flag save as S4-imported" requirement.
**Implication for the save editor:** this RAM layout is the serialized party block
the save file mirrors. Deliverable now as a PCSX2 RAM/pnach editor (matches how
the user already plays); once an actual ST save is supplied, search it for this
known structure to find the file offset + checksum.

## Save layer (separate RE; see feedback-re-methodology)

**DONE — `stsave.py` PS2 memory-card reader (game-agnostic, transfers verbatim):**
superblock, indirect-FAT/FAT cluster chains, root-directory walk, file read, and
per-page Hamming ECC. Tested on a real card (`Mcd001 (Main).ps2`) — correctly
lists 69 save folders; ECC round-trips. CLI: `python3 stsave.py CARD.ps2 list|find|files`.

### Real ST saves FOUND + SharkPort codec (2026-08-16) — `Saves/Saves/ST Saves/`
15 saves in `.sps`/`.xps` (SharkPort/X-Port), `.max` (AR-MAX), `.cbs` (CodeBreaker),
`.zip`. Both USA (`BASLUS-21245-NN`) and PAL (`BESLES-53769-NN`). USA matches our
ISO/pnach. `stsaveedit.py` opens `.sps`/`.xps`, extracts the 54960-byte game data
file, and repacks byte-safe — **identity round-trip OK on all 4 tested saves**
(SharkPort trailing checksum is proprietary but importers ignore it, so it's
preserved verbatim).
**VERIFIED in the save file:** a party array at **stride 0x188** (dominant
autocorrelation period, 92%; matches the pnach RAM stride 0x188). Rune-slot-range
(0x00..0x36) bytes cluster at record offsets ~0x88..0xC1. Diffing USA Finale vs
NG+ shows a per-record mutable field at +0x179 (88/90 records) + a 0x128..0x154
cluster (active-party subset).
**NOT YET PINNED (blocks labeled stat/skill/rune/recruit editing):** the exact
record *phase* (true boundary) and per-field offsets. Autocorrelation gives the
region+stride but two locator heuristics disagree on phase (0x1631 vs 0x2316) and
the recruited-flag column doesn't surface cleanly at either — so field offsets are
unverified. **Unblock = one ground-truth anchor:** open a known save in PCSX2 and
read one character's exact Level/EXP/HP(cur,max)/STR/SKL/MAG + equipped runes;
searching those literals in the record locks the phase + every column in one pass.
Alternative = controlled differential (edit byte, observe in PCSX2). The game's
own internal save checksum (if any) is a separate open risk to test with a
controlled edit before trusting in-game loads.

### Save-container pipeline — DONE + cross-verified (2026-08-16)
`stsaveio.py` reads every export format the user has and converts between them:
- `.sps`/`.xps` (SharkPort/X-Port, plain), `.cbs` (CodeBreaker: custom-RC4 + zlib),
  `.max` (AR-MAX/MAXDrive: **lzari** — full Py3 decode port), `.psu` (EMS, plain).
- Writer: `.psu` (universal uLaunchELF/mymc import); `.sps` in-place repack via
  `stsaveedit.SharkPort` (same-length, byte-identical round-trip).
- **Correctness proven by cross-decode:** the one PAL save exists as `.xps`+`.cbs`
  +`.max`; all three decode to a byte-identical 54960-B game file (crc 969A6538).
  `.max`->`.psu`->reread round-trips every file identically.
CLI: `python3 stsaveio.py convert IN.(sps|xps|cbs|max) OUT.psu`;
`python3 stsaveedit.py <anysave> party` (locates the 0x188 party array on any fmt).
Inject-to-card: `stsave.py putgame CARD FOLDER EDITED.bin OUT.ps2` writes an
edited (same-length) game file into an EXISTING card folder, emits a NEW card
image, and self-verifies (re-read byte-identical + ECC 16095/16096). Verified on
`PS2 Memory Card.ps2` which already holds `BASLUS-21245-00..03`. Adding a save to
a card that LACKS the folder is intentionally delegated to `.psu` export + mymc/
uLaunchELF (their allocator is proven) rather than reimplementing FAT allocation
here — lower risk of silent card corruption.
Still open: naming the save-record fields (needs the PCSX2 ground-truth anchor).

### SAVE FORMAT CRACKED + VERIFIED (2026-08-16) — `stsavefields.py`
Solved WITHOUT an emulator anchor, by combining three sources:
1. **ELF getter table** at vaddr `0x320A00+` (tiny `lui/jr/addiu` accessors) lists
   the game-state globals: `0x66E9F0` (state base), `0x670CA8` (party array),
   `0x6794F0` (skill points), `0x6795B0` (inventory-8), etc.
2. **pnach RAM map** re-derived precisely: ONE record per character, stride
   0x188; equip/rune/magic are IN-RECORD (Body +0x2E, Rune Head/R/L +0x42/43/44,
   Have-Character +0x50), not a parallel array (earlier sub-layout was anchored
   to the wrong list entry).
3. **Shift solve + hard discriminator**: save = `[0x20 header][RAM dump from
   0x66E9F0]`, i.e. `save_off = RAM − 0x66E9F0 + 0x20`. Proven by Lazlo (party
   idx 55): rune Left = 0x0B (fixed Rune of Punishment) + 0 invalid rune bytes
   across 89 records in all three saves; only H=0x20 passes.
**Header/integrity (verified on 6 saves):** `+0x0C` u32 = CRC-32(payload);
`+0x10..0x1F` = MD5(payload) byte-reversed; `+0x08` low16 = slot number. Both
digests recomputed by `stsavefields.fix_checksums()` (identity on unedited).
**Record layout (all verified):** +0x00 EXP u32 (level≈EXP/1000+1, matches save
titles); +0x08/0x0A HP cur/max; +0x0C..0x1A STR/SKL/MAG/EVA/PDF/MDF/SPD/LUC u16;
+0x1C..0x2C plus-stats ×9; +0x2E..0x40 equipment ×10 (item ids, DECIMAL-keyed
st_shop_items — Kyril endgame = Silver Mail/Ogre Breath/Berserker Belt ✓);
+0x42/43/44 runes; +0x45..0x4C magic overall/current ×4+4; **+0x50 bit0 =
recruited (0xE1/0xE0) — Lazlo 0xE0 in every real save → setting bit0 IS the
Suikoden IV import unlock.**
**Globals:** gold u32 @ save 0xAB28 (PAL save holds 99,429,989 ≈ maxed ✓);
skill points u32 @ 0xAB20; inventory @ 0xABE8 = 500 × (u16 item id, u16 qty).
**Editor:** Save tab does labeled editing of all of the above, with save/card
scanner (`/api/save_scan`), native file picker (`/api/save_browse`), name
dropdowns for gear/runes and datalist for inventory; verified end-to-end in
browser (gold/recruit-Lazlo/inventory edits land, checksums valid).
Remaining acceptance: load an edited save in PCSX2 once to confirm the game
accepts it (format-level double-digest validation passes).

**RESOLVED (was blocked) — game-specific save editing + Suikoden IV import flag:**
- Requires an actual Suikoden Tactics save to diff / format docs. None of the
  local cards contain an ST (SLUS-21245) or S4 save, and stat/guide sites are
  inaccessible here (bot-blocked / 402).
- To finish: obtain an ST save (ideally two — one with S4 import, one without),
  diff to find the import/carryover flag byte and the party/gold/inventory/
  recruitment offsets, then crack the checksum by playtime-ordered diff (find the
  word that zeroes the sum). pnach party map (`0x0067xxxx`) is the RAM-side hint.

## Text encoding CRACKED + S4 hero name / import (2026-08-21, real ISO + saves)

### Game text encoding — 16-bit `0x8A` font page
Game strings (names, dialogue) are **16-bit LE glyphs**: high byte `0x8A` (the
standard font page), **low byte = ASCII − 0x20**; space = `0x8A00`. Control/format
codes use other high bytes (`0x00C1`, `0xFFFF`, `0x00C4` = terminators/markers).
This is why plain-ASCII/UTF-16 scans found nothing (the old "no ASCII name pool"
note). Decode: `chr(low + 0x20)` when `hi == 0x8A`. Verified by decoding whole
battle scripts and the per-character weapon/name table at ISO `0x035E38C0`
(char-ID order: Andarc→Blade Rod/Axe/Halberd, Seneca→Honeybee/Wasp/Hornet, …).
- **FILEDATA.BIN** (ISO `0x004B0000`, 472 MB) holds the resident DB. A packed
  script archive at `0x03040890` uses a TOC of `(u16 id, 6×00, u32 off, u32 size)`
  records; resource data starts at TOC-base + off. Names are also embedded as
  literals in every map's dialogue (speaker tags), so they are **massively
  duplicated + variable-length** → a clean in-place "rename any character" is not
  feasible without rebuilding pointer tables. Not attempted.

### Imported S4 hero name — SAVE field `0x58` (ASCII)
The imported hero name is stored **in the save**, not the ISO, as plain ASCII in a
`0x11`-byte slot at save `0x58` (real saves: "Sparda"; next metadata field "Sta"
at `0x69`, then "Basel" at `0x7A`). `stsavefields.read/write_hero_name`, capped 16,
NUL-padded, stays within the slot. **The in-game "L a z l o" spacing is a Tactics
render bug in its ASCII-name path — the stored bytes are already space-free, so no
save edit fixes it; the real fix is a `.pnach` code patch (needs EE-RAM RE).**

### "Save data imported" = Have-Character flags for idx 55 + 56
Diffed 3 imported vs 1 non-imported save (same card): the only import-correlated
party bytes are **idx 55 (S4 Hero/Lazlo) and idx 56 (Snowe, Adult)** `+0x50`, each
`0xE0`→`0xE1`. So the import bonus unlocks BOTH. `stsavefields.set_s4_import` /
`s4_import_enabled` toggle both bit0s. (Global bytes `0x1070/78/7A` also differ but
are unverified — left unwritten per verify-before-write.) Editor: **S4 Save Data**
section (import toggle + hero rename), round-trip tested end-to-end via HTTP.

### "L a z l o" spacing bug — render path traced (ELF), fix NOT shipped
Disassembled `SLUS_212.45` (capstone MIPS32-LE, skipdata; code seg file `0x66000`
↔ vaddr `0x100000`). The imported-name field render chain:
- Menu dispatcher `0x00320994` loads the name RAM addr `0x0066EA28` (= save `0x58`)
  and draws it + `+0x11`/`+0x22` ("Sta"/"Basel") — independently reconfirms the
  0x11-byte slot stride.
- **Name builder `0x0013B560`** (4 callers, all name menus): copies the ASCII name
  into a temp buffer (loop @ `0x13B5EC`; byte `<0xA0` = literal, `>=0xA0` =
  sprintf-escape via `0x00111FE8`, a varargs wrapper → `0x001128C0`). Default name
  "Hero" @ data `0x00464B40` (plain ASCII, same path).
- Hands the C-string to **shared text engine `0x001314C0`** — **14 callers**, so its
  per-glyph advance can't be patched without affecting other UI text.
Conclusion: the spacing is `0x1314C0`'s advance for raw-ASCII glyphs; a safe fix must
live in the name-specific `0x13B560` path or a width table, and the exact advance/
which on-screen text is affected needs a PCSX2 runtime trace (breakpoint `0x13B560`
or read-watch `0x0066EA28`) to confirm before writing a `.pnach`. Not shipped in v1.1.0.
