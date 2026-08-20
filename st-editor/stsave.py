"""PS2 memory-card (PS2MFS) reader for Suikoden Tactics saves.

Ported to Python 3 from mymc (Ross Ridge, public domain): superblock, indirect
FAT / FAT cluster chains, root directory walk, and per-page Hamming ECC. This is
the game-agnostic card container. Game-specific save fields (party, gold, the
Suikoden IV import flag) still require format RE and are not decoded yet.

CLI:
    python3 stsave.py CARD.ps2 list           # list save folders
    python3 stsave.py CARD.ps2 find           # highlight Suikoden Tactics saves
    python3 stsave.py CARD.ps2 files "<dir>"   # list files in a save folder
    python3 stsave.py CARD.ps2 putgame FOLDER EDITED.bin OUT.ps2
        # inject an edited (same-length) game file into an EXISTING folder,
        # write a NEW card image, and verify (re-read identical + ECC).
        # To add a save to a card that lacks it, export .psu (stsaveio) and
        # import via mymc/uLaunchELF — reusing their proven allocator.
"""

import struct
import sys

PS2MC_MAGIC = b"Sony PS2 Memory Card Format "
FAT_ALLOCATED = 0x80000000
FAT_MASK = 0x7FFFFFFF
DF_DIR = 0x0020
DF_FILE = 0x0010
DF_EXISTS = 0x8000
DIRENT_LEN = 512

# Suikoden Tactics USA save-folder id prefix (PS2 browser dir names use the serial)
ST_SERIAL = "SLUS-21245"
ST_DIR_HINT = "21245"


# --- Hamming ECC (public domain, from mymc) --------------------------------
def _parityb(a):
    a ^= a >> 1; a ^= a >> 2; a ^= a >> 4
    return a & 1


_PARITY = [_parityb(b) for b in range(256)]
_CPMASK = [0x55, 0x33, 0x0F, 0x00, 0xAA, 0xCC, 0xF0]
_COLPAR = []
for _b in range(256):
    _m = 0
    for _i in range(len(_CPMASK)):
        _m |= _PARITY[_b & _CPMASK[_i]] << _i
    _COLPAR.append(_m)


def ecc_calculate(chunk):
    """Hamming code (3 bytes) for a <=128-byte chunk."""
    cp = 0x77; lp0 = 0x7F; lp1 = 0x7F
    for i, b in enumerate(chunk):
        cp ^= _COLPAR[b]
        if _PARITY[b]:
            lp0 ^= ~i & 0x7F
            lp1 ^= i
    return bytes([cp, lp0 & 0x7F, lp1 & 0x7F])


def ecc_page_spare(page512):
    """Build the 16-byte spare (12 bytes ECC + pad) for a 512-byte page."""
    out = bytearray()
    for i in range(0, len(page512), 128):
        out += ecc_calculate(page512[i:i + 128])
    out += b"\x00" * (16 - len(out))
    return bytes(out)


