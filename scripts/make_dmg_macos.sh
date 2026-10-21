#!/usr/bin/env bash
# Package dist/Swap.app into a drag-to-Applications disk image.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/dist/Swap.app"
OUT="${1:-$ROOT/dist/Swap-macOS.dmg}"
[[ -d "$APP" ]] || { echo "Build Swap first: ./scripts/build_macos.sh" >&2; exit 1; }
STAGE="$(mktemp -d)"
ditto "$APP" "$STAGE/Swap.app"
ln -s /Applications "$STAGE/Applications"
rm -f "$OUT"
hdiutil create -volname "Swap" -srcfolder "$STAGE" -ov -format UDZO "$OUT"
rm -rf "$STAGE"
echo "Built: $OUT"
