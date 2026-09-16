#!/usr/bin/env bash
# Install one app, keeping a recoverable archive of the previous installation.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="${1:-$ROOT/dist/Swap.app}"
[[ -d "$APP" ]] || { echo "Build Swap first: ./scripts/build_macos.sh" >&2; exit 1; }
codesign --verify --deep --strict "$APP"
BACKUPS="$HOME/Library/Application Support/G/app-backups"
mkdir -p "$BACKUPS"
STAMP="$(date +%Y%m%d-%H%M%S)"
# Quit via the app's normal handler. Never kill unrelated Python processes.
for name in G Swap; do
  if pgrep -x "$name" >/dev/null; then
    osascript -e "tell application \"$name\" to quit"
  fi
done
for name in G Swap; do
  old="/Applications/$name.app"
  if [[ -d "$old" ]]; then
    ditto -c -k --sequesterRsrc --keepParent "$old" "$BACKUPS/$name-$STAMP.zip"
    # Only remove the exact app after its backup has been verified.
    unzip -tq "$BACKUPS/$name-$STAMP.zip" >/dev/null
    rm -rf "$old"
  fi
done
ditto "$APP" /Applications/Swap.app
codesign --verify --deep --strict /Applications/Swap.app
if [[ "$APP" == "$ROOT/dist/Swap.app" ]]; then
  ditto -c -k --sequesterRsrc --keepParent "$APP" "$ROOT/dist/Swap-macOS.zip"
  unzip -tq "$ROOT/dist/Swap-macOS.zip" >/dev/null
  rm -rf "$ROOT/dist/Swap.app"
fi
echo "Installed /Applications/Swap.app. Previous apps archived in $BACKUPS"
echo "Open Swap from Applications."
