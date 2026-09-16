#!/usr/bin/env bash
# Reuse G's existing identity. Never reset permissions or generate a new key.
set -euo pipefail
APP="${1:?Usage: sign_macos.sh /path/to/Swap.app}"
IDENTITY="${SWAP_SIGN_IDENTITY:-G Onboard Local}"
BUNDLE_ID="io.bytecode.g-onboard"
if ! security find-identity -v -p codesigning | grep -F "\"$IDENTITY\"" >/dev/null; then
  echo "Signing identity '$IDENTITY' is unavailable. Set SWAP_SIGN_IDENTITY to your signing identity." >&2
  exit 1
fi
codesign --force --deep --timestamp=none --sign "$IDENTITY" "$APP"
for exe in "$APP/Contents/MacOS"/*; do
  [[ -f "$exe" && -x "$exe" ]] || continue
  codesign --force --timestamp=none --identifier "$BUNDLE_ID" --sign "$IDENTITY" "$exe"
done
codesign --force --timestamp=none --identifier "$BUNDLE_ID" --sign "$IDENTITY" "$APP"
codesign --verify --deep --strict "$APP"
