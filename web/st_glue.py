"""Pyodide glue for the Suikoden Tactics web save editor.

Runs entirely in the browser (CPython-in-WASM). It reuses the *desktop* editor's
save modules unchanged — stsaveio / stsaveedit / stsavefields / stsave — feeding
them a path in Pyodide's in-memory filesystem. Nothing is uploaded: the uploaded
save is written to /save.bin, decoded, edited, and the result is read back out of
the in-memory FS and handed to the browser as a download.

Mirrors the desktop server's _save_open / _save_state / _save_write handlers
(steditor.py) so the field logic and checksum handling stay identical.
"""
import json, os

import stsaveio
import stsaveedit
import stsavefields
import stsave

_STATE = {}
SAVE_PATH = "/save.bin"
OUT_PATH = "/out.bin"


def _save_state(gb):
    names = stsavefields.roster()
    chars = []
    for i in range(stsavefields.PARTY_SLOTS):
        ch = stsavefields.read_char(gb, i)
        ch["name"] = names.get(i, "slot %d" % i)
        chars.append(ch)
    inv = [{"slot": k, "id": a, "qty": q}
           for k, (a, q) in enumerate(stsavefields.read_inventory(gb))]
    return {"globals": stsavefields.read_globals(gb), "chars": chars,
            "inventory": inv,
            "hero_name": stsavefields.read_hero_name(gb),
            "hero_name_max": stsavefields.HERO_NAME_MAX,
            "s4_import": stsavefields.s4_import_enabled(gb),
            "stat_keys": stsavefields.STATS, "plus_keys": stsavefields.PLUS,
            "equip_keys": stsavefields.EQUIP, "rune_keys": stsavefields.RUNE_SLOTS}


def open_save(orig_name, folder=None):
    """Open the container/card already written to SAVE_PATH. Returns JSON."""
    folder = (folder or "").strip() or None
    is_card = stsaveio.is_ps2_card(SAVE_PATH)
    try:
        if is_card:
            dn, files = stsaveio.open_ps2_card(SAVE_PATH, folder)
            card_saves = stsaveio.list_ps2_card_saves(SAVE_PATH)
        else:
            dn, files = stsaveio.open_any(SAVE_PATH)
            card_saves = []
        gname, gbytes = stsaveedit.pick_game_file(files)
    except Exception as e:
        return json.dumps({"error": "Could not open save: %s" % e})
    ext = os.path.splitext(orig_name)[1].lower().lstrip(".")
    crc_ok, md5_ok = stsavefields.verify(gbytes)
    _STATE["save"] = {"orig_name": orig_name, "ext": ext, "dirname": dn,
                      "gamename": gname, "bytes": bytearray(gbytes),
                      "files": files, "is_card": is_card,
                      "folder": dn if is_card else ""}
    return json.dumps({
        "message": "Opened %s (%s) — %d bytes, checksums %s"
                   % (dn, gname, len(gbytes),
                      "OK" if crc_ok and md5_ok else
                      "BAD (crc=%s md5=%s)" % (crc_ok, md5_ok)),
        "dirname": dn, "gamename": gname, "ext": ext, "size": len(gbytes),
        "crc_ok": crc_ok, "md5_ok": md5_ok,
        "can_sps": ext in ("sps", "xps"),
        "is_card": is_card, "card_saves": card_saves,
        "folder": dn if is_card else "",
        "state": _save_state(bytes(gbytes)),
    })


def _download_name(st, kind):
    base = os.path.splitext(st["orig_name"])[0] or (st["dirname"] or "save")
    if kind == "psu":
        return "%s.psu" % (st["dirname"] or base)
    if kind == "ps2":
        return "%s.edited.ps2" % base
    return "%s.edited.%s" % (base, kind)


