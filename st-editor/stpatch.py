"""ISO read/patch engine for Suikoden Tactics (SLUS-21245).

The character table is stored uncompressed in the disc image; a character's
file offset equals its documented in-RAM address (verified across all 63
documented characters). Records are 0x280 bytes; see stfields.py.

Usage as a CLI:
    python3 stpatch.py --iso GAME.iso validate
    python3 stpatch.py --iso GAME.iso list
    python3 stpatch.py --iso GAME.iso show Kyril
    python3 stpatch.py --iso GAME.iso set Kyril str_growth 8
"""

import argparse
import json
import os
import shutil
import sys

import stfields

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SLUS_MARKER = b"SLUS_212.45"


def load_characters():
    with open(os.path.join(_DATA_DIR, "st_characters.json"), "r", encoding="utf-8") as f:
        chars = json.load(f)
    # Disambiguate names that appear twice in the doc (e.g. Kyril/Snowe adult
    # + child). The doc lists the adult first, so keep the first occurrence's
    # plain name and qualify later ones by their note (usually "Child").
    seen = {}
    for c in chars:
        n = c["name"]
        if n in seen:
            note = (c.get("doc_note") or "").strip().strip("()") or "alt"
            c["name"] = "%s (%s)" % (n, note)
        seen[n] = True
    return chars


def load_tables():
    p = os.path.join(_DATA_DIR, "st_tables.json")
    if not os.path.exists(p):
        return {}
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


class ISOEditor:
    def __init__(self, path):
        self.path = path
        self._backed_up = False
        self.chars = load_characters()
        self._by_name = {c["name"].lower(): c for c in self.chars}
        self.tables = load_tables()

    # --- validation -----------------------------------------------------
    def validate(self):
        """Return (ok, message). Checks USA serial marker + Kyril fingerprint."""
        with open(self.path, "rb") as f:
            head = f.read(2 * 1024 * 1024)
        if SLUS_MARKER not in head:
            return False, ("Could not find %s. This may not be the USA "
                           "(SLUS-21245) version of Suikoden Tactics." % SLUS_MARKER.decode())
        kyril = self._by_name.get("kyril")
        if kyril:
            wt = self.read_bytes(kyril["offset"] + 0x0B, 1)[0]
            if wt != 0x0E:
                return False, ("SLUS marker found, but the character table "
                               "fingerprint failed (Kyril weapon-type byte = 0x%02X, "
                               "expected 0x0E). Offsets may not match this dump." % wt)
        return True, "USA Suikoden Tactics (SLUS-21245) confirmed; character table verified."

    # --- low-level IO ---------------------------------------------------
    def read_bytes(self, offset, n):
        with open(self.path, "rb") as f:
            f.seek(offset)
            return f.read(n)

    def _ensure_backup(self):
        if self._backed_up:
            return
        bak = self.path + ".bak"
        if not os.path.exists(bak):
            shutil.copy2(self.path, bak)
        self._backed_up = True

    def write_bytes(self, offset, data, backup=True):
        if backup:
            self._ensure_backup()
        with open(self.path, "r+b") as f:
            f.seek(offset)
            f.write(data)

    # --- character helpers ---------------------------------------------
    def find_char(self, name):
        c = self._by_name.get(name.lower())
        if not c:
            raise KeyError("no character named %r (try `list`)" % name)
        return c

    def read_record(self, char):
        return self.read_bytes(char["offset"], stfields.RECORD_LEN)

    def decode_char(self, char):
        rec = self.read_record(char)
        d = stfields.decode(rec)
        d["_name"] = char["name"]
        d["_offset"] = char["offset"]
        return d

    def set_field(self, char, field_key, value, backup=True):
        fdef = stfields.field_by_key(field_key)
        if not fdef:
            raise KeyError("unknown field %r" % field_key)
        w = stfields.field_width(fdef)
        value = int(value)
        maxv = (1 << (8 * w)) - 1
        if not (0 <= value <= maxv):
            raise ValueError("value out of range 0-%d for u%d: %d" % (maxv, 8 * w, value))
        self.write_bytes(char["offset"] + fdef["offset"], value.to_bytes(w, "little"), backup=backup)
        return fdef

    def set_weapon_growth(self, char, level, value, backup=True):
        if not (1 <= level <= stfields.WEAPON_GROWTH_LEVELS):
            raise ValueError("weapon level out of range 1-%d" % stfields.WEAPON_GROWTH_LEVELS)
        off = char["offset"] + stfields.WEAPON_GROWTH_OFFSET + (level - 1)
        self.write_bytes(off, bytes([int(value) & 0xFF]), backup=backup)

    # --- generic tables (items/shop/runeshop/itemprice/skills/enemies) ----
    def table(self, name):
        t = self.tables.get(name)
        if not t:
            raise KeyError("unknown table %r (have: %s)" % (name, ", ".join(self.tables)))
        return t

    def table_record_offset(self, name, rec_id):
        t = self.table(name)
        if not (0 <= rec_id < t["count"]):
            raise ValueError("id %d out of range 0-%d for %s" % (rec_id, t["count"] - 1, name))
        return t["base"] + rec_id * t["stride"]

    def read_table_record(self, name, rec_id):
        t = self.table(name)
        return self.read_bytes(self.table_record_offset(name, rec_id), t["stride"])

    def decode_table_record(self, name, rec_id):
        t = self.table(name)
        rec = self.read_table_record(name, rec_id)
        fields = []
        for f in t["fields"]:
            off, w = f["off"], f["w"]
            val = int.from_bytes(rec[off:off + w], "little")  # PS2 EE is little-endian
            fields.append({"off": off, "w": w, "label": f["label"], "value": val})
        return {"table": name, "id": rec_id,
                "offset": self.table_record_offset(name, rec_id),
                "stride": t["stride"], "fields": fields, "raw": rec.hex()}

    def set_table_field(self, name, rec_id, off, w, value, backup=True):
        t = self.table(name)
        if off < 0 or off + w > t["stride"]:
            raise ValueError("field @0x%X w%d outside record (stride 0x%X)" % (off, w, t["stride"]))
        maxv = (1 << (8 * w)) - 1
        value = int(value)
        if not (0 <= value <= maxv):
            raise ValueError("value %d out of range 0-%d for u%d" % (value, maxv, 8 * w))
        abs_off = self.table_record_offset(name, rec_id) + off
        self.write_bytes(abs_off, int(value).to_bytes(w, "little"), backup=backup)


