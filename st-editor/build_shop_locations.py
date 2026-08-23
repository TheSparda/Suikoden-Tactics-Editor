#!/usr/bin/env python3
"""Derive per-location shop stock from the local item guide into a small committed
file (data/st_shop_locations.json): { "Location": [item_id, ...] }.

The web ISO editor uses this to IDENTIFY a shop record in-browser — it matches the
record's live item list against these sets and shows the best-matching location.
The shop table has no name field, so this is a best-effort, clearly-flagged guess.

Only derived facts are committed, not the guide prose (Guides/ is gitignored).
Re-run when the guide changes:  python3 build_shop_locations.py
"""
import json, os, re, unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
GUIDES = os.path.normpath(os.path.join(HERE, "..", "Guides"))
DATA = os.path.join(HERE, "data")
SRC = os.path.join(GUIDES, "ItemList.txt")
OUT = os.path.join(DATA, "st_shop_locations.json")


def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


def main():
    items = json.load(open(os.path.join(DATA, "st_shop_items.json"), encoding="utf-8"))
    by_norm = {norm(v): int(k) for k, v in items.items()}   # item name -> id
    known_locs = set()
    lp = os.path.join(DATA, "st_locations.json")
    if os.path.exists(lp):
        known_locs = {norm(v) for v in json.load(open(lp, encoding="utf-8")).values()}

    if not os.path.exists(SRC):
        print("WARNING: %s not found." % SRC)
        json.dump({}, open(OUT, "w"), indent=1); return

    lines = [l.rstrip() for l in open(SRC, encoding="utf-8", errors="replace")]
    loc_items = {}   # display location name -> set(item id)
    i = 0
    while i < len(lines):
        m = re.match(r"\s*Name:\s*(.+)", lines[i])
        if not m:
            i += 1; continue
        name = m.group(1).strip()
        iid = by_norm.get(norm(name))
        # gather the (possibly wrapped) Shop Location line(s)
        loc_text = ""
        for j in range(i + 1, min(i + 8, len(lines))):
            lm = re.match(r"\s*Shop Location:\s*(.+)", lines[j])
            if lm:
                loc_text = lm.group(1).strip()
                k = j + 1
                while k < len(lines) and lines[k].strip() and not re.match(r"\s*(Name|Price|Bargain|Chest|Dig|Description):", lines[k]):
                    loc_text += " " + lines[k].strip(); k += 1
                break
            if re.match(r"\s*Name:", lines[j]):
                break
        i += 1
        if iid is None or not loc_text or loc_text.upper() in ("NA", "N/A"):
            continue
        # "All Shops" / "All Shops EXCEPT X" → add to every known location minus exceptions
        if re.search(r"all shops", loc_text, re.I):
            exc = set()
            em = re.search(r"except\s+(.+)", loc_text, re.I)
            if em:
                exc = {norm(x) for x in re.split(r"[,/]| and ", em.group(1))}
            for locn in json.load(open(lp, encoding="utf-8")).values() if os.path.exists(lp) else []:
                if norm(locn) not in exc:
                    loc_items.setdefault(locn, set()).add(iid)
            continue
        for part in re.split(r",|/| and ", loc_text):
            p = part.strip()
            if not p or norm(p) not in known_locs:
                continue
            # use the canonical display name from st_locations
            disp = next((v for v in json.load(open(lp, encoding="utf-8")).values() if norm(v) == norm(p)), p)
            loc_items.setdefault(disp, set()).add(iid)

    out = {loc: sorted(ids) for loc, ids in loc_items.items() if ids}
    json.dump(out, open(OUT, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print("Wrote %s: %d locations" % (OUT, len(out)))
    for loc, ids in sorted(out.items()):
        print("  %-24s %d items" % (loc, len(ids)))


if __name__ == "__main__":
    main()