def write_save(payload_json):
    """Apply edits, write the chosen container to OUT_PATH, return JSON with the
    suggested download filename (JS reads OUT_PATH back out of the FS)."""
    st = _STATE.get("save")
    if not st:
        return json.dumps({"error": "open a save first"})
    d = json.loads(payload_json)
    gb = bytes(st["bytes"])
    # structured edits (verified field map) — same order as the desktop server
    for ce in d.get("char_edits") or []:
        gb = stsavefields.write_char(gb, int(ce["index"]), ce["char"])
    gl = d.get("globals") or {}
    if gl:
        gb = stsavefields.write_globals(gb, gold=gl.get("gold"),
                                        skill_points=gl.get("skill_points"))
    if d.get("hero_name") is not None:
        gb = stsavefields.write_hero_name(gb, d.get("hero_name"))
    if d.get("s4_import") is not None:
        gb = stsavefields.set_s4_import(gb, bool(d.get("s4_import")))
    for ie in d.get("inv_edits") or []:
        gb = stsavefields.write_inventory_slot(gb, int(ie["slot"]),
                                               int(ie["id"]), int(ie["qty"]))
    # raw byte edits (fallback)
    gb = bytearray(gb)
    for e in d.get("edits") or []:
        off = int(e["off"]); val = int(e["val"]) & 0xFF
        if 0 <= off < len(gb):
            gb[off] = val
    gb = bytearray(stsavefields.fix_checksums(bytes(gb)))

    target = d.get("target") or "psu"
    try:
        if target == "psu":
            files = [(n, bytes(gb) if n == st["gamename"] else data)
                     for n, data in st["files"]]
            n = stsaveio.write_psu(st["dirname"], files, OUT_PATH)
            msg = "Wrote .psu (%d bytes)" % n
            fn = _download_name(st, "psu")
        elif target == "sps":
            if st["ext"] not in ("sps", "xps"):
                return json.dumps({"error": "source is not .sps/.xps; use PSU export"})
            sp = stsaveedit.SharkPort(SAVE_PATH)
            with open(OUT_PATH, "wb") as f:
                f.write(sp.repack(bytes(gb)))
            msg = "Repacked .%s container" % st["ext"]
            fn = _download_name(st, st["ext"])
        elif target == "card":
            if not st["is_card"]:
                return json.dumps({"error": "source is not a raw .ps2 card"})
            folder = st["folder"]
            mc = stsave.PS2MC(SAVE_PATH)
            ent = next((x for x in mc.root_entries() if x["name"] == folder), None)
            if not ent:
                return json.dumps({"error": "folder not on card: %s" % folder})
            dc = ent["cluster"]
            gfiles = [f for f in mc.read_dir(dc) if not f["is_dir"]]
            gn = next((f["name"] for f in gfiles if stsave.ST_DIR_HINT in f["name"]),
                      gfiles[0]["name"] if gfiles else None)
            cur = mc.read_file(dc, gn)
            if len(gb) != len(cur):
                return json.dumps({"error": "length mismatch card=%d edited=%d"
                                   % (len(cur), len(gb))})
            # inject a copy whose header slot number matches the folder's -NN suffix
            cb = bytes(gb)
            tail = folder.rsplit("-", 1)[-1]
            if tail.isdigit():
                cb = stsavefields.fix_checksums(stsavefields.set_slot(cb, int(tail)))
            mc.replace_file_data(dc, gn, cb)
            mc.write(OUT_PATH)
            v = stsave.PS2MC(OUT_PATH)
            dc2 = next(x["cluster"] for x in v.root_entries() if x["name"] == folder)
            ok = v.read_file(dc2, gn) == cb
            chk, match = v.verify_ecc()
            msg = ("Injected into card [%s/%s] — re-read %s, ECC %d/%d"
                   % (folder, gn, "OK" if ok else "MISMATCH", match, chk))
            fn = _download_name(st, "ps2")
        else:
            return json.dumps({"error": "unknown target"})
    except Exception as e:
        return json.dumps({"error": "%s" % e})

    st["bytes"] = gb
    return json.dumps({"message": msg, "filename": fn, "outpath": OUT_PATH,
                       "state": _save_state(bytes(gb))})
