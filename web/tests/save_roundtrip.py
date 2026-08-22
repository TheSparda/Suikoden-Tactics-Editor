#!/usr/bin/env python3
"""Engine round-trip test — ships NO game data.

Builds a synthetic, valid Suikoden Tactics `gamedata` blob using the *real*
engine's own constants (so it can't drift), then drives the same read/write/
checksum functions the web glue calls in the browser and asserts every field
round-trips and the checksum invariant holds. Mirrors the S3 playbook's
`save_roundtrip.py`.
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ST_EDITOR = os.path.normpath(os.path.join(HERE, "..", "..", "st-editor"))
sys.path.insert(0, ST_EDITOR)

import stsavefields as F

PASS, FAIL = [], []
def ok(cond, label):
    (PASS if cond else FAIL).append(label)
    print(("  PASS " if cond else "  FAIL ") + label)

# --- synthetic fixture size, derived from the engine's own offset constants ---
size = max(
    F.INV_OFF + 4 * F.INV_SLOTS,
    F.PARTY_OFF + F.PARTY_SLOTS * F.PARTY_STRIDE,
    F.GOLD_OFF + 4, F.SP_OFF + 4, F.HERO_NAME_OFF + F.HERO_NAME_SLOT,
    F.HDR,
) + 64
data = bytes(F.fix_checksums(bytes(size)))   # zero blob with valid checksums

print("=== synthetic fixture ===")
ok(len(data) == size, "built %d-byte synthetic save" % size)
crc, md5 = F.verify(data)
ok(crc and md5, "synthetic save passes verify() (crc=%s md5=%s)" % (crc, md5))

print("\n=== globals ===")
data = F.fix_checksums(F.write_globals(data, gold=123456, skill_points=77))
g = F.read_globals(data)
ok(g["gold"] == 123456, "gold round-trip = %d" % g["gold"])
ok(g["skill_points"] == 77, "skill_points round-trip = %d" % g["skill_points"])
ok(all(F.verify(data)), "checksums valid after globals write")

print("\n=== gold clamp ===")
data2 = F.fix_checksums(F.write_globals(data, gold=F.GOLD_MAX + 5000))
ok(F.read_globals(data2)["gold"] <= F.GOLD_MAX, "gold clamped to GOLD_MAX (%d)" % F.read_globals(data2)["gold"])

print("\n=== character ===")
ch = F.read_char(data, 0)
ch["exp"] = 99999
ch["hp_cur"] = 250; ch["hp_max"] = 300
ch["recruited"] = True
first_stat = F.STATS[0]; ch["stats"][first_stat] = 123
first_plus = F.PLUS[0]; ch["plus"][first_plus] = 45
ch["equip"][F.EQUIP[0]] = 7
ch["runes"][F.RUNE_SLOTS[0]] = 3
ch["magic_overall"][0] = 5; ch["magic_current"][0] = 4
data = F.fix_checksums(F.write_char(data, 0, ch))
r = F.read_char(data, 0)
ok(r["exp"] == 99999, "char exp round-trip")
ok(r["hp_cur"] == 250 and r["hp_max"] == 300, "char hp round-trip")
ok(r["recruited"] is True, "char recruited round-trip")
ok(r["stats"][first_stat] == 123, "char stat round-trip")
ok(r["plus"][first_plus] == 45, "char plus round-trip")
ok(r["equip"][F.EQUIP[0]] == 7, "char equip round-trip")
ok(r["runes"][F.RUNE_SLOTS[0]] == 3, "char rune round-trip")
ok(r["magic_overall"][0] == 5 and r["magic_current"][0] == 4, "char magic round-trip")
ok(all(F.verify(data)), "checksums valid after char write")

print("\n=== inventory ===")
data = F.fix_checksums(F.write_inventory_slot(data, 3, 42, 9))
inv = F.read_inventory(data)
ok(inv[3] == (42, 9), "inventory slot round-trip = %s" % (inv[3],))
ok(all(F.verify(data)), "checksums valid after inventory write")

print("\n=== hero name + S4 import ===")
data = F.fix_checksums(F.write_hero_name(data, "Lazlo"))
ok(F.read_hero_name(data) == "Lazlo", "hero name round-trip = %r" % F.read_hero_name(data))
data = F.fix_checksums(F.set_s4_import(data, True))
ok(F.s4_import_enabled(data) is True, "S4 import flag round-trip")
ok(all(F.verify(data)), "checksums valid after hero/s4 write")

print("\n=== slot header ===")
data = F.fix_checksums(F.set_slot(data, 4))
ok(all(F.verify(data)), "checksums valid after set_slot")

print("\n=== rejects tampering ===")
bad = bytearray(data); bad[F.HDR + 1] ^= 0xFF   # flip a payload byte, don't re-checksum
crc, md5 = F.verify(bytes(bad))
ok(not (crc and md5), "tampered payload fails verify() (crc=%s md5=%s)" % (crc, md5))

print("\n================ SUMMARY ================")
print("PASS: %d   FAIL: %d" % (len(PASS), len(FAIL)))
for f in FAIL:
    print("  - " + f)
sys.exit(1 if FAIL else 0)
