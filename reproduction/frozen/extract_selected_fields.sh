#!/usr/bin/env bash
set -euo pipefail
if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
  echo "usage: $0 MAIN_WORKBOOK BRIDGE_WORKBOOK [OUT_DIR]" >&2
  exit 2
fi
MAIN=$1
BRIDGE=$2
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
OUT=${3:-$ROOT}
mkdir -p "$OUT"
EXPECTED_MAIN=bc7fd66ae50b53f2d425a8493336d7433ed4d625da9db94701dc1ae4d1b67ac4
EXPECTED_BRIDGE=f6caf4d1d2b311a41e6195b6dc4ce786b4ee7aa20a3aedbe1789d655336fbb08
ACTUAL_MAIN=$(sha256sum "$MAIN" | awk '{print $1}')
ACTUAL_BRIDGE=$(sha256sum "$BRIDGE" | awk '{print $1}')
[ "$ACTUAL_MAIN" = "$EXPECTED_MAIN" ] || { echo "main SHA-256 mismatch: $ACTUAL_MAIN" >&2; exit 1; }
[ "$ACTUAL_BRIDGE" = "$EXPECTED_BRIDGE" ] || { echo "bridge SHA-256 mismatch: $ACTUAL_BRIDGE" >&2; exit 1; }
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
unzip -p "$MAIN" xl/worksheets/sheet2.xml > "$TMP/main_sheet2.xml"
g++ -O3 -std=c++17 "$SCRIPT_DIR/extract_main.cpp" -o "$TMP/extract_main"
"$TMP/extract_main" "$TMP/main_sheet2.xml" "$OUT/all_invoices_selected.tsv"
python "$SCRIPT_DIR/extract_bridge_selected.py" "$BRIDGE" "$OUT/2024_selected.tsv"
echo "main_sha256=$ACTUAL_MAIN"
echo "bridge_sha256=$ACTUAL_BRIDGE"
wc -l "$OUT/all_invoices_selected.tsv" "$OUT/2024_selected.tsv"
