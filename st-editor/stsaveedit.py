"""Suikoden Tactics save-file layer: SharkPort/X-Port container + party locator.

Container: SharkPort (.sps) and X-Port (.xps) share one format (magic
"\\x0d\\0\\0\\0SharkPortSave"). They wrap the raw PS2 save folder (icon.sys,
rap.ico, and the game data file BASLUS-21245-NN / BESLES-53769-NN). We can open
one, extract the game data file, edit it, and repack byte-safe (verified by an
identity round-trip when nothing is edited).

Party layout (VERIFIED structurally against real USA saves + the pnach RAM map):
- The game data file contains a party array at stride 0x188 (392 B), ~90 records
  (matches the roster). Confirmed by autocorrelation (dominant period 0x188) and
  by the pnach's RAM stride (also 0x188).
- Rune-slot bytes (values in the rune-id space 0x00..0x36) cluster at record
  offsets ~0x88..0xC1.
- Diffing two saves of one playthrough (Finale vs New Game+) shows a per-character
  mutable field at record +0x179 (changes in 88/90 records) plus a smaller
  0x128..0x154 cluster (the active-party subset).

NOT YET NAMED to the >=95% bar: which record column is STR vs SKL vs level, etc.
Those need one ground-truth anchor (a character's real in-game stats in a known
save) or in-emulator differential testing. Until then this module exposes the
container + located party records for byte-level editing and the fields that ARE
constrained (rune slots, recruited flag candidates); it never writes a field it
can't justify.
"""
import struct, sys, os
try:
    import stsaveio
except ImportError:
    stsaveio = None

SPS_MAGIC = b"\x0d\0\0\0SharkPortSave"
REC_STRIDE = 0x188


def pick_game_file(files):
    """From a normalized [(name, bytes), ...] folder, return (game_file_name,
    game_bytes) — the one entry that is not an icon/metadata file."""
    for name, data in files:
        n = name.lower()
        if n not in ("icon.sys", "rap.ico") and not n.endswith(".ico") \
                and n != "icon.sys":
            return name, data
    raise ValueError("no game data file found in save folder")


def open_game_save(path):
    """Open ANY supported container (.sps/.xps/.cbs/.max/.psu) or a raw .ps2
    memory-card image and return (dirname, game_file_name, game_bytes)."""
    if stsaveio is None:
        raise RuntimeError("stsaveio module not available")
    dn, files = stsaveio.open_any(path)
    gname, data = pick_game_file(files)
    return dn, gname, data


def _rstr(d, o):
    (L,) = struct.unpack("<L", d[o:o + 4]); o += 4
    return d[o:o + L], o + L


class SharkPort:
    """Open/repack a SharkPort/X-Port (.sps/.xps) save export."""

    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            self.raw = f.read()
        self._parse()

    def _parse(self):
        d = self.raw
        if d[:17] != SPS_MAGIC:
            raise ValueError("not a SharkPort/X-Port save (bad magic)")
        o = 17
        (self.savetype,) = struct.unpack("<L", d[o:o + 4]); o += 4
        self.title, o = _rstr(d, o)
        self.datestamp, o = _rstr(d, o)
        self.comment, o = _rstr(d, o)
        (self.flen,) = struct.unpack("<L", d[o:o + 4]); o += 4
        self.body_start = o
        # directory header
        hlen, rawdir, dirlen, dirmode, created, modified = struct.unpack(
            "<H64sL8xH2x8s8s", d[o:o + 98])
        self.dir_hlen = hlen
        self.dirname = rawdir.split(b"\0")[0]
        o += hlen
        self.files = []          # list of dicts: name, hdr(bytes), data offset/len
        for _ in range(dirlen - 2):
            fh = d[o:o + 98]
            hlen, name, fl, mode, cr, mo = struct.unpack("<H64sL8xH2x8s8s", fh)
            hdr = d[o:o + hlen]; o += hlen
            self.files.append(dict(name=name.split(b"\0")[0].decode("latin1"),
                                    hdr=hdr, off=o, len=fl))
            o += fl
        self.tail = d[o:]        # trailing 4-byte checksum (+ any padding)

    def game_file(self):
        """(name, bytes) of the game data file (the non-icon entry)."""
        for fe in self.files:
            n = fe["name"].lower()
            if n not in ("icon.sys", "rap.ico") and not n.endswith(".ico"):
                return fe["name"], self.raw[fe["off"]:fe["off"] + fe["len"]]
        raise ValueError("no game data file in container")

    def repack(self, new_game_bytes):
        """Rebuild the container with the game data file replaced (same length,
        so every header stays valid). The SharkPort trailing checksum is
        preserved verbatim: import tools (mymc) ignore it, so a no-op repack is
        byte-identical. (The game's OWN internal checksum, if any, lives inside
        the data file and is a separate concern — see module notes.)"""
        name, old = self.game_file()
        if len(new_game_bytes) != len(old):
            raise ValueError("edited save must keep the same length (%d)" % len(old))
        out = bytearray(self.raw)
        for fe in self.files:
            if fe["name"] == name:
                out[fe["off"]:fe["off"] + fe["len"]] = new_game_bytes
                break
        return bytes(out)


