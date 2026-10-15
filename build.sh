#!/bin/bash
# Build a HiBy R1 firmware (.upt) from the source tree in src/.
#
#   src/       --(tools/repack.sh)-->   out/<name>-<ver>.upt
#   out/*.upt  --(tools/unpack.sh)-->   temp dir      (round-trip gate)
#
# Usage: ./build.sh
set -euo pipefail

cd "$(dirname "$0")"
source ./config.sh

for tool in 7z unsquashfs mksquashfs xorriso; do
    command -v "$tool" >/dev/null || { echo "Missing tool: $tool"; exit 1; }
done
[[ -f src/xImage && -d src/squashfs-root ]] || {
    echo "src/ incomplete: need src/xImage and src/squashfs-root/"; exit 1; }

mkdir -p out

OUT="out/${FW_NAME}-${FW_VERSION}.upt"
echo "== Packing src/ -> $OUT"
./tools/repack.sh src "$OUT"

echo "== Verifying round trip"
VERIFY=$(mktemp -d)
trap 'rm -rf "$VERIFY"' EXIT
./tools/unpack.sh "$OUT" "$VERIFY"

echo
echo "Built: $OUT ($(du -h "$OUT" | cut -f1))"
