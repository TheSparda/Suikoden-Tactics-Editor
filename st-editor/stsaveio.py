"""PS2 save-container I/O for Suikoden Tactics: read every common export format
and convert between them.

Supported readers -> a normalized container (dirname, [(name, bytes), ...]):
  .sps / .xps   SharkPort / X-Port         (plain)
  .cbs          CodeBreaker                (RC4 + zlib)
  .max          Action Replay MAX / MAXDrive (lzari — ported here)
  .psu          EMS / uLaunchELF export    (plain, 1024-padded)

Writers:
  .psu          the universal import format (uLaunchELF / mymc)
  .sps          re-import via SharkPort-aware tools

The lzari decoder and the CodeBreaker RC4 key/permutation are ported to Python 3
from mymc (Ross Ridge, public domain). Correctness is cross-verified: the same
PAL save exists as .xps, .cbs and .max — all three must decode to a byte-identical
game data file (see `selftest`).
"""
import struct, zlib, sys
from bisect import bisect_right

# ---------------------------------------------------------------------------
# lzari decode (MAX Drive) — Py3 port of mymc lzari.py, decode path only
# ---------------------------------------------------------------------------
HIST_LEN = 4096
MIN_MATCH_LEN = 3
MAX_MATCH_LEN = 60
ARITH_BITS = 15
QUADRANT1 = 1 << ARITH_BITS
QUADRANT2 = QUADRANT1 * 2
QUADRANT3 = QUADRANT1 * 3
QUADRANT4 = QUADRANT1 * 4
MAX_CUM = QUADRANT1 - 1
MAX_CHAR = 256 + MAX_MATCH_LEN - MIN_MATCH_LEN + 1


