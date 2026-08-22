#!/usr/bin/env python3
"""Emit the character-record field schema from stfields.py as JSON so the web
ISO editor (web/iso.js) can consume the SAME offsets the desktop uses — one
source of truth, no hand-ported drift. Writes data/st_char_schema.json.

Run from st-editor/:  python3 gen_iso_schema.py
The web build/test asserts the committed JSON matches this output.
"""
import json, os
import stfields as F

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "st_char_schema.json")


def build():
    return {
        "record_len": F.RECORD_LEN,
        "growth_ranks": {str(k): v for k, v in F.GROWTH_RANKS.items()},
        "elements": {str(k): v for k, v in F.ELEMENTS.items()},
        "weapon_growth": {"offset": F.WEAPON_GROWTH_OFFSET, "levels": F.WEAPON_GROWTH_LEVELS},
        "skill_block": {"offset": F.SKILL_BLOCK_OFFSET, "count": F.SKILL_COUNT, "rank_max": F.SKILL_RANK_MAX},
        "fields": [
            {"key": f["key"], "label": f["label"], "offset": f["offset"],
             "kind": f["kind"], "w": F.field_width(f), **({"data": f["data"]} if "data" in f else {})}
            for f in F.FIELDS
        ],
    }


if __name__ == "__main__":
    schema = build()
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(schema, f, indent=1, ensure_ascii=False)
    print("wrote %s (%d fields, record_len 0x%X)" % (OUT, len(schema["fields"]), schema["record_len"]))
