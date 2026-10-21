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

# Stage old outputs outside the checkout instead of deleting release artifacts.
for folder in build dist; do
  if [[ -e "$folder" ]]; then
    backup="$(mktemp -d "${TMPDIR:-/tmp}/swap-build.XXXXXX")"
    mv "$folder" "$backup/"
  fi
done
# py2app rejects pyproject [project].dependencies ("install_requires is no
# longer supported"), so hide pyproject.toml while it runs and always restore it.
PYPROJECT_STASH="$(mktemp -d)"
mv pyproject.toml "$PYPROJECT_STASH/"
trap 'mv "$PYPROJECT_STASH/pyproject.toml" "$ROOT/pyproject.toml"' EXIT
"${SWAP_PYTHON:-python3}" setup.py py2app
mv "$PYPROJECT_STASH/pyproject.toml" "$ROOT/pyproject.toml"
trap - EXIT
"$ROOT/scripts/sign_macos.sh" "$ROOT/dist/Swap.app"
echo "Built: $ROOT/dist/Swap.app"