# --- CLI ---------------------------------------------------------------
def _cmd_validate(ed, args):
    ok, msg = ed.validate()
    print(("OK: " if ok else "FAIL: ") + msg)
    return 0 if ok else 1


def _cmd_list(ed, args):
    for c in ed.chars:
        extra = c.get("doc_note") or ""
        print("%-16s @0x%08X  %s" % (c["name"], c["offset"], extra))
    print("\n%d characters." % len(ed.chars))
    return 0


def _cmd_show(ed, args):
    c = ed.find_char(args.name)
    d = ed.decode_char(c)
    print("%s  @0x%08X" % (d["_name"], d["_offset"]))
    for fdef in stfields.FIELDS:
        e = d[fdef["key"]]
        print("  %-14s = %-18s (byte 0x%02X = 0x%02X / %d)"
              % (fdef["label"], e["display"], e["offset"], e["value"], e["value"]))
    print("  Weapon growth  = %s" % " ".join("%02X" % b for b in d["weapon_growth"]))
    return 0


def _cmd_set(ed, args):
    c = ed.find_char(args.name)
    fdef = ed.set_field(c, args.field, args.value)
    print("Set %s.%s = %d (byte 0x%02X). Backup at %s.bak"
          % (c["name"], args.field, int(args.value), fdef["offset"], ed.path))
    return 0


def _cmd_table_list(ed, args):
    for name, t in ed.tables.items():
        print("%-11s base=0x%08X stride=0x%X count=%d fields=%d"
              % (name, t["base"], t["stride"], t["count"], len(t["fields"])))
    return 0