def locate_party(save, stride=REC_STRIDE):
    """Return (start, count) of the party array via 0x188 autocorrelation.
    Picks the densest contiguous periodic region."""
    N = len(save)
    match = [1 if i + stride < N and save[i] == save[i + stride] else 0
             for i in range(N)]
    w = stride * 2
    if N <= w:
        return 0, N // stride
    acc = sum(match[:w]); best = (acc, 0)
    for i in range(w, N):
        acc += match[i] - match[i - w]
        if acc > best[0]:
            best = (acc, i - w)
    start = best[1]
    # walk back to the array's true start in stride steps while still periodic
    while start - stride >= 0 and match[start - stride]:
        start -= stride
    count = (N - start) // stride
    return start, count


def _selftest(path):
    sp = SharkPort(path)
    name, gf = sp.game_file()
    rp = sp.repack(gf)
    ident = (rp == sp.raw)
    start, count = locate_party(gf)
    print("container: title=%r date=%r file=%s (%d bytes)"
          % (sp.title.decode("latin1", "replace"),
             sp.datestamp.decode("latin1", "replace"), name, len(gf)))
    print("repack identity round-trip:", "OK" if ident else "DIFFERS")
    print("party array: start=0x%X count=%d (stride 0x%X)" % (start, count, REC_STRIDE))
    # show rune-id-constrained columns as a sanity check
    cols = []
    for r in range(REC_STRIDE - 2):
        c = sum(1 for k in range(start, len(gf) - REC_STRIDE, REC_STRIDE)
                if 1 <= gf[k + r] <= 0x36 and gf[k + r + 1] <= 0x36 and gf[k + r + 2] <= 0x36
                and (gf[k + r] or gf[k + r + 1] or gf[k + r + 2]))
        cols.append((c, r))
    cols.sort(reverse=True)
    print("candidate rune-slot record offsets:", ["0x%X" % r for _, r in cols[:4]])
    return ident


def main(argv=None):
    argv = argv or sys.argv[1:]
    if len(argv) < 2:
        print("usage: python3 stsaveedit.py <save.sps|.xps> {info|extract OUT|repack IN OUT}")
        return 2
    path, cmd = argv[0], argv[1]
    # party works on any container; info/extract/repack are SharkPort-only.
    if cmd == "party":
        dn, name, gf = open_game_save(path)
        start, count = locate_party(gf)
        print("%s: %s (%d bytes) party array start=0x%X count=%d stride=0x%X"
              % (dn, name, len(gf), start, count, REC_STRIDE))
        return 0
    sp = SharkPort(path)
    if cmd == "info":
        return 0 if _selftest(path) else 1
    if cmd == "extract" and len(argv) >= 3:
        name, gf = sp.game_file(); open(argv[2], "wb").write(gf)
        print("wrote %s (%d bytes)" % (argv[2], len(gf))); return 0
    if cmd == "repack" and len(argv) >= 4:
        gb = open(argv[2], "rb").read()
        open(argv[3], "wb").write(sp.repack(gb))
        print("repacked -> %s" % argv[3]); return 0
    print("usage: python3 stsaveedit.py <save> {info|party|extract OUT|repack IN OUT}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
