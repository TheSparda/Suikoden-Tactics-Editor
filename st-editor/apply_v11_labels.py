#!/usr/bin/env python3
"""Recover V11's real per-field labels from the decompiled IL and apply them to
data/st_tables.json (the single source both the desktop and web ISO editors read).

V11's InitializeComponent sets each Label's text immediately before loading its
data control, named list<T>_<F>NUD / nameList<T>TxtBox (T = table, F = field).
So label -> (T, F); F's byte offset comes from data/v11_table_fields.json. We map
our table name -> V11 list number by DISTINCTIVE label content (verified below),
then relabel each st_tables field whose offset matches a V11 field.

V11's exact wording is preserved (including its own hedged terms), so no meaning
is invented — these are the labels the V11 author shipped after editing the ROM.
Fields with no V11 label stay generic. Re-run after any table change.
"""
import re, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEC = os.path.normpath(os.path.join(HERE, "..", "Editor", "_decomp"))
IL = os.path.join(DEC, "full.il")
V11F = os.path.join(DEC, "v11_table_fields.json")
TABLES = os.path.join(HERE, "data", "st_tables.json")

# our table -> V11 list number, identified by distinctive label content:
#   list3 = shop bargains; list4 = rune shop orbs; list5 = Price (x10); list6 = skills;
#   list7 = enemies (HP growth, drop/steal chances, Jump)
# ITEMS is deliberately excluded: V11's item record writes across overlapping
# sub-blocks (per the RE notes), so its field<->offset mapping is ambiguous and
# would produce wrong labels. Item records already show the item NAME in the
# selector; their per-byte fields stay generic until verified in-emulator.
TABLE_LISTS = {"shop": [3], "runeshop": [4], "itemprice": [5], "skills": [6], "enemies": [7]}


def extract_labels():
    il = open(IL, encoding="utf-8", errors="replace").read().splitlines()
    ldstr = re.compile(r'ldstr\s+"((?:[^"\\]|\\.)*)"')
    settext = re.compile(r'set_Text\(string\)')
    ctrl = re.compile(r'Form1::(list(\d+)_(\d+)NUD|list(\d+)_(\d+)TxtBox|nameList(\d+)TxtBox)')
    out = {}  # (T,F) -> label
    i, n = 0, len(il)
    while i < n:
        m = ldstr.search(il[i])
        if m:
            lab = m.group(1)
            j, ok = i + 1, False
            while j < min(i + 3, n):
                if settext.search(il[j]): ok = True; break
                if ldstr.search(il[j]): break
                j += 1
            if ok:
                k = j + 1
                while k < min(j + 40, n):
                    c = ctrl.search(il[k])
                    if c:
                        if c.group(2): out[(int(c.group(2)), int(c.group(3)))] = lab
                        elif c.group(4): out[(int(c.group(4)), int(c.group(5)))] = lab
                        i = j; break
                    k += 1
        i += 1
    return out


def clean(lab):
    lab = lab.replace("\\r", " ").replace("\\n", " ").replace("\r", " ").replace("\n", " ").strip()
    return re.sub(r"\s+", " ", lab)


def main():
    labels = extract_labels()          # (T,F) -> label
    v11 = json.load(open(V11F, encoding="utf-8"))
    tables = json.load(open(TABLES, encoding="utf-8"))

    # per table: offset -> V11 label (via the correct list numbers)
    applied = {}
    for tname, lists in TABLE_LISTS.items():
        if tname not in v11:
            continue
        off_to_label = {}
        for f in v11[tname]["fields"]:
            for T in lists:
                if (T, f["n"]) in labels:
                    lab = clean(labels[(T, f["n"])])
                    # skip junk / hint-paragraph labels and designer leftovers
                    if (not lab or lab.startswith("label") or len(lab) > 40
                            or 'Patch' in lab or 'guarantee' in lab or lab.startswith('List of')):
                        continue
                    off_to_label.setdefault(f["off"], lab)
        applied[tname] = off_to_label

    # relabel st_tables fields whose offset has a V11 label
    changed = 0
    for tname, tinfo in tables.items():
        o2l = applied.get(tname, {})
        for f in tinfo["fields"]:
            if f["off"] in o2l:
                new = o2l[f["off"]]
                if f.get("label") != new:
                    f["label"] = new; changed += 1

    if "--write" in sys.argv:
        json.dump(tables, open(TABLES, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
        print("Updated %s (%d field labels set)." % (TABLES, changed))
    else:
        print("PREVIEW (%d labels would change). Run with --write to apply.\n" % changed)
        for tname in tables:
            o2l = applied.get(tname, {})
            named = sum(1 for f in tables[tname]["fields"] if f["off"] in o2l)
            print("== %s: %d/%d fields get V11 labels ==" % (tname, named, len(tables[tname]["fields"])))
            for f in tables[tname]["fields"]:
                if f["off"] in o2l:
                    print("   +0x%02X  %s" % (f["off"], o2l[f["off"]]))


if __name__ == "__main__":
    main()