class PS2MC:
    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            self.data = f.read()
        sb = struct.unpack_from("<28s12sHHHHIIIIII8x128s128sbb", self.data, 0)
        if not sb[0].startswith(PS2MC_MAGIC[:20]):
            raise ValueError("not a PS2 memory-card image (bad magic)")
        self.page_len = sb[2]
        self.pages_per_cluster = sb[3]
        self.clusters_per_card = sb[6]
        self.alloc_offset = sb[7]
        self.rootdir_cluster = sb[9]
        self.indirect_fat = list(struct.unpack("<32I", sb[12]))
        self.cluster_size = self.page_len * self.pages_per_cluster
        self.spare_size = (self.page_len // 128) * 4 if (self.page_len % 128 == 0) else 0
        # detect ECC images by total size
        pages = len(self.data) // (self.page_len + self.spare_size) if self.spare_size else 0
        self.raw_page = self.page_len + self.spare_size
        if not (self.spare_size and pages >= self.clusters_per_card * self.pages_per_cluster):
            self.spare_size = 0; self.raw_page = self.page_len
        self.entries_per_cluster = self.cluster_size // DIRENT_LEN
        self.ent_per_fat = self.cluster_size // 4

    def read_page(self, n):
        off = n * self.raw_page
        return self.data[off:off + self.page_len]

    def read_cluster(self, cluster):
        """Read an absolute cluster (page_len*pages_per_cluster bytes)."""
        base = cluster * self.pages_per_cluster
        return b"".join(self.read_page(base + i) for i in range(self.pages_per_cluster))

    def read_alloc_cluster(self, a):
        return self.read_cluster(self.alloc_offset + a)

    def fat(self, alloc_cluster):
        """Return the raw FAT entry for an allocatable cluster."""
        double, single = divmod(alloc_cluster, self.ent_per_fat)
        ind_i, ind_off = divmod(double, self.ent_per_fat)
        fat_cluster_num = struct.unpack_from("<I", self.read_cluster(self.indirect_fat[ind_i]), ind_off * 4)[0]
        return struct.unpack_from("<I", self.read_cluster(fat_cluster_num), single * 4)[0]

    def fat_chain(self, first):
        chain = []
        c = first
        while c != 0xFFFFFFFF and (c & FAT_MASK) != FAT_MASK and len(chain) < 100000:
            chain.append(c)
            nxt = self.fat(c)
            if not (nxt & FAT_ALLOCATED):
                break
            c = nxt & FAT_MASK
        return chain

    def read_dir(self, first_cluster):
        """Return existing dirents in a directory. Walks the full cluster chain
        and keeps entries with the EXISTS flag — the "." entry's length is only
        meaningful for the root, so we don't rely on it for subfolders."""
        ents = []
        for cl in self.fat_chain(first_cluster):
            buf = self.read_alloc_cluster(cl)
            for j in range(self.entries_per_cluster):
                e = self._dirent(buf[j * DIRENT_LEN:(j + 1) * DIRENT_LEN])
                if e["exists"] and e["cluster"] != 0xFFFFFFFF and e["length"] != 0xFFFFFFFF and e["name"]:
                    ents.append(e)
        return ents

    @staticmethod
    def _dirent(s):
        mode, _, length, _c, fat_cluster, parent, _m, _attr, name = \
            struct.unpack("<HHL8sLL8sL28x448s", s)
        nm = name.split(b"\x00", 1)[0].decode("ascii", "replace")
        return {"mode": mode, "length": length, "cluster": fat_cluster,
                "name": nm, "is_dir": bool(mode & DF_DIR), "exists": bool(mode & DF_EXISTS)}

    def root_entries(self):
        ents = self.read_dir(self.rootdir_cluster)
        return [e for e in ents if e["name"] not in (".", "..") and e["exists"]]

    def read_file(self, dir_cluster, filename):
        for e in self.read_dir(dir_cluster):
            if e["name"] == filename and not e["is_dir"]:
                data = b"".join(self.read_alloc_cluster(c) for c in self.fat_chain(e["cluster"]))
                return data[:e["length"]]
        raise FileNotFoundError(filename)

    # --- writing (ECC-safe) --------------------------------------------
    def page_spare(self, n):
        off = n * self.raw_page + self.page_len
        return self.data[off:off + self.spare_size]

    def total_pages(self):
        return len(self.data) // self.raw_page

    def verify_ecc(self, limit=None):
        """Compare our recomputed ECC to the card's, over real (non-erased)
        pages only. Erased pages carry all-0xFF spare and are skipped. A
        matched==checked result proves our ECC matches the console's."""
        if not self.spare_size:
            return (0, 0)
        n = self.total_pages()
        if limit:
            n = min(n, limit)
        checked = matched = 0
        for i in range(n):
            stored = self.page_spare(i)[:12]
            if stored == b"\xff" * 12:  # erased page — no computed ECC
                continue
            checked += 1
            if ecc_page_spare(self.read_page(i))[:12] == stored:
                matched += 1
        return (checked, matched)

    def write(self, out_path):
        """Write the card back out. self.data already holds correct ECC for any
        pages edited via replace_file_data and original spare everywhere else,
        so an unmodified card is byte-identical."""
        with open(out_path, "wb") as f:
            f.write(self.data)

    def _set_page_data(self, n, page_data):
        off = n * self.raw_page
        b = bytearray(self.data)
        b[off:off + self.page_len] = page_data.ljust(self.page_len, b"\x00")[:self.page_len]
        if self.spare_size:
            b[off + self.page_len:off + self.page_len + 12] = ecc_page_spare(bytes(b[off:off + self.page_len]))[:12]
        self.data = bytes(b)

    def replace_file_data(self, dir_cluster, filename, new_bytes):
        """Overwrite an existing file's bytes in place (no resize). Updates the
        affected pages and their ECC. Caller must write() afterwards."""
        for e in self.read_dir(dir_cluster):
            if e["name"] == filename and not e["is_dir"]:
                if len(new_bytes) > e["length"]:
                    raise ValueError("new data (%d) larger than file (%d); resize unsupported"
                                     % (len(new_bytes), e["length"]))
                chain = self.fat_chain(e["cluster"])
                pos = 0
                for cl in chain:
                    for p in range(self.pages_per_cluster):
                        if pos >= len(new_bytes):
                            return
                        page_no = (self.alloc_offset + cl) * self.pages_per_cluster + p
                        cur = bytearray(self.read_page(page_no))
                        take = min(self.page_len, len(new_bytes) - pos)
                        cur[:take] = new_bytes[pos:pos + take]
                        self._set_page_data(page_no, bytes(cur))
                        pos += take
                return
        raise FileNotFoundError(filename)


class PSU:
    """Reader for .psu single-save exports (uLaunchELF/mymc):
    512-byte directory entries; file data padded to 1024-byte boundaries."""

    def __init__(self, path):
        with open(path, "rb") as f:
            self.data = f.read()

    def entries(self):
        d0 = PS2MC._dirent(self.data[0:DIRENT_LEN])
        n = d0["length"]
        pos = DIRENT_LEN
        files = []
        for _ in range(n):
            ent = PS2MC._dirent(self.data[pos:pos + DIRENT_LEN]); pos += DIRENT_LEN
            if ent["name"] in (".", ".."):
                continue
            size = ent["length"]
            data = self.data[pos:pos + size]
            pos += (size + 1023) // 1024 * 1024
            files.append((ent, data))
        return d0, files


def _fmt(e):
    kind = "DIR " if e["is_dir"] else "file"
    return "  [%s] %-32s len=%d cluster=%d" % (kind, e["name"], e["length"], e["cluster"])


def main(argv=None):
    argv = argv or sys.argv[1:]
    if len(argv) < 2:
        print(__doc__); return 2
    path, cmd = argv[0], argv[1]
    if path.lower().endswith(".psu"):
        psu = PSU(path)
        d0, files = psu.entries()
        print("%s: folder '%s', %d file(s)" % (path, d0["name"], len(files)))
        for ent, data in files:
            print("  [file] %-24s %d bytes" % (ent["name"], len(data)))
        return 0
    mc = PS2MC(path)
    if cmd == "ecc":
        checked, matched = mc.verify_ecc()
        print("ECC: %d/%d pages match (our writer %s)"
              % (matched, checked, "valid" if matched == checked else "MISMATCH"))
        return 0 if matched == checked else 1
    if cmd == "roundtrip":
        out = argv[2] if len(argv) >= 3 else path + ".rt"
        mc.write(out)
        same = open(path, "rb").read() == open(out, "rb").read()
        print("round-trip write -> %s : %s" % (out, "identical" if same else "DIFFERS"))
        return 0 if same else 1
    if cmd == "list":
        ents = mc.root_entries()
        print("%s: %d save folders (page=%d spare=%d cluster=%d)"
              % (path, len(ents), mc.page_len, mc.spare_size, mc.cluster_size))
        for e in ents:
            print(_fmt(e))
        return 0
    if cmd == "find":
        hits = [e for e in mc.root_entries() if ST_DIR_HINT in e["name"]]
        if hits:
            print("Suikoden Tactics saves found:")
            for e in hits:
                print(_fmt(e))
        else:
            print("No Suikoden Tactics (%s) save folders on this card." % ST_SERIAL)
        return 0
    if cmd == "files" and len(argv) >= 3:
        for e in mc.root_entries():
            if e["name"] == argv[2]:
                for f in mc.read_dir(e["cluster"]):
                    if f["name"] not in (".", ".."):
                        print(_fmt(f))
                return 0
        print("folder not found: %s" % argv[2]); return 1
    if cmd == "putgame" and len(argv) >= 5:
        folder, local, out = argv[2], argv[3], argv[4]
        ent = next((e for e in mc.root_entries() if e["name"] == folder), None)
        if not ent:
            print("folder not found: %s" % folder); return 1
        dc = ent["cluster"]
        files = [f for f in mc.read_dir(dc) if not f["is_dir"]]
        game = next((f["name"] for f in files if ST_DIR_HINT in f["name"]), None) \
            or (files[0]["name"] if files else None)
        if game is None:
            print("no game file in %s" % folder); return 1
        new = open(local, "rb").read()
        cur = mc.read_file(dc, game)
        if len(new) != len(cur):
            print("length mismatch: edited=%d card=%d (must match)" % (len(new), len(cur)))
            return 1
        mc.replace_file_data(dc, game, new)
        mc.write(out)
        # verify: re-read + ECC
        v = PS2MC(out)
        dc2 = next(e["cluster"] for e in v.root_entries() if e["name"] == folder)
        ok = v.read_file(dc2, game) == new
        chk, match = v.verify_ecc()
        print("wrote %s : %s into %s/%s ; re-read %s ; ECC %d/%d"
              % (out, "%d bytes" % len(new), folder, game,
                 "OK" if ok else "MISMATCH", match, chk))
        return 0 if ok else 1
    print(__doc__); return 2


if __name__ == "__main__":
    sys.exit(main())