def _cmd_table_show(ed, args):
    d = ed.decode_table_record(args.table, int(args.id))
    print("%s id=%d  @0x%08X  (stride 0x%X)" % (d["table"], d["id"], d["offset"], d["stride"]))
    for f in d["fields"]:
        print("  %-22s @+0x%02X u%d = %d (0x%X)"
              % (f["label"], f["off"], f["w"] * 8, f["value"], f["value"]))
    print("  raw: %s" % d["raw"])
    return 0


def _cmd_table_set(ed, args):
    off = int(args.off, 0); w = int(args.width); val = int(args.value, 0)
    ed.set_table_field(args.table, int(args.id), off, w, val)
    print("Set %s id=%d @+0x%X (u%d) = %d. Backup at %s.bak"
          % (args.table, int(args.id), off, w * 8, val, ed.path))
    return 0


def _cmd_find_bytes(ed, args):
    pat = bytes.fromhex(args.hex.replace(" ", ""))
    limit = int(args.limit)
    hits = []
    with open(ed.path, "rb") as f:
        data = f.read()
    i = data.find(pat)
    while i != -1 and len(hits) < limit:
        hits.append(i); i = data.find(pat, i + 1)
    print("%d hit(s) for %s (showing %d):" % (len(hits), pat.hex(), min(limit, len(hits))))
    for h in hits:
        print("  0x%08X" % h)
    return 0


def _cmd_dump_region(ed, args):
    off = int(args.offset, 0); length = int(args.length, 0)
    data = ed.read_bytes(off, length)
    for row in range(0, len(data), 16):
        chunk = data[row:row + 16]
        hexs = " ".join("%02X" % b for b in chunk)
        asci = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print("0x%08X  %-47s  %s" % (off + row, hexs, asci))
    return 0


def _cmd_ids(ed, args):
    import glob
    if args.table:
        m = stfields.load_data(args.table)
        for k in sorted(m, key=lambda x: int(x, 16)):
            print("  0x%s  %s" % (k, m[k]))
        print("%d entries in %s" % (len(m), args.table))
    else:
        print("Available id lists (use: ids <name>):")
        for p in sorted(glob.glob(os.path.join(_DATA_DIR, "st_*.json"))):
            name = os.path.basename(p)[3:-5]
            if name in ("characters", "tables"):
                continue
            try:
                n = len(json.load(open(p)))
            except Exception:  # noqa
                n = "?"
            print("  %-18s %s entries" % (name, n))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Suikoden Tactics ISO editor (CLI)")
    p.add_argument("--iso", required=True, help="path to the .iso")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate")
    sub.add_parser("list")
    sp = sub.add_parser("show"); sp.add_argument("name")
    sp = sub.add_parser("set"); sp.add_argument("name"); sp.add_argument("field"); sp.add_argument("value")
    sub.add_parser("table-list")
    sp = sub.add_parser("table-show"); sp.add_argument("table"); sp.add_argument("id")
    sp = sub.add_parser("table-set")
    sp.add_argument("table"); sp.add_argument("id"); sp.add_argument("off")
    sp.add_argument("width"); sp.add_argument("value")
    sp = sub.add_parser("find-bytes"); sp.add_argument("hex"); sp.add_argument("--limit", default="20")
    sp = sub.add_parser("dump-region"); sp.add_argument("offset"); sp.add_argument("length")
    sp = sub.add_parser("ids"); sp.add_argument("table", nargs="?")
    args = p.parse_args(argv)
    if not os.path.exists(args.iso):
        print("ISO not found: %s" % args.iso, file=sys.stderr)
        return 2
    ed = ISOEditor(args.iso)
    return {"validate": _cmd_validate, "list": _cmd_list, "show": _cmd_show, "set": _cmd_set,
            "table-list": _cmd_table_list, "table-show": _cmd_table_show,
            "table-set": _cmd_table_set, "find-bytes": _cmd_find_bytes,
            "dump-region": _cmd_dump_region, "ids": _cmd_ids}[args.cmd](ed, args)


if __name__ == "__main__":
    sys.exit(main())
