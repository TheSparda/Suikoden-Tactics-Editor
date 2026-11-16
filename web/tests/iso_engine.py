#!/usr/bin/env python3
"""ISO editor engine tests — ships NO game data.

1. Schema-drift guard: the committed data/st_char_schema.json (consumed by the
   web ISO editor) must exactly match what gen_iso_schema.py produces from
   stfields.py — so the JS port can't silently drift from the Python offsets.
2. Patch-engine sanity: build a SPARSE synthetic disc (real offsets, ~0 disk)
   with the SLUS marker + Kyril fingerprint, then drive the real stpatch
   ISOEditor read/decode/set at those offsets.
"""
import os, sys, json, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ST_EDITOR = os.path.normpath(os.path.join(HERE, "..", "..", "st-editor"))
sys.path.insert(0, ST_EDITOR)

import gen_iso_schema
import stfields
import stpatch

PASS, FAIL = [], []
def ok(cond, label):
    (PASS if cond else FAIL).append(label); print(("  PASS " if cond else "  FAIL ") + label)

print("=== schema drift ===")
committed_path = os.path.join(ST_EDITOR, "data", "st_char_schema.json")
committed = json.load(open(committed_path, encoding="utf-8"))
fresh = gen_iso_schema.build()
ok(committed == fresh, "committed st_char_schema.json matches stfields.py (regenerate with gen_iso_schema.py)")
ok(committed["record_len"] == stfields.RECORD_LEN, "schema record_len = 0x%X" % committed["record_len"])
ok(all(f["offset"] + f["w"] <= committed["record_len"] for f in committed["fields"]), "all fields fit inside the record")

print("\n=== patch engine on a sparse synthetic disc ===")
chars = stpatch.load_characters()
tables = stpatch.load_tables()
kyril = next(c for c in chars if c["name"].lower() == "kyril")

