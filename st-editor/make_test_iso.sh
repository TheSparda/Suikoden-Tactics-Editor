#!/bin/bash
# Clone an ISO (copy-on-write when supported), apply a sample character edit,
# and print steps to verify in an emulator. Never touches the original.
#
# Usage: ./make_test_iso.sh "/path/to/Suikoden Tactics (USA).iso" [OutName.iso]
set -euo pipefail
SRC="${1:?usage: make_test_iso.sh SRC.iso [OUT.iso]}"
OUT="${2:-test_$(basename "$SRC")}"
HERE="$(cd "$(dirname "$0")" && pwd)"

[ -f "$SRC" ] || { echo "source not found: $SRC" >&2; exit 1; }
echo "Cloning $SRC -> $OUT"
cp -c "$SRC" "$OUT" 2>/dev/null || cp "$SRC" "$OUT"   # -c = APFS clonefile; falls back to full copy

echo "Applying sample edit (Kyril STR growth -> 8) ..."
python3 "$HERE/stpatch.py" --iso "$OUT" set Kyril str_growth 8
echo
python3 "$HERE/stpatch.py" --iso "$OUT" show Kyril | head -6
echo
cat <<EOF
Done. Test clone: $OUT  (backup: $OUT.bak)

Verify in an emulator (PCSX2):
  1. Load $OUT as the disc.
  2. Start a new game; check Kyril's growth reflects the edit.
  3. Compare against the original ISO to confirm only intended bytes changed:
       cmp -l "$SRC" "$OUT" | head
EOF
