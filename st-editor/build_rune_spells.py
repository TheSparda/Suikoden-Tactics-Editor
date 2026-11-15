#!/usr/bin/env python3
"""Build data/st_rune_spells.json for the web ISO editor's Runes/Spells tabs.

Names are NOT taken from guides: they are decoded from the game's own text
streams inside the ISO (the same streams the game renders), so what the
editor shows is what the disc says.

  rune names/descs   : stream at ISO 0x35EF78A, 2 units per rune
                       (name, description); rune record k uses units (2k, 2k+1)
  ability names      : stream at ISO 0x361EA74, 3 units per ability
                       (name, short desc, long "Effect:" desc);
                       ability record n uses units (3n, 3n+1, 3n+2)

Glyph encoding (RE 2026-11-15): u16 words; 0x8Axx = ASCII (xx + 0x20),
0xC400 = space, 0xC000 = newline, 0xE6xx = element icon, FFFF = hidden
marker, units terminated by the word pair C100 FFFF.

Usage: python3 build_rune_spells.py [path-to-iso]
Writes data/st_rune_spells.json next to the other data files. The ISO is
only read, never written.
"""
import json, os, re, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ISO = os.path.join(HERE, "..", "ISO", "Suikoden Tactics (USA).iso")
OUT = os.path.join(HERE, "data", "st_rune_spells.json")

RUNE_STREAM = 0x35EF78A
RUNE_COUNT = 60
ABILITY_STREAM = 0x361EA74
ABILITY_COUNT = 221
STREAM_SPAN = 0x40000  # generous read window; units are consumed by count


def decode(unit):
    out = []
    for w in unit:
        if w == 0xC400:
            out.append(" ")
        elif w == 0xC000:
            out.append("\n")
        elif (w >> 8) == 0x8A:
            out.append(chr((w & 0xFF) + 0x20))
        # FFFF hidden markers, E6xx element icons and other control words
        # carry no text of their own — dropped.
    return "".join(out).strip()


def read_units(f, start, count):
    f.seek(start)
    words = struct.unpack("<%dH" % (STREAM_SPAN // 2), f.read(STREAM_SPAN))
    units, cur, i = [], [], 0
    while i < len(words) and len(units) < count:
        if words[i] == 0xC100 and i + 1 < len(words) and words[i + 1] == 0xFFFF:
            units.append(decode(cur))
            cur = []
            i += 2
        else:
            cur.append(words[i])
            i += 1
    if len(units) < count:
        raise SystemExit("stream at 0x%X yielded %d units, expected %d" % (start, len(units), count))
    return units


# Range / area reference values (RE 2026-11-15, round 5). These are NOT read
# from the ability record: a field-by-field correlation against the game's own
# "Effect:" data sheets proved range, area and casting speed are not stored as
# plain fields in the 80-byte ability record (they live in the effect data the
# record points at). The values below are the verified reference numbers from
# the Neoseeker "Rune Abilities Guide" v1.6 (Yeblos), cross-checked against
# the game's own text; the editor shows them read-only next to each spell.
# "All" = the terrain-wide spells (infinite range, every terrain panel).
GUIDE_RANGE_AREA = {
    1: ("4", "5"), 2: ("4", "13"), 3: ("4", "1"), 4: ("All", "All terrain"),
    5: ("4", "13"), 6: ("4", "13"), 7: ("4", "5"), 8: ("4", "13"),
    9: ("4", "1"), 10: ("4", "9"), 11: ("4", "9"), 12: ("4", "49"),
    13: ("4", "5"), 14: ("4", "13"), 15: ("6", "5"), 16: ("6", "5"),
    17: ("6", "13"), 18: ("All", "All terrain"), 19: ("4", "5"),
    20: ("4", "13"), 21: ("4", "5"), 22: ("4", "1"), 23: ("4", "13"),
    24: ("4", "13"), 25: ("4", "5"), 26: ("4", "13"), 27: ("5", "5"),
    28: ("5", "1"), 29: ("All", "All terrain"), 30: ("5", "1"),
    31: ("6", "1"), 32: ("6", "9"), 33: ("6", "1"), 34: ("6", "25"),
    45: (None, "12"), 47: ("2-3", None), 49: (None, "20"),
    50: (None, "2 (line)"), 51: (None, "3 (line)"), 52: (None, "4 (line)"),
    55: (None, "8 (around user)"), 58: (None, "8 (around user)"),
    62: ("6", None), 63: (None, "4 (T-shape)"), 64: (None, "12"),
}
SPEED_RE = re.compile(r"Casting time: (Instantaneous|Fast|Medium|Slow)")


def ability_ref(n, long_text):
    ref = {}
    if n in GUIDE_RANGE_AREA:
        rng, area = GUIDE_RANGE_AREA[n]
        if rng is not None:
            ref["range"] = rng
        if area is not None:
            ref["area"] = area
    m = SPEED_RE.search(long_text or "")
    if m:
        ref["speed"] = m.group(1)
    return ref


def main():
    iso_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ISO
    with open(iso_path, "rb") as f:
        rune_units = read_units(f, RUNE_STREAM, RUNE_COUNT * 2)
        ability_units = read_units(f, ABILITY_STREAM, ABILITY_COUNT * 3)
    abilities = {}
    for n in range(ABILITY_COUNT):
        entry = {"name": ability_units[3 * n], "desc": ability_units[3 * n + 1]}
        ref = ability_ref(n, ability_units[3 * n + 2])
        if ref:
            entry["ref"] = ref
        abilities[str(n)] = entry
    data = {
        "runes": {str(k): {"name": rune_units[2 * k], "desc": rune_units[2 * k + 1]}
                  for k in range(RUNE_COUNT)},
        "abilities": abilities,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    print("wrote %s (%d runes, %d abilities)" % (OUT, RUNE_COUNT, ABILITY_COUNT))


if __name__ == "__main__":
    main()
