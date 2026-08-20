"""Character record schema for Suikoden Tactics (SLUS-21245).

Each character record is 0x280 bytes. Field map derived from HexEditing.rtf
and verified against the real ISO (see stpatch.verify). Byte numbers in the
doc are 1-indexed; offsets here are 0-indexed (doc byte 01 == offset 0x00).
"""

import json
import os

RECORD_LEN = 0x280

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def load_data(name):
    """Load a data/st_<name>.json map ({hex_id: label}); {} if missing."""
    path = os.path.join(_DATA_DIR, "st_%s.json" % name)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# Status-growth-rank byte -> label (HexEditing.rtf "Status Growth Rate").
# 0x01-0x08 are the plain F..SS ranks; 0x09-0x0C are special curves whose
# Lv50 result approximates the noted rank.
GROWTH_RANKS = {
    0x01: "F", 0x02: "E", 0x03: "D", 0x04: "C", 0x05: "B",
    0x06: "A", 0x07: "S", 0x08: "SS",
    0x09: "C (special)", 0x0A: "B~S (special)",
    0x0B: "S (special)", 0x0C: "SS+ (special)",
}

# Number of weapon-growth levels documented (offsets 0x0C..0x13, doc bytes 0D-14).
WEAPON_GROWTH_LEVELS = 8
WEAPON_GROWTH_OFFSET = 0x0C

# Field definitions. kind:
#   "u8"    raw byte (0-255)
#   "rank"  growth rank, labeled via GROWTH_RANKS
#   "enum"  labeled via the named data map (hex-id keyed)
FIELDS = [
    {"key": "move",       "label": "Move",         "offset": 0x00, "kind": "u8"},
    {"key": "unknown_01", "label": "Unknown",      "offset": 0x01, "kind": "u8"},
    {"key": "hp_growth",  "label": "HP Growth",    "offset": 0x02, "kind": "rank"},
    {"key": "str_growth", "label": "STR Growth",   "offset": 0x03, "kind": "rank"},
    {"key": "skl_growth", "label": "SKL Growth",   "offset": 0x04, "kind": "rank"},
    {"key": "mag_growth", "label": "MAG Growth",   "offset": 0x05, "kind": "rank"},
    {"key": "eva_growth", "label": "EVA Growth",   "offset": 0x06, "kind": "rank"},
    {"key": "pdf_growth", "label": "PDF Growth",   "offset": 0x07, "kind": "rank"},
    {"key": "mdf_growth", "label": "MDF Growth",   "offset": 0x08, "kind": "rank"},
    {"key": "spd_growth", "label": "SPD Growth",   "offset": 0x09, "kind": "rank"},
    {"key": "luck_growth","label": "LUCK Growth",  "offset": 0x0A, "kind": "rank"},
    {"key": "weapon_type","label": "Weapon Type",  "offset": 0x0B, "kind": "enum", "data": "weapon_types"},
    {"key": "rune_head",  "label": "Head Rune",    "offset": 0x58, "kind": "enum", "data": "runes"},
    {"key": "rune_right", "label": "Right Rune",   "offset": 0x5A, "kind": "enum", "data": "runes"},
    {"key": "rune_left",  "label": "Left Rune",    "offset": 0x5C, "kind": "enum", "data": "runes"},
]

# Extra 16-bit fields V11 writes at +0x2A..+0x4E (meaning unconfirmed — likely
# skill caps / secondary stats). Exposed as raw u16 so nothing is lost; labeled
# generically until verified. All fields default to width 1 unless "w" is set.
EXTRA_FIELDS = [
    {"key": "u16_%02X" % off, "label": "u16 @0x%02X" % off, "offset": off, "kind": "u16", "w": 2}
    for off in range(0x2A, 0x50, 4)
]
FIELDS = FIELDS + EXTRA_FIELDS


def field_width(fdef):
    return fdef.get("w", 1)


# Innate element (record +0x5E). Verified vs walkthrough guide + community post.
ELEMENTS = {0: "None", 1: "Fire", 2: "Cyclone (Wind)", 3: "Earth", 4: "Lightning", 5: "Water"}
ELEMENT_OFFSET = 0x5E

# 29 battle-skill affinity ranks at record +0xF1 (one byte each, 0=off, 1-7 rank).
# Order = skill ids 1..29 (Counter Attack .. Narcissism), per the community hex post
# and verified against known character skills (Kika/Akaghi/Mizuki).
SKILL_BLOCK_OFFSET = 0xF1
SKILL_COUNT = 29
SKILL_RANK_MAX = 7


def _skill_name(i):
    m = load_data("skills")
    for k in ("%03d" % i, "%02d" % i, "%d" % i, "%02X" % i):
        if k in m:
            return m[k]
    return "Skill %d" % i


FIELDS = FIELDS + [
    {"key": "element", "label": "Innate Element", "offset": ELEMENT_OFFSET, "kind": "element", "w": 1},
]
FIELDS = FIELDS + [
    {"key": "skill_%02d" % i, "label": "Skill: %s" % _skill_name(i),
     "offset": SKILL_BLOCK_OFFSET + (i - 1), "kind": "skillrank", "w": 1}
    for i in range(1, SKILL_COUNT + 1)
]


def enum_label(data_name, byte_val):
    """Human label for an enum byte, or a raw fallback like '0x0E (?)'."""
    m = load_data(data_name)
    key = "%02X" % byte_val
    if key in m:
        return m[key]
    # some maps (rune orbs) use 4-digit ids
    key4 = "%04X" % byte_val
    if key4 in m:
        return m[key4]
    return "0x%02X (?)" % byte_val


def decode(record):
    """Decode a 0x280-byte record into a dict of field -> {value, label}."""
    if len(record) < RECORD_LEN:
        raise ValueError("record too short: %d bytes" % len(record))
    out = {}
    for fdef in FIELDS:
        off = fdef["offset"]; w = field_width(fdef)
        b = int.from_bytes(record[off:off + w], "little")
        entry = {"value": b, "offset": off, "w": w, "label": fdef["label"]}
        if fdef["kind"] == "rank":
            entry["display"] = GROWTH_RANKS.get(b, "0x%02X (?)" % b)
        elif fdef["kind"] == "enum":
            entry["display"] = enum_label(fdef["data"], b)
        elif fdef["kind"] == "element":
            entry["display"] = ELEMENTS.get(b, "0x%02X (?)" % b)
        elif fdef["kind"] == "skillrank":
            entry["display"] = "off" if b == 0 else "rank %d" % b
        else:
            entry["display"] = str(b)
        out[fdef["key"]] = entry
    out["weapon_growth"] = list(
        record[WEAPON_GROWTH_OFFSET:WEAPON_GROWTH_OFFSET + WEAPON_GROWTH_LEVELS]
    )
    return out


def field_by_key(key):
    for fdef in FIELDS:
        if fdef["key"] == key:
            return fdef
    return None
