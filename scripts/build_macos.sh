#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f assets/Swap.icns ]]; then
  TMP="$(mktemp -d)"
  ICONSET="$TMP/Swap.iconset"
  mkdir -p "$ICONSET"
  for size in 16 32 128 256 512; do
    sips -z "$size" "$size" assets/Swap.png --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
    sips -z $((size*2)) $((size*2)) assets/Swap.png --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
  done
  iconutil -c icns "$ICONSET" -o assets/Swap.icns
  rm -rf "$TMP"
fi

rm -rf build dist
python3 setup.py py2app
codesign --force --deep --sign - dist/Swap.app
echo "Built: $ROOT/dist/Swap.app"
