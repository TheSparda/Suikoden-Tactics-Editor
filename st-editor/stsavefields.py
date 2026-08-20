"""Suikoden Tactics save-file (game data file) field map — VERIFIED 2026-08-16.

Cracked by matching the ELF's game-state getter table (accessor functions at
ELF vaddr 0x320A00+, each `lui/jr/addiu` returning a global in 0x0066-0x0069)
against three real saves. The save file is:

    [0x20-byte header][payload = RAM block starting at 0x66E9F0]

    save_offset(RAM_addr) = RAM_addr - 0x66E9F0 + 0x20

Header (all verified on 6 saves):
    +0x00 u32 = 0
    +0x04 u32 = save "type"? (1 or 2; observed 2 on PAL/cleared saves)
    +0x08 u32 = low16: slot number; high16: flag (1 on NG+/cleared)
    +0x0C u32 = CRC-32 (zlib/IEEE) of payload (bytes 0x20..end)
    +0x10..0x1F  = MD5 of payload, byte-reversed
Both digests must be recomputed after any payload edit.

Party array: 89 slots x 0x188 bytes at RAM 0x670CA8 (save 0x22D8).
Record layout (verified: stats grow across saves; story-fixed runes correct;
Lazlo idx 55 left rune = 0x0B Rune of Punishment in every save):
    +0x00 u32  EXP            (level ~= EXP/1000 + 1; matches save titles)
    +0x08 u16  HP current
    +0x0A u16  HP max
    +0x0C..0x1A u16 x8       STR, SKL, MAG, EVA, PDF, MDF, SPD, LUC
    +0x1C..0x2C u16 x9       plus-stats: HP,STR,SKL,MAG,EVA,PDF,MDF,SPD,LUC
    +0x2E..0x40 u16 x10      equipment: Body, Hands, Other 1-8 (item ids)
    +0x42/0x43/0x44 u8       rune Head / Right hand / Left hand (rune ids)
    +0x45..0x48 u8 x4        magic level overall, slots 1-4
    +0x49..0x4C u8 x4        magic level current, slots 1-4
    +0x50 u8   recruit flag  bit0 = recruited (0xE1 recruited / 0xE0 present
                              -but-locked). Lazlo (idx 55) 0xE0 -> setting bit0
                              is the Suikoden IV import unlock.
Globals:
    gold          u32 at RAM 0x6794F8 (save 0xAB28)   [0..9,999,999]
    skill points  u32 at RAM 0x6794F0 (save 0xAB20)
    inventory     at RAM 0x6795B8 (save 0xABE8): slots of (u16 item id,
                  u16 quantity), 500 slots (pnach: 250+250)
Party index -> character names come from data/st_ram_party_map.json (pnach
order; 75 named of 89 slots).
"""
import hashlib, json, os, struct, zlib

RAM_BASE = 0x66E9F0
HDR = 0x20
def soff(ram): return ram - RAM_BASE + HDR

PARTY_OFF = soff(0x670CA8)      # 0x22D8
PARTY_STRIDE = 0x188
PARTY_SLOTS = 89
GOLD_OFF = soff(0x6794F8)
SP_OFF = soff(0x6794F0)
INV_OFF = soff(0x6795B8)
INV_SLOTS = 500
GOLD_MAX = 9_999_999

STATS = ["STR", "SKL", "MAG", "EVA", "PDF", "MDF", "SPD", "LUC"]
PLUS = ["HP+", "STR+", "SKL+", "MAG+", "EVA+", "PDF+", "MDF+", "SPD+", "LUC+"]
EQUIP = ["Body", "Hands"] + ["Other %d" % i for i in range(1, 9)]
RUNE_SLOTS = ["Head", "Right hand", "Left hand"]

_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def _load(name):
    with open(os.path.join(_DATA, name)) as f:
        return json.load(f)


def roster():
    """{party_index: character name} from the pnach RAM map (75 named)."""
    m = _load("st_ram_party_map.json")["roster_stat_base"]
    return {((int(v, 16) - 0x50) - 0x670CA8) // PARTY_STRIDE: n
            for n, v in m.items()}


def verify(data):
    """Check both header digests against the payload."""
    pay = data[HDR:]
    crc_ok = struct.unpack_from("<I", data, 0x0C)[0] == (zlib.crc32(pay) & 0xFFFFFFFF)
    md5_ok = data[0x10:0x20] == hashlib.md5(pay).digest()[::-1]
    return crc_ok, md5_ok


def fix_checksums(data):
    """Recompute header CRC-32 + reversed MD5 over the payload."""
    b = bytearray(data)
    pay = bytes(b[HDR:])
    struct.pack_into("<I", b, 0x0C, zlib.crc32(pay) & 0xFFFFFFFF)
    b[0x10:0x20] = hashlib.md5(pay).digest()[::-1]
    return bytes(b)


def set_slot(data, slot):
    """Set the header slot number (+0x08 low u16; matches the -NN in the save
    folder name, verified 04/05/00 across real saves). Use when injecting a
    save into a folder with a different slot suffix."""
    b = bytearray(data)
    struct.pack_into("<H", b, 0x08, int(slot) & 0xFFFF)
    return bytes(b)


