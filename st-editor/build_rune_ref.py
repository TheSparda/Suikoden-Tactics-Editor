#!/usr/bin/env python3
"""Derive per-rune effect notes from the local GameFAQs rune guides into a small
committed reference file (data/st_rune_ref.json), keyed by the SAME rune id the
UI uses (hex, matching st_runes.json / st_rune_orbs.json).

Only the DERIVED FACTS are committed — never the copyrighted guide prose. The
guides live under Guides/ (gitignored); re-run this when they change:
    python3 build_rune_ref.py

Name-anchored + format-agnostic: for each known rune it finds the guide title
line (magic runes use a '----' underline, passive runes an 'Effect:' line, orbs
a '***' prefix) and extracts a one-line description + any 'Rank N:' abilities.
Missing runes are omitted (UI shows no note). Correct-or-blank.
"""
import json, os, re, unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
GUIDES = os.path.normpath(os.path.join(HERE, "..", "Guides"))
DATA = os.path.join(HERE, "data")
SOURCES = [
    os.path.join(GUIDES, "Suikoden Tactics - Rune Abilities Guide - PlayStation 2 - By Yeblos - GameFAQs.txt"),
    os.path.join(GUIDES, "Suikoden Tactics - Orb_Rune Guide - PlayStation 2 - By tumiwa - GameFAQs.txt"),
]
OUT = os.path.join(DATA, "st_rune_ref.json")

JUNK = re.compile(r"(bookmark|would you recommend|gamefaqs|gamespot|log in|sign up|^\d+/\d+/\d+|^\s*$|search this guide|table of contents|^\s*-+\s*$)", re.I)


def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = s.lower().replace("punishement", "punishment")
    return re.sub(r"[^a-z0-9]", "", s)


def clean_title(s):
    return s.strip().lstrip("*").strip()


def known_runes():
    out = {}
    for fn in ("st_runes.json", "st_rune_orbs.json"):
        p = os.path.join(DATA, fn)
        if not os.path.exists(p):
            continue
        for k, v in json.load(open(p, encoding="utf-8")).items():
            if v and v.strip().lower() not in ("blank", "?", "unused", "none"):
                out.setdefault(k, v)
    return out


def is_prose(line):
    t = line.strip()
    if len(t) < 6 or len(t) > 140 or JUNK.search(t):
        return False
    if re.search(r"\b(Rune|Orb)\b\s*$", t):      # looks like another title
        return False
    return bool(re.search(r"[a-z]", t)) and (" " in t)


def best_desc(lines, start, name_norm):
    """From a title line, return (desc, abilities) scanning the next few lines."""
    desc, abilities = "", []
    for j in range(start + 1, min(start + 10, len(lines))):
        t = lines[j].strip()
        m = re.match(r"Rank\s+(\d+)\s*:\s*(.+)", t)
        if m:
            abilities.append("Rank %s: %s" % (m.group(1), m.group(2).strip()[:40]))
            continue
        if not desc:
            em = re.match(r"Effect\s*:\s*(.+)", t, re.I)
            if em:
                desc = em.group(1).strip()
            elif re.fullmatch(r"-{3,}", t):
                continue
            elif is_prose(t) and norm(t) != name_norm:
                desc = t
        # stop if we hit another rune title
        if j > start + 1 and clean_title(lines[j]) and norm(clean_title(lines[j])).endswith("rune") and is_prose(lines[j]) is False and len(clean_title(lines[j])) < 30:
            break
    return desc.rstrip(" .") + ("." if desc and not desc.endswith(".") else ""), abilities


def parse():
    docs = []
    for p in SOURCES:
        if os.path.exists(p):
            docs.append([l.rstrip() for l in open(p, encoding="utf-8", errors="replace")])
    runes = known_runes()
    result, matched, missing = {}, 0, []
    for hid, name in runes.items():
        nkey = norm(name)
        found = None
        for lines in docs:
            best = None
            for i, line in enumerate(lines):
                if norm(clean_title(line)) == nkey:
                    d, ab = best_desc(lines, i, nkey)
                    score = (len(d) > 0) * 2 + len(ab)
                    if score and (best is None or score > best[0]):
                        best = (score, d, ab)
            if best:
                found = best
                break   # prefer earlier source (Yeblos)
        if found and (found[1] or found[2]):
            entry = {}
            if found[1]:
                entry["desc"] = found[1]
            if found[2]:
                entry["abilities"] = found[2][:8]
            result[hid] = entry
            matched += 1
        else:
            missing.append("%s %s" % (hid, name))

    if not docs:
        print("WARNING: no guide files found under %s" % GUIDES)
    json.dump(result, open(OUT, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print("Wrote %s: %d/%d runes matched." % (OUT, matched, len(runes)))
    if missing:
        print("Unmatched (%d): %s" % (len(missing), ", ".join(missing[:25]) + (" …" if len(missing) > 25 else "")))


if __name__ == "__main__":
    parse()
