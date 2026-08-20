"""Local web UI for the Suikoden Tactics ISO editor.

Runs a stdlib HTTP server on 127.0.0.1:8748 (8747 is the S3 editor, so both can
run at once). Nothing is uploaded; the server only touches the ISO you point it
at. Features: character editor (all fields), data-table editors, reference
browser, Hard Mode, staged edits with Save/Revert, changed-from-baseline
highlighting + per-field restore, search on every list, light/dark themes.
"""

import json
import os
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import stfields
import stpatch
try:
    import stsaveio
    import stsaveedit
    import stsave
    import stsavefields
except ImportError:
    stsaveio = stsaveedit = stsave = stsavefields = None

HOST, PORT = "127.0.0.1", 8748
STATE = {"editor": None, "baseline": {}, "save": None}


def _enum_options(data_name):
    m = stfields.load_data(data_name)
    opts = [{"value": 0, "label": "0x00 (none)"}]
    for k, v in sorted(m.items(), key=lambda kv: int(kv[0], 16)):
        iv = int(k, 16)
        if iv == 0:
            continue
        opts.append({"value": iv, "label": "%s  (0x%02X)" % (v, iv)})
    return opts


def build_meta():
    rank_opts = [{"value": v, "label": "%s  (0x%02X)" % (lbl, v)}
                 for v, lbl in sorted(stfields.GROWTH_RANKS.items())]
    fields = []
    for f in stfields.FIELDS:
        w = stfields.field_width(f)
        entry = {"key": f["key"], "label": f["label"], "kind": f["kind"],
                 "offset": f["offset"], "w": w}
        if f["kind"] == "enum":
            entry["options"] = _enum_options(f["data"])
        elif f["kind"] == "rank":
            entry["options"] = rank_opts
        elif f["kind"] == "element":
            entry["options"] = [{"value": v, "label": "%s (%d)" % (lbl, v)}
                                for v, lbl in sorted(stfields.ELEMENTS.items())]
        elif f["kind"] == "skillrank":
            entry["options"] = [{"value": 0, "label": "off"}] + \
                [{"value": r, "label": "rank %d" % r} for r in range(1, stfields.SKILL_RANK_MAX + 1)]
        fields.append(entry)
    return {"fields": fields, "weapon_growth_levels": stfields.WEAPON_GROWTH_LEVELS,
            "rank_keys": [f["key"] for f in stfields.FIELDS if f["kind"] == "rank"]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def _q(self):
        from urllib.parse import urlparse, parse_qs
        return parse_qs(urlparse(self.path).query)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if self.path == "/api/meta":
            return self._send(200, build_meta())
        if self.path == "/api/lists":
            names = ["weapon_types", "runes", "rune_orbs", "shop_items",
                     "enemies", "skills", "locations", "character_names"]
            return self._send(200, {n: stfields.load_data(n) for n in names})
        if self.path == "/api/tables":
            return self._tables()
        if self.path.startswith("/api/trecord"):
            return self._trecord()
        if self.path.startswith("/api/char"):
            return self._char()
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/api/open":
            return self._open()
        if self.path == "/api/save":
            return self._save()
        if self.path == "/api/tsave":
            return self._tsave()
        if self.path == "/api/hardmode":
            return self._hardmode()
        if self.path == "/api/save_open":
            return self._save_open()
        if self.path == "/api/save_write":
            return self._save_write()
        if self.path == "/api/save_scan":
            return self._save_scan()
        if self.path == "/api/save_browse":
            return self._save_browse()
        return self._send(404, {"error": "not found"})

    # --- local save/card discovery --------------------------------------
    SAVE_EXTS = (".sps", ".xps", ".cbs", ".max", ".psu")

    def _save_scan(self):
        """Walk the project tree (and common PCSX2 memcard dirs) for ST save
        exports and .ps2 cards; identify each and list ST folders on cards."""
        roots = [os.path.abspath(os.path.join(os.getcwd(), ".."))]
        home = os.path.expanduser("~")
        for extra in (os.path.join(home, "Library", "Application Support", "PCSX2", "memcards"),
                      os.path.join(home, ".config", "PCSX2", "memcards"),
                      os.path.join(home, "Documents", "PCSX2", "memcards")):
            if os.path.isdir(extra):
                roots.append(extra)
        saves, cards = [], []
        seen = set()
        for root in roots:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [x for x in dirnames if not x.startswith(".") and x != "_decomp"]
                if len(saves) + len(cards) > 400:
                    break
                for fn in filenames:
                    p = os.path.join(dirpath, fn)
                    if p in seen:
                        continue
                    seen.add(p)
                    low = fn.lower()
                    if low.endswith(self.SAVE_EXTS):
                        try:
                            dn, name, gb = stsaveedit.open_game_save(p)
                            if "21245" not in dn and "53769" not in dn:
                                continue  # not a Suikoden Tactics save
                            crc_ok, md5_ok = stsavefields.verify(gb)
                            saves.append({"path": p, "file": fn, "dirname": dn,
                                          "size": len(gb), "ok": crc_ok and md5_ok,
                                          "region": "USA" if "SLUS" in dn else ("PAL" if "SLES" in dn else "?")})
                        except Exception:
                            pass
                    elif low.endswith(".ps2"):
                        try:
                            mc = stsave.PS2MC(p)
                            ents = [e["name"] for e in mc.root_entries()]
                            st = [n for n in ents if "21245" in n or "53769" in n]
                            cards.append({"path": p, "file": fn,
                                          "folders": len(ents), "st_folders": st})
                        except Exception:
                            pass
        saves.sort(key=lambda s: (s["region"] != "USA", s["file"]))
        cards.sort(key=lambda c: (not c["st_folders"], c["file"]))
        return self._send(200, {"saves": saves, "cards": cards,
                                "message": "found %d saves, %d cards" % (len(saves), len(cards))})

    def _save_browse(self):
        """Open a native file-picker (macOS osascript) and return the path."""
        import subprocess
        try:
            r = subprocess.run(
                ["osascript", "-e",
                 'POSIX path of (choose file with prompt "Choose a Suikoden Tactics save or memory card")'],
                capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                return self._send(200, {"cancelled": True})
            return self._send(200, {"path": r.stdout.strip()})
        except Exception as e:
            return self._send(400, {"error": "native file dialog unavailable: %s" % e})

    # --- save-file editor (any container format) -----------------------
    def _save_open(self):
        if stsaveio is None:
            return self._send(500, {"error": "save modules unavailable"})
        d = self._read_json()
        path = (d.get("path") or "").strip().strip('"')
        if not path or not os.path.exists(path):
            return self._send(400, {"error": "File not found: %s" % path})
        try:
            dn, files = stsaveio.open_any(path)
            gname, gbytes = stsaveedit.open_game_save(path)[1:]
        except Exception as e:
            return self._send(400, {"error": "Could not open save: %s" % e})
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        crc_ok, md5_ok = stsavefields.verify(gbytes)
        STATE["save"] = {"path": path, "ext": ext, "dirname": dn,
                         "gamename": gname, "bytes": bytearray(gbytes),
                         "files": files}
        return self._send(200, {
            "message": "Opened %s (%s) — %d bytes, checksums %s"
                       % (dn, gname, len(gbytes),
                          "OK" if crc_ok and md5_ok else "BAD (crc=%s md5=%s)" % (crc_ok, md5_ok)),
            "dirname": dn, "gamename": gname, "ext": ext, "size": len(gbytes),
            "can_sps": ext in ("sps", "xps"),
            "state": self._save_state(bytes(gbytes))})

    @staticmethod
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
                "stat_keys": stsavefields.STATS, "plus_keys": stsavefields.PLUS,
                "equip_keys": stsavefields.EQUIP, "rune_keys": stsavefields.RUNE_SLOTS}

    def _save_write(self):
        st = STATE.get("save")
        if not st:
            return self._send(400, {"error": "open a save first"})
        d = self._read_json()
        gb = bytes(st["bytes"])
        # structured edits (verified field map)
        for ce in d.get("char_edits") or []:
            gb = stsavefields.write_char(gb, int(ce["index"]), ce["char"])
        gl = d.get("globals") or {}
        if gl:
            gb = stsavefields.write_globals(gb, gold=gl.get("gold"),
                                            skill_points=gl.get("skill_points"))
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
        out = (d.get("out") or "").strip().strip('"')
        try:
            if target == "psu":
                if not out:
                    return self._send(400, {"error": "output path required"})
                files = [(n, bytes(gb) if n == st["gamename"] else data)
                         for n, data in st["files"]]
                n = stsaveio.write_psu(st["dirname"], files, out)
                st["bytes"] = gb
                return self._send(200, {"message": "Wrote .psu (%d bytes) -> %s" % (n, out),
                                        "state": self._save_state(bytes(gb))})
            if target == "sps":
                if st["ext"] not in ("sps", "xps"):
                    return self._send(400, {"error": "source is not .sps/.xps; use PSU export"})
                sp = stsaveedit.SharkPort(st["path"])
                open(out, "wb").write(sp.repack(bytes(gb)))
                st["bytes"] = gb
                return self._send(200, {"message": "Repacked .sps/.xps -> %s" % out,
                                        "state": self._save_state(bytes(gb))})
            if target == "card":
                card = (d.get("card") or "").strip().strip('"')
                folder = (d.get("folder") or "").strip()
                if not (card and folder and out):
                    return self._send(400, {"error": "card path, folder, and out required"})
                mc = stsave.PS2MC(card)
                ent = next((x for x in mc.root_entries() if x["name"] == folder), None)
                if not ent:
                    return self._send(400, {"error": "folder not on card: %s" % folder})
                dc = ent["cluster"]
                gfiles = [f for f in mc.read_dir(dc) if not f["is_dir"]]
                gn = next((f["name"] for f in gfiles if stsave.ST_DIR_HINT in f["name"]),
                          gfiles[0]["name"] if gfiles else None)
                cur = mc.read_file(dc, gn)
                if len(gb) != len(cur):
                    return self._send(400, {"error": "length mismatch card=%d edited=%d" % (len(cur), len(gb))})
                # inject a copy whose header slot number matches the target
                # folder's -NN suffix; session bytes keep their own slot
                cb = bytes(gb)
                tail = folder.rsplit("-", 1)[-1]
                if tail.isdigit():
                    cb = stsavefields.fix_checksums(stsavefields.set_slot(cb, int(tail)))
                mc.replace_file_data(dc, gn, cb); mc.write(out)
                v = stsave.PS2MC(out)
                dc2 = next(x["cluster"] for x in v.root_entries() if x["name"] == folder)
                ok = v.read_file(dc2, gn) == cb
                chk, match = v.verify_ecc()
                st["bytes"] = gb
                return self._send(200, {"message": "Injected into %s/%s -> %s (re-read %s, ECC %d/%d)"
                                        % (folder, gn, out, "OK" if ok else "MISMATCH", match, chk),
                                        "state": self._save_state(bytes(gb))})
        except Exception as e:
            return self._send(400, {"error": "%s" % e})
        return self._send(400, {"error": "unknown target"})

    # --- ISO / characters ----------------------------------------------
    def _open(self):
        data = self._read_json()
        path = (data.get("path") or "").strip().strip('"')
        if not path or not os.path.exists(path):
            return self._send(400, {"error": "File not found: %s" % path})
        try:
            ed = stpatch.ISOEditor(path)
            ok, msg = ed.validate()
        except Exception as e:  # noqa
            return self._send(400, {"error": str(e)})
        if not ok:
            return self._send(400, {"error": msg})
        STATE["editor"] = ed
        # snapshot growth-rank baseline for idempotent Hard Mode
        rank_keys = [f["key"] for f in stfields.FIELDS if f["kind"] == "rank"]
        base = {}
        for c in ed.chars:
            try:
                d = ed.decode_char(c)
                base[c["name"]] = {k: d[k]["value"] for k in rank_keys}
            except Exception:  # noqa
                pass
        STATE["baseline"] = base
        return self._send(200, {"ok": True, "message": msg, "path": path,
                                "characters": [c["name"] for c in ed.chars]})

    def _char(self):
        ed = STATE["editor"]
        if not ed:
            return self._send(400, {"error": "No ISO open."})
        name = (self._q().get("name") or [""])[0]
        try:
            c = ed.find_char(name); d = ed.decode_char(c)
        except KeyError as e:
            return self._send(404, {"error": str(e)})
        fields = {k: {"value": v["value"], "display": v["display"]}
                  for k, v in d.items() if isinstance(v, dict)}
        return self._send(200, {"name": d["_name"], "offset": "0x%08X" % d["_offset"],
                                "fields": fields, "weapon_growth": d["weapon_growth"]})

    def _save(self):
        ed = STATE["editor"]
        if not ed:
            return self._send(400, {"error": "No ISO open."})
        data = self._read_json()
        try:
            c = ed.find_char(data["name"]); first = True
            for e in data.get("edits", []):
                ed.set_field(c, e["key"], e["value"], backup=first); first = False
            g = data.get("weapon_growth")
            if g is not None:
                for i, val in enumerate(g, start=1):
                    ed.set_weapon_growth(c, i, val, backup=first); first = False
        except Exception as e:  # noqa
            return self._send(400, {"error": str(e)})
        return self._send(200, {"ok": True, "message": "Saved. Backup at %s.bak" % ed.path})

    # --- tables --------------------------------------------------------
    def _tables(self):
        ed = STATE["editor"]
        if not ed:
            return self._send(400, {"error": "No ISO open."})
        out = {}
        for name, t in ed.tables.items():
            id_names = stfields.load_data(t["id_list"]) if t.get("id_list") else {}
            out[name] = {"count": t["count"], "stride": t["stride"],
                         "fields": t["fields"], "id_list": t.get("id_list"),
                         "id_names": id_names}
        return self._send(200, out)

    def _trecord(self):
        ed = STATE["editor"]
        if not ed:
            return self._send(400, {"error": "No ISO open."})
        q = self._q()
        try:
            return self._send(200, ed.decode_table_record(q["table"][0], int(q["id"][0])))
        except Exception as e:  # noqa
            return self._send(400, {"error": str(e)})

    def _tsave(self):
        ed = STATE["editor"]
        if not ed:
            return self._send(400, {"error": "No ISO open."})
        data = self._read_json()
        try:
            name = data["table"]; rec_id = int(data["id"]); edits = data.get("edits", []); first = True
            for e in edits:
                ed.set_table_field(name, rec_id, int(e["off"]), int(e["w"]), int(e["value"]), backup=first)
                first = False
        except Exception as e:  # noqa
            return self._send(400, {"error": str(e)})
        return self._send(200, {"ok": True, "message": "Saved %d field(s). Backup at %s.bak"
                                % (len(edits), ed.path)})

    # --- Hard Mode -----------------------------------------------------
    def _hardmode(self):
        ed = STATE["editor"]
        if not ed:
            return self._send(400, {"error": "No ISO open."})
        data = self._read_json()
        try:
            mult = float(data.get("multiplier", 1.0))
        except Exception:  # noqa
            return self._send(400, {"error": "bad multiplier"})
        base = STATE.get("baseline", {})
        changed = 0; first = True
        for c in ed.chars:
            b = base.get(c["name"])
            if not b:
                continue
            for key, orig in b.items():
                if orig == 0:
                    continue
                nv = max(1, min(0x0C, int(round(orig * mult))))
                ed.set_field(c, key, nv, backup=first); first = False; changed += 1
        return self._send(200, {"ok": True, "message":
                                "Applied x%.2f to %d growth-rank fields across %d characters (from baseline). Backup at %s.bak"
                                % (mult, changed, len(base), ed.path)})


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Suikoden Tactics Editor</title>
<style>
 :root{--bg:#1a1113;--panel:#241619;--ink:#f3e6c4;--muted:#b89;--gold:#d8b45a;--crim:#a52434;--line:#3a2429;--chg:#e8a33d;--ok:#2e6b41;}
 [data-theme=light]{--bg:#f5efe1;--panel:#fffdf7;--ink:#2a1c1f;--muted:#7a6;--gold:#9a6b1e;--crim:#a52434;--line:#e2d6bd;--chg:#b9791a;--ok:#2e6b41;}
 *{box-sizing:border-box} body{margin:0;font:14px/1.5 system-ui,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--ink)}
 header{background:linear-gradient(180deg,color-mix(in srgb,var(--panel) 80%,#000),var(--bg));border-bottom:2px solid var(--gold);padding:12px 20px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
 h1{margin:0;font-size:18px;color:var(--gold)} .sub{color:var(--muted);font-size:12px}
 main{max-width:1000px;margin:0 auto;padding:18px}
 .card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:16px;margin-bottom:16px}
 label{display:block;font-size:12px;color:var(--gold);margin:0 0 4px}
 input,select,button{font:inherit;color:var(--ink);background:color-mix(in srgb,var(--bg) 70%,#000);border:1px solid var(--line);border-radius:6px;padding:7px 9px}
 [data-theme=light] input,[data-theme=light] select{background:#fff}
 input[type=text],input[type=number]{width:100%}
 button{background:var(--crim);border-color:#7d1a26;color:#fff;cursor:pointer;font-weight:600}
 button.ghost{background:transparent;border-color:var(--line);color:var(--ink);font-weight:500}
 button:hover{filter:brightness(1.12)} button:disabled{opacity:.45;cursor:default}
 .row{display:flex;gap:10px;align-items:end;flex-wrap:wrap} .spacer{flex:1}
 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:12px}
 .fld{position:relative} .fld input.chg,.fld select.chg{border-color:var(--chg);box-shadow:0 0 0 1px var(--chg)}
 .restore{position:absolute;right:6px;top:26px;padding:0 6px;font-size:12px;line-height:20px;background:transparent;border:1px solid var(--line);color:var(--muted);display:none}
 .fld .chg ~ .restore{display:block}
 .msg{padding:8px 12px;border-radius:6px;margin-top:10px;font-size:13px;display:none}
 .msg.ok{background:color-mix(in srgb,var(--ok) 30%,var(--bg));color:#9be3ae;border:1px solid var(--ok);display:block}
 .msg.err{background:#3a1820;color:#f0a6b1;border:1px solid #7d1a26;display:block}
 .tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:14px;align-items:center}
 .tablabel{color:var(--muted);font-size:12px;margin-right:4px}
 .tabs button{background:transparent;border:1px solid var(--line);color:var(--ink);font-weight:500}
 .tabs button.active{background:var(--gold);color:#241619;border-color:var(--gold)}
 .wg{display:grid;grid-template-columns:repeat(8,1fr);gap:6px} .wg input{text-align:center;padding:6px 2px}
 table{width:100%;border-collapse:collapse;font-size:13px} td,th{border-bottom:1px solid var(--line);padding:4px 8px;text-align:left} th{color:var(--gold)}
 .hide{display:none} small{color:var(--muted)}
 footer{margin:24px 0 8px;text-align:center;color:var(--muted);font-size:12px}
 footer a{color:var(--gold);text-decoration:none} footer a:hover{text-decoration:underline}
 .unsaved{color:var(--chg);font-weight:700;margin-left:6px}
 .sechead{display:flex;align-items:center;gap:10px;margin-bottom:10px}
 .sechead h2{font-size:15px;margin:0;color:var(--gold)}
 .raw{font-family:ui-monospace,Menlo,monospace;font-size:12px;color:var(--muted);word-break:break-all}
</style></head><body data-theme="dark">
<header>
 <h1>Suikoden Tactics — Editor</h1>
 <span class="sub">USA / SLUS-21245 · local only</span>
 <span class="spacer"></span>
 <button class="ghost" id="themeBtn">Theme</button>
</header>
<main>
 <div class="card">
  <label>ISO path <small>(leave empty and click Open for a file picker) — for editing game data (characters, tables). To edit memory-card saves instead, use the Save editor view below; no ISO needed.</small></label>
  <div class="row"><input id="path" type="text" placeholder="/full/path/to/Suikoden Tactics (USA).iso">
  <button id="openBtn">Open</button></div>
  <div id="openMsg" class="msg"></div>
 </div>

 <div id="app" class="hide">
  <div class="tabs" id="tabs"></div>

  <!-- Characters -->
  <div class="card sec" data-sec="characters">
   <div class="sechead"><h2>Character</h2><span class="unsaved hide">● unsaved</span><span class="spacer"></span>
    <button class="ghost revert">Revert</button><button class="save">Save</button></div>
   <div class="row">
    <div style="flex:1;min-width:200px"><label>Search</label><input id="charFilter" type="text" placeholder="filter by name"></div>
    <div style="flex:1;min-width:200px"><label>Character</label><select id="charSel"></select></div>
    <div><label>Offset</label><input id="charOff" type="text" readonly style="width:120px"></div>
   </div>
   <div class="grid" id="charFields" style="margin-top:12px"></div>
   <div class="row" style="margin-top:12px;align-items:end">
    <div style="flex:1"><label>Weapon growth (L1–L8)</label><div class="wg" id="wg"></div></div>
    <div><label>Scale ×</label><input id="wgScale" type="number" step="0.05" value="1" style="width:90px"></div>
    <button class="ghost" id="wgScaleBtn">Apply scale</button>
   </div>
   <div class="msg secmsg"></div>
  </div>

  <!-- Data tables -->
  <div class="card sec hide" data-sec="tables">
   <div class="sechead"><h2 id="tableTitle">Table</h2><span class="unsaved hide">● unsaved</span><span class="spacer"></span>
    <button class="ghost revert">Revert</button><button class="save">Save</button></div>
   <div class="row">
    <div><label>Table</label><select id="tableSel"></select></div>
    <div style="flex:1;min-width:180px"><label>Search record</label><input id="recFilter" type="text" placeholder="filter"></div>
    <div style="flex:1;min-width:180px"><label>Record</label><select id="recSel"></select></div>
   </div>
   <small>Field meanings unverified except where labeled (e.g. Price ×10). Values are the exact bytes V11 edits — safe to write (byte-bounded, backup).</small>
   <div class="grid" id="tFields" style="margin-top:12px"></div>
   <div style="margin-top:10px"><label>Raw record bytes</label><div id="tRaw" class="raw"></div></div>
   <div class="msg secmsg"></div>
  </div>

  <!-- Hard Mode -->
  <div class="card sec hide" data-sec="hardmode">
   <div class="sechead"><h2>Hard Mode — party growth</h2></div>
   <small>Scales every character's stat growth ranks from the values loaded when you opened the ISO (idempotent; ×1 restores). Ranks clamp to F–SS+.</small>
   <div class="row" style="margin-top:10px">
    <div><label>Growth multiplier ×</label><input id="hmMult" type="number" step="0.05" value="0.75" style="width:110px"></div>
    <button id="hmApply">Apply to all characters</button>
    <button class="ghost" id="hmRestore">Restore (×1)</button>
   </div>
   <div class="msg secmsg"></div>
  </div>

  <!-- Save editor -->
  <div class="card sec hide" data-sec="saveedit">
   <div class="sechead"><h2>Save editor (memory-card / export)</h2></div>
   <small>Opens any ST save container (.sps/.xps/.cbs/.max/.psu) with <b>verified</b>
   field decoding: party stats, equipment, runes, magic levels, recruitment
   (incl. the Suikoden IV hero unlock), gold, skill points, and inventory.
   Both game checksums (CRC-32 + MD5) are recomputed on write. Export to .psu
   (importable via mymc/uLaunchELF), repack .sps/.xps, or inject into an
   existing memory-card folder.</small>
   <div class="row" style="margin-top:10px">
    <div style="flex:1;min-width:260px"><label>Save file path <small>(leave empty and click Open for a file picker)</small></label><input id="svPath" type="text" placeholder="/path/to/suikoden-tactics.NNN.xps"></div>
    <button id="svOpen">Open save</button>
    <button class="ghost" id="svScan">Scan for saves</button>
   </div>
   <div id="svScanBox" class="hide" style="margin-top:8px">
    <label>Saves found (click to open)</label>
    <div style="max-height:220px;overflow:auto"><table><thead><tr><th>File</th><th>Save</th><th>Region</th><th>Checksums</th></tr></thead><tbody id="svScanSaves"></tbody></table></div>
    <label style="margin-top:8px;display:block">Memory cards found (click a folder to target card injection)</label>
    <div style="max-height:160px;overflow:auto"><table><thead><tr><th>Card</th><th>Folders</th><th>ST save folders</th></tr></thead><tbody id="svScanCards"></tbody></table></div>
   </div>
   <div id="svMsg" class="msg"></div>
   <div id="svBody" class="hide">
    <h3 style="color:var(--gold);font-size:14px;margin:14px 0 6px">Globals</h3>
    <div class="row">
     <div><label>Gold (potch)</label><input id="svGold" type="number" min="0" max="9999999" style="width:140px"></div>
     <div><label>Skill points</label><input id="svSP" type="number" min="0" max="9999999" style="width:140px"></div>
    </div>
    <h3 style="color:var(--gold);font-size:14px;margin:16px 0 6px">Character</h3>
    <div class="row">
     <div style="flex:1;min-width:220px"><label>Character (party slot)</label><select id="svChar"></select></div>
     <div><label>Recruited</label><br><input id="svRecruited" type="checkbox" style="width:22px;height:22px"></div>
     <div><label>EXP</label><input id="svExp" type="number" min="0" style="width:110px"></div>
     <div><label>HP</label><input id="svHpc" type="number" min="0" max="9999" style="width:80px"></div>
     <div><label>HP max</label><input id="svHpm" type="number" min="0" max="9999" style="width:80px"></div>
    </div>
    <div style="margin-top:8px"><label>Stats</label><div class="grid" id="svStats"></div></div>
    <div style="margin-top:8px"><label>Plus stats (equipment bonuses)</label><div class="grid" id="svPlus"></div></div>
    <div style="margin-top:8px"><label>Equipment</label><div class="grid" id="svEquip"></div></div>
    <div style="margin-top:8px"><label>Runes</label><div class="grid" id="svRunes"></div></div>
    <div style="margin-top:8px"><label>Magic levels (overall / current, slots 1-4)</label><div class="grid" id="svMagic"></div></div>
    <h3 style="color:var(--gold);font-size:14px;margin:16px 0 6px">Inventory</h3>
    <div class="row"><div style="flex:1"><label>Filter</label><input id="svInvFilter" placeholder="item name or id"></div>
     <div><label>Show empty slots</label><br><input id="svInvEmpty" type="checkbox" style="width:22px;height:22px"></div></div>
    <div style="max-height:300px;overflow:auto;margin-top:6px">
     <table><thead><tr><th>Slot</th><th>Item</th><th>Qty</th></tr></thead><tbody id="svInvBody"></tbody></table>
    </div>
    <details style="margin-top:12px"><summary style="color:var(--muted);cursor:pointer">Advanced: raw byte edits</summary>
     <div id="svEdits"></div>
     <div class="row" style="margin-top:6px"><button class="ghost" id="svAddEdit">+ add byte edit</button></div>
    </details>
    <hr style="border-color:var(--line);margin:14px 0">
    <div class="row">
     <div><label>Export as</label><select id="svTarget"><option value="psu">.psu (mymc import)</option><option value="sps">.sps/.xps repack</option><option value="card">inject into card folder</option></select></div>
     <div style="flex:1;min-width:220px"><label>Output path</label><input id="svOut" type="text" placeholder="/path/to/output"></div>
    </div>
    <div class="row" id="svCardRow" style="display:none">
     <div style="flex:1;min-width:220px"><label>Card (.ps2) path</label><input id="svCard" type="text" placeholder="/path/to/Mcd.ps2"></div>
     <div style="flex:1;min-width:160px"><label>Folder on card</label><input id="svFolder" type="text" placeholder="BASLUS-21245-00"></div>
    </div>
    <div class="row" style="margin-top:8px"><button id="svWrite">Write / export</button></div>
    <div class="msg secmsg" id="svWriteMsg"></div>
   </div>
  </div>

  <!-- Reference -->
  <div class="card sec hide" data-sec="reference">
   <div class="sechead"><h2>Reference lists</h2></div>
   <div class="row"><div><label>List</label><select id="refSel"></select></div>
    <div style="flex:1"><label>Search</label><input id="refFilter" type="text" placeholder="filter"></div></div>
   <table><thead><tr><th>ID</th><th>Name</th></tr></thead><tbody id="refBody"></tbody></table>
  </div>
 </div>
 <footer>Made by Sparda · <a href="https://github.com/TheSparda/Suikoden-Tactics-Editor" target="_blank" rel="noopener">github.com/TheSparda/Suikoden-Tactics-Editor</a></footer>
</main>
<script>
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
async function jget(u){return (await fetch(u)).json();}
async function jpost(u,b){return (await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)})).json();}
function msg(el,m,ok){el.textContent=m;el.className='msg '+(ok?'ok':'err');}

// theme
const th=localStorage.getItem('st_theme')||'dark'; document.body.dataset.theme=th;
$('#themeBtn').onclick=()=>{const t=document.body.dataset.theme==='dark'?'light':'dark';document.body.dataset.theme=t;localStorage.setItem('st_theme',t);};

let META,TABLES,LISTS,CHARS=[],SAVE=null;
const TABS=[['characters','Characters'],['tables','Data tables'],['hardmode','Hard Mode'],['saveedit','Save editor'],['reference','Reference']];
function showTab(id){$$('.sec').forEach(s=>s.classList.toggle('hide',s.dataset.sec!==id));$$('#tabs button').forEach(b=>b.classList.toggle('active',b.dataset.t===id));}
function buildTabs(tabs,def){$('#tabs').innerHTML='<span class="tablabel">View:</span>'+tabs.map(([id,l])=>`<button data-t="${id}">${l}</button>`).join('');$$('#tabs button').forEach(b=>b.onclick=()=>showTab(b.dataset.t));showTab(def);}

// --- staging helpers: inputs carry data-base; amber when differs; per-field restore ---
function wrapField(inner){return `<div class="fld">${inner}<button type="button" class="restore" title="restore">↺</button></div>`;}
function bindStaging(sec){
  const root=$(`.sec[data-sec="${sec}"]`);
  root.querySelectorAll('input[data-base],select[data-base]').forEach(el=>{
    el.oninput=()=>{el.classList.toggle('chg',String(el.value)!==String(el.dataset.base));updUnsaved(sec);};
    const r=el.parentElement.querySelector('.restore');
    if(r)r.onclick=()=>{el.value=el.dataset.base;el.classList.remove('chg');updUnsaved(sec);};
  });
  updUnsaved(sec);
}
function updUnsaved(sec){const root=$(`.sec[data-sec="${sec}"]`);const any=[...root.querySelectorAll('.chg')].length>0;const u=root.querySelector('.unsaved');if(u)u.classList.toggle('hide',!any);}
function revertSec(sec){$(`.sec[data-sec="${sec}"]`).querySelectorAll('input[data-base],select[data-base]').forEach(el=>{el.value=el.dataset.base;el.classList.remove('chg');});updUnsaved(sec);}
$$('.revert').forEach(b=>b.onclick=()=>revertSec(b.closest('.sec').dataset.sec));

// ---- open ----
$('#openBtn').onclick=async()=>{
  let path=$('#path').value.trim();
  if(!path){
    const b=await jpost('/api/save_browse',{});
    if(b.error){msg($('#openMsg'),b.error+' — type the ISO path instead.',false);return;}
    if(b.cancelled)return;
    path=b.path; $('#path').value=path;
  }
  const r=await jpost('/api/open',{path});
  if(r.error){msg($('#openMsg'),r.error,false);return;}
  msg($('#openMsg'),r.message,true);
  META=await jget('/api/meta'); LISTS=await jget('/api/lists'); TABLES=await jget('/api/tables');
  CHARS=r.characters;
  buildTabs(TABS,'characters'); $('#app').classList.remove('hide');
  initChars(); initTables(); initRef();
};

// ---- characters ----
function fillCharSel(){const f=$('#charFilter').value.toLowerCase();$('#charSel').innerHTML=CHARS.filter(c=>c.toLowerCase().includes(f)).map(c=>`<option>${c}</option>`).join('');loadChar();}
function initChars(){$('#charFilter').oninput=fillCharSel;$('#charSel').onchange=loadChar;fillCharSel();}
async function loadChar(){
  const name=$('#charSel').value; if(!name)return;
  const d=await jget('/api/char?name='+encodeURIComponent(name));
  if(d.error){msg($('.sec[data-sec=characters] .secmsg'),d.error,false);return;}
  $('#charOff').value=d.offset;
  $('#charFields').innerHTML=META.fields.map(f=>{
    const v=d.fields[f.key]?d.fields[f.key].value:0;
    let ctl;
    if(f.options)ctl=`<select data-key="${f.key}" data-base="${v}">`+f.options.map(o=>`<option value="${o.value}" ${o.value==v?'selected':''}>${o.label}</option>`).join('')+`</select>`;
    else ctl=`<input data-key="${f.key}" data-base="${v}" type="number" min="0" max="${Math.pow(256,f.w)-1}" value="${v}">`;
    return `<div class="fld"><label>${f.label} <small>@0x${f.offset.toString(16)}</small></label>${ctl}<button type="button" class="restore">↺</button></div>`;
  }).join('');
  $('#wg').innerHTML=d.weapon_growth.map((v,i)=>`<div class="fld"><input data-wg="${i}" data-base="${v}" type="number" min="0" max="255" value="${v}" title="L${i+1}"></div>`).join('');
  bindStaging('characters');
}
$('#wgScaleBtn').onclick=()=>{const s=parseFloat($('#wgScale').value)||1;$$('#wg input').forEach(e=>{e.value=Math.max(0,Math.min(255,Math.round((+e.dataset.base)*s)));e.dispatchEvent(new Event('input'));});};
$('.sec[data-sec=characters] .save').onclick=async()=>{
  const edits=META.fields.map(f=>({key:f.key,value:parseInt($(`[data-key="${f.key}"]`).value||0)}));
  const wg=$$('#wg input').map(e=>parseInt(e.value||0));
  const r=await jpost('/api/save',{name:$('#charSel').value,edits,weapon_growth:wg});
  msg($('.sec[data-sec=characters] .secmsg'),r.error||r.message,!r.error); if(!r.error)loadChar();
};

// ---- tables ----
function initTables(){const ts=$('#tableSel');ts.innerHTML=Object.keys(TABLES).map(n=>`<option>${n}</option>`).join('');ts.onchange=fillRecs;$('#recFilter').oninput=fillRecs;$('#recSel').onchange=loadRec;fillRecs();}
function recName(t,i){return t.id_names[i.toString(16).toUpperCase().padStart(2,'0')]||'';}
function fillRecs(){
  const t=TABLES[$('#tableSel').value];$('#tableTitle').textContent=$('#tableSel').value;
  const f=$('#recFilter').value.toLowerCase();let o='';
  for(let i=0;i<t.count;i++){const nm=recName(t,i);const s=`${i}${nm?' - '+nm:''}`;if(f&&!s.toLowerCase().includes(f))continue;o+=`<option value="${i}">${s}</option>`;}
  $('#recSel').innerHTML=o;loadRec();
}
async function loadRec(){
  const table=$('#tableSel').value,id=$('#recSel').value; if(id===''||id==null)return;
  const d=await jget(`/api/trecord?table=${table}&id=${id}`);
  if(d.error){msg($('.sec[data-sec=tables] .secmsg'),d.error,false);return;}
  $('#tRaw').textContent=d.raw.replace(/(..)/g,'$1 ').trim();
  $('#tFields').innerHTML=d.fields.map(f=>`<div class="fld"><label>${f.label}</label><input data-off="${f.off}" data-w="${f.w}" data-base="${f.value}" type="number" min="0" max="${Math.pow(256,f.w)-1}" value="${f.value}"><button type="button" class="restore">↺</button></div>`).join('');
  bindStaging('tables');
}
$('.sec[data-sec=tables] .save').onclick=async()=>{
  const edits=$$('#tFields input').map(e=>({off:+e.dataset.off,w:+e.dataset.w,value:parseInt(e.value||0)}));
  const r=await jpost('/api/tsave',{table:$('#tableSel').value,id:+$('#recSel').value,edits});
  msg($('.sec[data-sec=tables] .secmsg'),r.error||r.message,!r.error); if(!r.error)loadRec();
};

// ---- hard mode ----
$('#hmApply').onclick=async()=>{const r=await jpost('/api/hardmode',{multiplier:parseFloat($('#hmMult').value)||1});msg($('.sec[data-sec=hardmode] .secmsg'),r.error||r.message,!r.error);if(!r.error)loadChar();};
$('#hmRestore').onclick=async()=>{const r=await jpost('/api/hardmode',{multiplier:1});msg($('.sec[data-sec=hardmode] .secmsg'),r.error||r.message,!r.error);if(!r.error)loadChar();};

// ---- reference ----
function initRef(){const rs=$('#refSel');rs.innerHTML=Object.keys(LISTS).map(n=>`<option>${n}</option>`).join('');rs.onchange=renderRef;$('#refFilter').oninput=renderRef;renderRef();}
function renderRef(){const m=LISTS[$('#refSel').value]||{};const f=$('#refFilter').value.toLowerCase();
  $('#refBody').innerHTML=Object.keys(m).sort((a,b)=>parseInt(a,16)-parseInt(b,16)).filter(k=>!f||m[k].toLowerCase().includes(f)||k.toLowerCase().includes(f)).map(k=>`<tr><td>0x${k}</td><td>${m[k]}</td></tr>`).join('');}

// ---- save editor (works without an ISO) ----
let SVD=null, SV_DIRTY_CH=new Set(), SV_DIRTY_INV=new Set(), SVLISTS=null;
function svEditRow(){return `<div class="row svrow" style="margin-top:6px"><input class="svoff" placeholder="offset (hex, e.g. 1a3f)" style="width:170px"><input class="svval" type="number" min="0" max="255" placeholder="byte 0-255" style="width:130px"><button class="ghost svdel" type="button">✕</button></div>`;}
function bindSvDel(){$$('#svEdits .svdel').forEach(b=>b.onclick=()=>b.closest('.svrow').remove());}
function itemName(id){if(!SVLISTS)return'';return SVLISTS.shop_items[String(id).padStart(3,'0')]||'';}
function itemLabel(id){const nm=itemName(id);return id?(nm?`${nm} [${id}]`:`unknown [${id}]`):'';}
function itemOpts(sel){let o=`<option value="0" ${sel===0?'selected':''}>— empty —</option>`;const m=SVLISTS.shop_items;
  Object.keys(m).sort((a,b)=>(+a)-(+b)).forEach(k=>{const v=parseInt(k,10);if(!v)return;o+=`<option value="${v}" ${v===sel?'selected':''}>${m[k]} [${v}]</option>`;});
  if(sel&&!itemName(sel))o+=`<option value="${sel}" selected>unknown [${sel}]</option>`;return o;}
function parseItem(v){v=(v||'').trim();if(v==='')return 0;const m=v.match(/\[(\d+)\]\s*$/);if(m)return +m[1];if(/^\d+$/.test(v))return +v;
  const hit=Object.entries(SVLISTS.shop_items).find(([k,n])=>n.toLowerCase()===v.toLowerCase());return hit?parseInt(hit[0],10):null;}
function ensureItemsDL(){if(document.getElementById('itemsDL'))return;const dl=document.createElement('datalist');dl.id='itemsDL';
  dl.innerHTML=Object.keys(SVLISTS.shop_items).sort((a,b)=>(+a)-(+b)).map(k=>{const v=parseInt(k,10);return v?`<option value="${SVLISTS.shop_items[k]} [${v}]"></option>`:'';}).join('');
  document.body.appendChild(dl);}
function runeOpts(sel){let o='<option value="0">— none —</option>';const m=SVLISTS?SVLISTS.runes:{};Object.keys(m).sort((a,b)=>parseInt(a,16)-parseInt(b,16)).forEach(k=>{const v=parseInt(k,16);o+=`<option value="${v}" ${v===sel?'selected':''}>${m[k]} (0x${k})</option>`;});return o;}
function svCurrent(){return SVD.chars[+$('#svChar').value];}
function svRenderChar(){
  const c=svCurrent(); if(!c)return;
  $('#svRecruited').checked=c.recruited; $('#svExp').value=c.exp;
  $('#svHpc').value=c.hp_cur; $('#svHpm').value=c.hp_max;
  $('#svStats').innerHTML=SVD.stat_keys.map(k=>`<div class="fld"><label>${k}</label><input data-sk="${k}" type="number" min="0" max="999" value="${c.stats[k]}"></div>`).join('');
  $('#svPlus').innerHTML=SVD.plus_keys.map(k=>`<div class="fld"><label>${k}</label><input data-pk="${k}" type="number" min="0" max="999" value="${c.plus[k]}"></div>`).join('');
  $('#svEquip').innerHTML=SVD.equip_keys.map(k=>{const v=c.equip[k];return `<div class="fld"><label>${k}</label><select data-ek="${k}">${itemOpts(v)}</select></div>`;}).join('');
  $('#svRunes').innerHTML=SVD.rune_keys.map((k,i)=>`<div class="fld"><label>${k}</label><select data-rk="${k}">${runeOpts(c.runes[k])}</select></div>`).join('');
  $('#svMagic').innerHTML=[0,1,2,3].map(i=>`<div class="fld"><label>Slot ${i+1} all/cur</label><div class="row"><input data-mo="${i}" type="number" min="0" max="9" value="${c.magic_overall[i]}" style="width:70px"><input data-mc="${i}" type="number" min="0" max="9" value="${c.magic_current[i]}" style="width:70px"></div></div>`).join('');
  // bind edits -> write into SVD + dirty
  const idx=+$('#svChar').value;
  const mark=()=>SV_DIRTY_CH.add(idx);
  $('#svRecruited').onchange=()=>{c.recruited=$('#svRecruited').checked;mark();};
  $('#svExp').oninput=()=>{c.exp=+$('#svExp').value||0;mark();};
  $('#svHpc').oninput=()=>{c.hp_cur=+$('#svHpc').value||0;mark();};
  $('#svHpm').oninput=()=>{c.hp_max=+$('#svHpm').value||0;mark();};
  $$('#svStats input').forEach(e=>e.oninput=()=>{c.stats[e.dataset.sk]=+e.value||0;mark();});
  $$('#svPlus input').forEach(e=>e.oninput=()=>{c.plus[e.dataset.pk]=+e.value||0;mark();});
  $$('#svEquip select').forEach(e=>e.onchange=()=>{c.equip[e.dataset.ek]=+e.value||0;mark();});
  $$('#svRunes select').forEach(e=>e.onchange=()=>{c.runes[e.dataset.rk]=+e.value||0;mark();});
  $$('#svMagic input[data-mo]').forEach(e=>e.oninput=()=>{c.magic_overall[+e.dataset.mo]=+e.value||0;mark();});
  $$('#svMagic input[data-mc]').forEach(e=>e.oninput=()=>{c.magic_current[+e.dataset.mc]=+e.value||0;mark();});
}
function svRenderInv(){
  const f=($('#svInvFilter').value||'').toLowerCase(); const showEmpty=$('#svInvEmpty').checked;
  $('#svInvBody').innerHTML=SVD.inventory.filter(s=>{
    if(!showEmpty&&!s.id)return false;
    if(f){const nm=itemName(s.id).toLowerCase();if(!nm.includes(f)&&String(s.id)!==f)return false;}
    return true;
  }).slice(0,250).map(s=>`<tr><td>${s.slot}</td><td><input data-inv="${s.slot}" data-f="id" list="itemsDL" value="${itemLabel(s.id)}" placeholder="— empty — (type an item name)" style="width:260px"></td><td><input data-inv="${s.slot}" data-f="qty" type="number" min="0" max="99" value="${s.qty}" style="width:70px"></td></tr>`).join('');
  $$('#svInvBody input').forEach(e=>e.onchange=()=>{
    const s=SVD.inventory[+e.dataset.inv];
    if(e.dataset.f==='id'){
      const id=parseItem(e.value);
      if(id===null){e.style.borderColor='var(--chg)';return;}
      e.style.borderColor=''; s.id=id; e.value=itemLabel(id);
    } else { s.qty=+e.value||0; }
    SV_DIRTY_INV.add(+e.dataset.inv);
  });
}
function svApplyState(st){
  SVD=st; SV_DIRTY_CH.clear(); SV_DIRTY_INV.clear(); ensureItemsDL();
  $('#svGold').value=st.globals.gold; $('#svSP').value=st.globals.skill_points;
  $('#svChar').innerHTML=st.chars.map((c,i)=>`<option value="${i}">${c.name}${c.recruited?'':' (not recruited)'}</option>`).join('');
  svRenderChar(); svRenderInv();
}
async function svWrite(){
  const edits=$$('#svEdits .svrow').map(r=>({off:parseInt(r.querySelector('.svoff').value,16),val:parseInt(r.querySelector('.svval').value||0)})).filter(e=>!isNaN(e.off));
  const char_edits=[...SV_DIRTY_CH].map(i=>({index:i,char:SVD.chars[i]}));
  const inv_edits=[...SV_DIRTY_INV].map(s=>({slot:s,id:SVD.inventory[s].id,qty:SVD.inventory[s].qty}));
  const globals={gold:+$('#svGold').value||0,skill_points:+$('#svSP').value||0};
  const r=await jpost('/api/save_write',{edits,char_edits,inv_edits,globals,target:$('#svTarget').value,out:$('#svOut').value,card:$('#svCard').value,folder:$('#svFolder').value});
  msg($('#svWriteMsg'),r.error||r.message,!r.error);
  if(!r.error&&r.state)svApplyState(r.state);
}
function cleanPath(p){return (p||'').trim().replace(/^["']|["']$/g,'').replace(/^file:\/\//,'').trim();}
async function svOpenPath(path){
  path=cleanPath(path);
  msg($('#svMsg'),'Opening…',true);
  let r;
  try{ if(!SVLISTS)SVLISTS=await jget('/api/lists'); r=await jpost('/api/save_open',{path}); }
  catch(e){ msg($('#svMsg'),'Request failed (is the editor still running? reload the page): '+e.message,false); return; }
  if(r.error){msg($('#svMsg'),r.error,false);$('#svBody').classList.add('hide');return;}
  $('#svPath').value=path;
  msg($('#svMsg'),r.message,true); $('#svBody').classList.remove('hide');
  const sps=$('#svTarget option[value=sps]'); if(sps)sps.disabled=!r.can_sps;
  $('#svEdits').innerHTML='';
  svApplyState(r.state);
  $('#svBody').scrollIntoView({behavior:'smooth',block:'start'});
}
async function svScan(){
  msg($('#svMsg'),'Scanning for saves and memory cards…',true);
  const r=await jpost('/api/save_scan',{});
  if(r.error){msg($('#svMsg'),r.error,false);return;}
  msg($('#svMsg'),r.message,true);
  $('#svScanBox').classList.remove('hide');
  $('#svScanSaves').innerHTML=r.saves.map(s=>`<tr style="cursor:pointer" data-p="${s.path.replace(/"/g,'&quot;')}"><td>${s.file}</td><td>${s.dirname}</td><td>${s.region}</td><td>${s.ok?'OK':'BAD'}</td></tr>`).join('')||'<tr><td colspan=4>none found</td></tr>';
  $$('#svScanSaves tr[data-p]').forEach(tr=>tr.onclick=()=>svOpenPath(tr.dataset.p));
  $('#svScanCards').innerHTML=r.cards.map(c=>{
    const links=(c.st_folders||[]).map(f=>`<a href="#" data-card="${c.path.replace(/"/g,'&quot;')}" data-folder="${f}">${f}</a>`).join(' · ')||'—';
    return `<tr><td>${c.file}</td><td>${c.folders}</td><td>${links}</td></tr>`;
  }).join('')||'<tr><td colspan=3>none found</td></tr>';
  $$('#svScanCards a[data-card]').forEach(a=>a.onclick=(e)=>{e.preventDefault();
    $('#svTarget').value='card';$('#svCardRow').style.display='flex';
    $('#svCard').value=a.dataset.card;$('#svFolder').value=a.dataset.folder;
    msg($('#svWriteMsg'),'Card injection target set: '+a.dataset.folder,true);});
}
function initSave(){
  $('#svOpen').onclick=async()=>{
    let path=$('#svPath').value.trim();
    if(!path){
      const b=await jpost('/api/save_browse',{});
      if(b.error){msg($('#svMsg'),b.error+' — use Scan for saves instead.',false);return;}
      if(b.cancelled)return;
      path=b.path;
    }
    await svOpenPath(path);
  };
  $('#svScan').onclick=svScan;
  $('#svChar').onchange=svRenderChar;
  $('#svInvFilter').oninput=svRenderInv; $('#svInvEmpty').onchange=svRenderInv;
  $('#svAddEdit').onclick=()=>{$('#svEdits').insertAdjacentHTML('beforeend',svEditRow());bindSvDel();};
  $('#svTarget').onchange=()=>{$('#svCardRow').style.display=$('#svTarget').value==='card'?'flex':'none';};
  $('#svWrite').onclick=svWrite;
}
// reveal a minimal UI (Save editor) even before an ISO is opened
buildTabs([['saveedit','Save editor']],'saveedit');
$('#app').classList.remove('hide');
initSave();
</script></body></html>"""


def main():
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    url = "http://%s:%d/" % (HOST, PORT)
    print("Suikoden Tactics Editor running at %s" % url)
    print("(Ctrl+C to stop.)")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