def _u16(d, o): return d[o] | (d[o + 1] << 8)
def _u32(d, o): return struct.unpack_from("<I", d, o)[0]


def read_char(data, idx):
    b = PARTY_OFF + idx * PARTY_STRIDE
    return {
        "index": idx, "base": b,
        "exp": _u32(data, b),
        "hp_cur": _u16(data, b + 0x08), "hp_max": _u16(data, b + 0x0A),
        "stats": {STATS[k]: _u16(data, b + 0x0C + 2 * k) for k in range(8)},
        "plus": {PLUS[k]: _u16(data, b + 0x1C + 2 * k) for k in range(9)},
        "equip": {EQUIP[k]: _u16(data, b + 0x2E + 2 * k) for k in range(10)},
        "runes": {RUNE_SLOTS[k]: data[b + 0x42 + k] for k in range(3)},
        "magic_overall": [data[b + 0x45 + k] for k in range(4)],
        "magic_current": [data[b + 0x49 + k] for k in range(4)],
        "recruit_flag": data[b + 0x50],
        "recruited": bool(data[b + 0x50] & 1),
    }


def write_char(data, idx, ch):
    """Write a read_char()-shaped dict back. Returns new bytes (checksums NOT
    recomputed here — call fix_checksums once after all edits)."""
    b = PARTY_OFF + idx * PARTY_STRIDE
    out = bytearray(data)
    struct.pack_into("<I", out, b, ch["exp"] & 0xFFFFFFFF)
    struct.pack_into("<H", out, b + 0x08, ch["hp_cur"] & 0xFFFF)
    struct.pack_into("<H", out, b + 0x0A, ch["hp_max"] & 0xFFFF)
    for k in range(8):
        struct.pack_into("<H", out, b + 0x0C + 2 * k, ch["stats"][STATS[k]] & 0xFFFF)
    for k in range(9):
        struct.pack_into("<H", out, b + 0x1C + 2 * k, ch["plus"][PLUS[k]] & 0xFFFF)
    for k in range(10):
        struct.pack_into("<H", out, b + 0x2E + 2 * k, ch["equip"][EQUIP[k]] & 0xFFFF)
    for k in range(3):
        out[b + 0x42 + k] = ch["runes"][RUNE_SLOTS[k]] & 0xFF
    for k in range(4):
        out[b + 0x45 + k] = ch["magic_overall"][k] & 0xFF
        out[b + 0x49 + k] = ch["magic_current"][k] & 0xFF
    flag = out[b + 0x50]
    out[b + 0x50] = (flag & ~1) | (1 if ch["recruited"] else 0)
    return bytes(out)


def read_globals(data):
    return {"gold": _u32(data, GOLD_OFF), "skill_points": _u32(data, SP_OFF)}


def write_globals(data, gold=None, skill_points=None):
    out = bytearray(data)
    if gold is not None:
        struct.pack_into("<I", out, GOLD_OFF, min(max(int(gold), 0), GOLD_MAX))
    if skill_points is not None:
        struct.pack_into("<I", out, SP_OFF, min(max(int(skill_points), 0), GOLD_MAX))
    return bytes(out)


def read_inventory(data, limit=INV_SLOTS):
    inv = []
    for k in range(limit):
        o = INV_OFF + 4 * k
        if o + 4 > len(data):
            break
        inv.append((_u16(data, o), _u16(data, o + 2)))
    return inv


def write_inventory_slot(data, slot, item_id, qty):
    out = bytearray(data)
    o = INV_OFF + 4 * slot
    struct.pack_into("<H", out, o, item_id & 0xFFFF)
    struct.pack_into("<H", out, o + 2, qty & 0xFFFF)
    return bytes(out)


def _selftest(path):
    import stsaveedit
    dn, name, data = stsaveedit.open_game_save(path)
    crc_ok, md5_ok = verify(data)
    print("%s: crc=%s md5=%s" % (dn, crc_ok, md5_ok))
    ident = fix_checksums(data) == data
    print("fix_checksums identity on unedited save:", ident)
    g = read_globals(data)
    names = roster()
    rec = [i for i in range(PARTY_SLOTS) if read_char(data, i)["recruited"]]
    print("gold=%d sp=%d recruited=%d/%d" % (g["gold"], g["skill_points"], len(rec), PARTY_SLOTS))
    laz = read_char(data, 55)
    print("Lazlo: recruited=%s runes=%s" % (laz["recruited"], laz["runes"]))
    # edit round-trip: change gold, fix checksums, verify, restore
    d2 = fix_checksums(write_globals(data, gold=123456))
    assert verify(d2) == (True, True)
    assert read_globals(d2)["gold"] == 123456
    print("edit->checksum->verify round-trip OK")
    return crc_ok and md5_ok and ident


if __name__ == "__main__":
    import sys
    ok = all(_selftest(p) for p in sys.argv[1:]) if len(sys.argv) > 1 else False
    sys.exit(0 if ok else 1)