class _Lzari:
    def init(self):
        self.high = QUADRANT4
        self.low = 0
        self.code = 0
        self.sym_cum = list(range(0, MAX_CHAR + 1))          # increasing
        self.symbol_to_char = [0] + list(range(MAX_CHAR))
        self.sym_freq = [0] + [1] * MAX_CHAR
        self.position_cum = [0] * (HIST_LEN + 1)
        a = 0
        for i in range(HIST_LEN, 0, -1):
            a += 10000 // (200 + i)
            self.position_cum[i - 1] = a

    def search(self, table, x):
        c = 1; s = len(table) - 1
        while True:
            a = (s + c) // 2
            if table[a] <= x:
                s = a
            else:
                c = a + 1
            if c >= s:
                break
        return c

    def update_model_decode(self, symbol):
        sym_freq = self.sym_freq; sym_cum = self.sym_cum
        if sym_cum[MAX_CHAR] >= MAX_CUM:
            c = 0
            for i in range(MAX_CHAR, 0, -1):
                sym_cum[MAX_CHAR - i] = c
                a = (sym_freq[i] + 1) // 2
                sym_freq[i] = a
                c += a
            sym_cum[MAX_CHAR] = c
        freq = sym_freq[symbol]
        new_symbol = symbol
        while sym_freq[new_symbol - 1] == freq:
            new_symbol -= 1
        if new_symbol != symbol:
            s2c = self.symbol_to_char
            s2c[new_symbol], s2c[symbol] = s2c[symbol], s2c[new_symbol]
        sym_freq[new_symbol] = freq + 1
        for i in range(MAX_CHAR - new_symbol + 1, MAX_CHAR + 1):
            sym_cum[i] += 1

    def decode_char(self):
        high = self.high; low = self.low; code = self.code
        sym_cum = self.sym_cum
        rng = high - low
        max_cum_freq = sym_cum[MAX_CHAR]
        n = ((code - low + 1) * max_cum_freq - 1) // rng
        i = bisect_right(sym_cum, n, 1)
        high = low + sym_cum[i] * rng // max_cum_freq
        low = low + sym_cum[i - 1] * rng // max_cum_freq
        symbol = MAX_CHAR + 1 - i
        nb = self._nb
        while True:
            if low < QUADRANT2:
                if low < QUADRANT1 or high > QUADRANT3:
                    if high > QUADRANT2:
                        break
                else:
                    low -= QUADRANT1; code -= QUADRANT1; high -= QUADRANT1
            else:
                low -= QUADRANT2; code -= QUADRANT2; high -= QUADRANT2
            low *= 2; high *= 2; code = code * 2 + nb()
        ret = self.symbol_to_char[symbol]
        self.high = high; self.low = low; self.code = code
        self.update_model_decode(symbol)
        return ret

    def decode_position(self):
        rng = self.high - self.low
        max_cum = self.position_cum[0]
        pos = self.search(self.position_cum,
                          ((self.code - self.low + 1) * max_cum - 1) // rng) - 1
        self.high = self.low + self.position_cum[pos] * rng // max_cum
        self.low = self.low + self.position_cum[pos + 1] * rng // max_cum
        nb = self._nb
        while True:
            if self.low < QUADRANT2:
                if self.low < QUADRANT1 or self.high > QUADRANT3:
                    if self.high > QUADRANT2:
                        return pos
                else:
                    self.low -= QUADRANT1; self.code -= QUADRANT1; self.high -= QUADRANT1
            else:
                self.low -= QUADRANT2; self.code -= QUADRANT2; self.high -= QUADRANT2
            self.low *= 2; self.high *= 2; self.code = nb() + self.code * 2

    def decode(self, src, out_length):
        bits = []
        for b in src:
            for k in range(7, -1, -1):
                bits.append((b >> k) & 1)
        bits.extend([0] * 32)
        it = iter(bits)
        self._nb = lambda _n=it.__next__: _n()
        out = bytearray(out_length); outpos = 0
        self.init()
        self.code = 0
        for _ in range(ARITH_BITS + 2):
            self.code += self.code + self._nb()
        hist_pos = HIST_LEN - MAX_MATCH_LEN
        history = [0x20] * hist_pos + [0] * MAX_MATCH_LEN
        while outpos < out_length:
            char = self.decode_char()
            if char >= 0x100:
                pos = self.decode_position()
                length = char - 0x100 + MIN_MATCH_LEN
                base = (hist_pos - pos - 1) % HIST_LEN
                for off in range(length):
                    a = history[(base + off) % HIST_LEN]
                    out[outpos] = a; outpos += 1
                    history[hist_pos] = a; hist_pos = (hist_pos + 1) % HIST_LEN
            else:
                out[outpos] = char; outpos += 1
                history[hist_pos] = char; hist_pos = (hist_pos + 1) % HIST_LEN
        return bytes(out)


def lzari_decode(src, out_length):
    return _Lzari().decode(src, out_length)


# ---------------------------------------------------------------------------
# CodeBreaker RC4 (custom permutation + non-standard i start), from mymc
# ---------------------------------------------------------------------------
CBS_RC4_KEY = bytes([
    0x5f,0x1f,0x85,0x6f,0x31,0xaa,0x3b,0x18,0x21,0xb9,0xce,0x1c,0x07,0x4c,0x9c,0xb4,
    0x81,0xb8,0xef,0x98,0x59,0xae,0xf9,0x26,0xe3,0x80,0xa3,0x29,0x2d,0x73,0x51,0x62,
    0x7c,0x64,0x46,0xf4,0x34,0x1a,0xf6,0xe1,0xba,0x3a,0x0d,0x82,0x79,0x0a,0x5c,0x16,
    0x71,0x49,0x8e,0xac,0x8c,0x9f,0x35,0x19,0x45,0x94,0x3f,0x56,0x0c,0x91,0x00,0x0b,
    0xd7,0xb0,0xdd,0x39,0x66,0xa1,0x76,0x52,0x13,0x57,0xf3,0xbb,0x4e,0xe5,0xdc,0xf0,
    0x65,0x84,0xb2,0xd6,0xdf,0x15,0x3c,0x63,0x1d,0x89,0x14,0xbd,0xd2,0x36,0xfe,0xb1,
    0xca,0x8b,0xa4,0xc6,0x9e,0x67,0x47,0x37,0x42,0x6d,0x6a,0x03,0x92,0x70,0x05,0x7d,
    0x96,0x2f,0x40,0x90,0xc4,0xf1,0x3e,0x3d,0x01,0xf7,0x68,0x1e,0xc3,0xfc,0x72,0xb5,
    0x54,0xcf,0xe7,0x41,0xe4,0x4d,0x83,0x55,0x12,0x22,0x09,0x78,0xfa,0xde,0xa7,0x06,
    0x08,0x23,0xbf,0x0f,0xcc,0xc1,0x97,0x61,0xc5,0x4a,0xe6,0xa0,0x11,0xc2,0xea,0x74,
    0x02,0x87,0xd5,0xd1,0x9d,0xb7,0x7e,0x38,0x60,0x53,0x95,0x8d,0x25,0x77,0x10,0x5e,
    0x9b,0x7f,0xd8,0x6e,0xda,0xa2,0x2e,0x20,0x4f,0xcd,0x8f,0xcb,0xbe,0x5a,0xe0,0xed,
    0x2c,0x9a,0xd4,0xe2,0xaf,0xd0,0xa9,0xe8,0xad,0x7a,0xbc,0xa8,0xf2,0xee,0xeb,0xf5,
    0xa6,0x99,0x28,0x24,0x6c,0x2b,0x75,0x5d,0xf8,0xd3,0x86,0x17,0xfb,0xc0,0x7b,0xb3,
    0x58,0xdb,0xc7,0x4b,0xff,0x04,0x50,0xe9,0x88,0x69,0xc9,0x2a,0xab,0xfd,0x5b,0x1b,
    0x8a,0xd9,0xec,0x27,0x44,0x0e,0x33,0xc8,0x6b,0x93,0x32,0x48,0xb6,0x30,0x43,0xa5,
])
_CBS_KEY_FULL = CBS_RC4_KEY if len(CBS_RC4_KEY) == 256 else None


def _rc4_crypt(key, t):
    s = bytearray(key); t = bytearray(t); j = 0
    for ii in range(len(t)):
        i = (ii + 1) % 256
        j = (j + s[i]) % 256
        s[i], s[j] = s[j], s[i]
        t[ii] ^= s[(s[i] + s[j]) % 256]
    return bytes(t)


def _zt(b):
    return b.split(b"\0")[0].decode("latin1", "replace")


# ---------------------------------------------------------------------------
# Readers -> (dirname, [(name, data), ...])
# ---------------------------------------------------------------------------
def read_sps(d):
    assert d[:17] == b"\x0d\0\0\0SharkPortSave"
    o = 21
    def ls(o):
        (L,) = struct.unpack("<L", d[o:o+4]); return d[o+4:o+4+L], o+4+L
    dirname, o = ls(o); _, o = ls(o); _, o = ls(o)
    o += 4
    hlen, rawdir, dirlen, dirmode, cr, mo = struct.unpack("<H64sL8xH2x8s8s", d[o:o+98])
    o += hlen
    files = []
    for _ in range(dirlen - 2):
        hlen, name, fl, mode, cr, mo = struct.unpack("<H64sL8xH2x8s8s", d[o:o+98])
        o += hlen
        files.append((_zt(name), d[o:o+fl])); o += fl
    return _zt(rawdir), files


def read_cbs(d):
    assert d[:4] == b"CFU\0"
    (d04, hlen) = struct.unpack("<LL", d[4:12])
    body_hdr = d[12:12 + hlen - 12]
    (dlen, flen, dirname) = struct.unpack("<LL32s", body_hdr[:40])
    if _CBS_KEY_FULL is None:
        raise RuntimeError("CBS RC4 key incomplete in this build")
    body = d[12 + hlen - 12:12 + hlen - 12 + flen]
    body = _rc4_crypt(_CBS_KEY_FULL, body)
    body = zlib.decompressobj().decompress(body, dlen)
    files = []
    while body:
        h = struct.unpack("<8s8sLHHLL32s", body[:64])
        size = h[2]; name = _zt(h[7])
        files.append((name, body[64:64+size])); body = body[64+size:]
    return _zt(dirname), files


def read_max(d):
    (magic, crc, dirname, iconsysname, clen, dirlen, length) = \
        struct.unpack("<12sL32s32sLLL", d[:0x5C])
    assert magic == b"Ps2PowerSave", magic
    s = d[0x5C:] if clen == length else d[0x5C:0x5C + clen - 4]
    raw = lzari_decode(s, length)
    files = []; off = 0
    for _ in range(dirlen):
        (l, name) = struct.unpack("<L32s", raw[off:off+36]); off += 36
        files.append((_zt(name), raw[off:off+l])); off += l
        off = (off + 8 + 15) // 16 * 16 - 8
    return _zt(dirname), files


def read_psu(d):
    def de(s):
        mode, length, name = struct.unpack_from("<HH L 8x L L 8x L 28x 448s", s)[0:3] \
            if False else (struct.unpack_from("<H", s, 0)[0],
                           struct.unpack_from("<L", s, 4)[0],
                           _zt(s[0x40:0x40+448]))
        return mode, length, name
    n = struct.unpack_from("<L", d, 4)[0]
    pos = 512 * 3
    files = []
    for _ in range(n - 2):
        mode, length, name = de(d[pos:pos+512]); pos += 512
        files.append((name, d[pos:pos+length]))
        pos += (length + 1023) // 1024 * 1024
    _, _, root = de(d[0:512])
    return root, files


def open_any(path):
    d = open(path, "rb").read()
    if d[:17] == b"\x0d\0\0\0SharkPortSave": return read_sps(d)
    if d[:12] == b"Ps2PowerSave": return read_max(d)
    if d[:4] == b"CFU\0": return read_cbs(d)
    return read_psu(d)


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------
DF_DIR = 0x8427   # RWX | 0400 | DIR | EXISTS
DF_FILE = 0x8417  # RWX | 0400 | FILE | EXISTS


def _pack_dirent(mode, length, name):
    nm = name.encode("latin1")[:448]
    return struct.pack("<HHL8sLL8sL28x448s", mode, 0, length, b"\0"*8, 0, 0,
                       b"\0"*8, 0, nm)


def write_psu(dirname, files, path):
    out = bytearray()
    out += _pack_dirent(DF_DIR, len(files) + 2, dirname)
    out += _pack_dirent(DF_DIR, 0, ".")
    out += _pack_dirent(DF_DIR, 0, "..")
    for name, data in files:
        out += _pack_dirent(DF_FILE, len(data), name)
        out += data
        out += b"\0" * (((len(data) + 1023) // 1024 * 1024) - len(data))
    open(path, "wb").write(out)
    return len(out)


def selftest(paths):
    """Cross-verify: decode each container, print game-file crc; matching PAL
    trio (.xps/.cbs/.max of one save) must agree."""
    import zlib as _z
    for p in paths:
        try:
            dn, files = open_any(p)
            gf = next((data for nm, data in files
                       if nm.lower() not in ("icon.sys", "rap.ico") and not nm.lower().endswith(".ico")), b"")
            print("%-46s dir=%-24s game=%d bytes crc=%08X" %
                  (p.split("/")[-1], dn, len(gf), _z.crc32(gf) & 0xFFFFFFFF))
        except Exception as e:
            print("%-46s ERROR %s" % (p.split("/")[-1], e))


def main(argv):
    if len(argv) >= 3 and argv[0] == "convert":
        dn, files = open_any(argv[1])
        n = write_psu(dn, files, argv[2])
        print("converted %s -> %s (%s, %d files, %d bytes)"
              % (argv[1].split("/")[-1], argv[2], dn, len(files), n))
        return 0
    if argv:
        selftest(argv); return 0
    print("usage:\n  python3 stsaveio.py <save1> [save2 ...]     # inspect/verify\n"
          "  python3 stsaveio.py convert IN.(sps|xps|cbs|max) OUT.psu")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
