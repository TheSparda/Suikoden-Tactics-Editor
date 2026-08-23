#!/usr/bin/env python3
"""Derive per-item descriptions from the local Neoseeker item list into a small
committed reference file (data/st_item_ref.json), keyed by the SAME item id the
UI uses (3-digit decimal, matching st_shop_items.json).

Only derived facts are committed — not the copyrighted guide prose. The guide
lives under Guides/ (gitignored); re-run when it changes:
    python3 build_item_ref.py
"""
import json, os, re, unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
GUIDES = os.path.normpath(os.path.join(HERE, "..", "Guides"))
DATA = os.path.join(HERE, "data")
SRC = os.path.join(GUIDES, "ItemList.txt")
OUT = os.path.join(DATA, "st_item_ref.json")


def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


def parse_items(path):
    """normalized name -> description, from 'Name:' / 'Description/Function:' pairs."""
    if not os.path.exists(path):
        return {}
    lines = [l.rstrip() for l in open(path, encoding="utf-8", errors="replace")]
    out = {}
    for i, line in enumerate(lines):
        m = re.match(r"\s*Name:\s*(.+)", line)
        if not m:
            continue
        name = m.group(1).strip()
        desc = ""
        for j in range(i + 1, min(i + 4, len(lines))):
            dm = re.match(r"\s*Description/Function:\s*(.+)", lines[j])
            if dm:
                desc = dm.group(1).strip()
                # description may wrap to the next line
                k = j + 1
                if k < len(lines) and lines[k].strip() and not re.match(r"\s*(Price|Shop|Bargain|Chest|Dig|Name):", lines[k]):
                    desc += " " + lines[k].strip()
                break
        if name and desc and desc.upper() not in ("NA", "N/A"):
            out[norm(name)] = re.sub(r"\s+", " ", desc)
    return out


def main():
    guide = parse_items(SRC)
    items = json.load(open(os.path.join(DATA, "st_shop_items.json"), encoding="utf-8"))
    result, matched = {}, 0
    for iid, name in items.items():
        g = guide.get(norm(name))
        if g:
            result[iid] = g
            matched += 1
    if not os.path.exists(SRC):
        print("WARNING: %s not found." % SRC)
    json.dump(result, open(OUT, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print("Wrote %s: %d/%d items matched." % (OUT, matched, len(items)))


if __name__ == "__main__":
    main()