fd, path = tempfile.mkstemp(suffix=".iso"); os.close(fd)
try:
    # size to cover the last editable byte; sparse so it costs ~nothing on disk
    max_off = max(
        [c["offset"] + stfields.RECORD_LEN for c in chars if "offset" in c] +
        [t["base"] + t["count"] * t["stride"] for t in tables.values()]
    )
    with open(path, "r+b" if os.path.exists(path) else "w+b") as f:
        f.truncate(max_off + 0x100)
        f.seek(0x8000); f.write(stpatch.SLUS_MARKER)                 # region marker
        f.seek(kyril["offset"] + 0x0B); f.write(bytes([0x0E]))       # fingerprint
        f.seek(kyril["offset"] + 0x03); f.write(bytes([0x05]))       # STR growth = B

    ed = stpatch.ISOEditor(path)
    good, m = ed.validate()
    ok(good, "validate() passes on synthetic disc: %s" % m)

    d = ed.decode_char(kyril)
    ok(d["weapon_type"]["value"] == 0x0E, "decodes Kyril weapon_type byte")
    ok(d["str_growth"]["value"] == 0x05, "decodes STR growth = %s" % d["str_growth"]["display"])

    ed.set_field(kyril, "str_growth", 8, backup=False)
    ok(ed.decode_char(kyril)["str_growth"]["value"] == 8, "set_field STR growth -> SS persists")

    ed.set_weapon_growth(kyril, 1, 0x2A, backup=False)
    ok(ed.read_record(kyril)[stfields.WEAPON_GROWTH_OFFSET] == 0x2A, "set_weapon_growth Lv1 persists")

    # a generic table (items) round-trips at its real offset
    name = "items"
    ed.set_table_field(name, 1, 0x00, 1, 0x37, backup=False)
    rec = ed.decode_table_record(name, 1)
    ok(rec["fields"][0]["value"] == 0x37, "table set/read round-trip (items id=1 @+0x00 = 0x37)")

    # rune table (RE 2026-11-15): spell-assignment bytes round-trip
    ed.set_table_field("runes", 1, 18, 1, 31, backup=False)   # Fire Rune L1 -> Eternal Ordeal
    rec = ed.decode_table_record("runes", 1)
    by_off = {f["off"]: f["value"] for f in rec["fields"]}
    ok(by_off[18] == 31, "runes set/read round-trip (Fire L1 spell = 31)")

    # ability table (RE 2026-11-15): power field round-trips
    ed.set_table_field("abilities", 31, 0x44, 2, 999, backup=False)
    rec = ed.decode_table_record("abilities", 31)
    ok(rec["fields"][0]["value"] == 999, "abilities set/read round-trip (Eternal Ordeal power = 999)")

    # ability element + effect class (RE round 5, 2026-11-15): the two other
    # verified record fields round-trip at +0x3A / +0x38
    ed.set_table_field("abilities", 25, 0x3A, 2, 3, backup=False)   # Lightning Force -> Earth
    ed.set_table_field("abilities", 25, 0x38, 2, 257, backup=False)  # -> terrain-change class
    rec = ed.decode_table_record("abilities", 25)
    by_off = {f["off"]: f["value"] for f in rec["fields"]}
    ok(by_off[0x3A] == 3 and by_off[0x38] == 257,
       "abilities set/read round-trip (Lightning Force element = 3, effect class = 257)")

    # area shape table (RE round 7, 2026-11-15): 142-entry (dx, dy) offset-list
    # library at 0x35FDC00; decode a synthetic 2-shape table poked into the disc
    import struct as _struct
    with open(path, "r+b") as f:
        f.seek(stpatch.SHAPE_BASE)
        f.write(_struct.pack("<II", 0, 2))                 # zero word + count
        f.write(_struct.pack("<II", 0, 12))                # block offsets
        f.write(_struct.pack("<Ibb", 1, 0, 0) + b"\x00" * 6)   # shape 0: 1 pair, block 12
        f.write(_struct.pack("<Ibbbb", 2, 1, 0, 0, 1))     # shape 1: 2 pairs
    shapes = stpatch.decode_shapes(ed.read_bytes)
    ok(len(shapes) == 2 and shapes[0]["pairs"] == [[0, 0]] and shapes[0]["capacity"] == 4
       and shapes[1]["pairs"] == [[1, 0], [0, 1]],
       "shape table decode (count/offsets/pairs/capacity)")

    # spell descriptor table (RE rounds 9-10): range = s16 at record +0x30,
    # area shape = s16 at +0x32; the Rune of Punishment spells after Eternal
    # Ordeal (abilities 32-34) read the record one above their ability id.
    with open(path, "r+b") as f:
        for rec, rng, area in ((31, 6, 0), (32, 6, 0), (33, 6, 133), (34, 6, 0), (35, 6, 134), (12, 4, 46)):
            f.seek(stpatch.DESC_BASE + rec * stpatch.DESC_STRIDE + stpatch.RANGE_OFF)
            f.write(_struct.pack("<hh", rng, area))
    ok(stpatch.decode_spell_range(ed.read_bytes, 31) == 6
       and stpatch.decode_spell_area(ed.read_bytes, 31) == 0,
       "descriptor decode, unshifted (Eternal Ordeal record 31)")
    ok(stpatch.decode_spell_range(ed.read_bytes, 32) == 6
       and stpatch.decode_spell_area(ed.read_bytes, 32) == 133,
       "descriptor decode, RoP shift (Double-Edged Sword reads record 33)")
    ok(stpatch.decode_spell_area(ed.read_bytes, 34) == 134,
       "descriptor decode, RoP shift (Everlasting Mercy reads record 35)")
    ok(stpatch.decode_spell_area(ed.read_bytes, 12) == 46
       and stpatch.spell_area_offset(12) == stpatch.DESC_BASE + 12 * stpatch.DESC_STRIDE + 0x32,
       "descriptor decode, unshifted (Silent Lake record 12)")

    # casting time = s16 at descriptor record +0x3C (RE round 11), same
    # routing as range/area including the RoP shift: record 33 carries
    # Double-Edged Sword's 4, record 31 carries Eternal Ordeal's 0.
    with open(path, "r+b") as f:
        for rec, ct in ((31, 0), (33, 4), (35, 10)):
            f.seek(stpatch.DESC_BASE + rec * stpatch.DESC_STRIDE + stpatch.CASTTIME_OFF)
            f.write(_struct.pack("<h", ct))
    ok(stpatch.decode_spell_casttime(ed.read_bytes, 31) == 0,
       "casttime decode, unshifted (Eternal Ordeal record 31 = 0)")
    ok(stpatch.decode_spell_casttime(ed.read_bytes, 32) == 4
       and stpatch.spell_casttime_offset(32) == stpatch.DESC_BASE + 33 * stpatch.DESC_STRIDE + 0x3C,
       "casttime decode, RoP shift (Double-Edged Sword reads record 33)")
    ok(stpatch.decode_spell_casttime(ed.read_bytes, 34) == 10,
       "casttime decode, RoP shift (Everlasting Mercy reads record 35)")

    # skills table sits at the corrected base 0x3652E10 (V11 framing was +0x10)
    ok(tables["skills"]["base"] == 0x3652E10 and tables["skills"]["count"] == 36,
       "skills table base/count corrected (0x3652E10 x36)")

    # out-of-range rejected
    try:
        ed.set_field(kyril, "str_growth", 999, backup=False); ok(False, "rejects out-of-range u8")
    except ValueError:
        ok(True, "rejects out-of-range u8 write")
finally:
    os.remove(path)

print("\n================ SUMMARY ================")
print("PASS: %d   FAIL: %d" % (len(PASS), len(FAIL)))
for f in FAIL:
    print("  - " + f)
sys.exit(1 if FAIL else 0)
