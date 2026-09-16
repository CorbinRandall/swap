"""macOS 26 (Tahoe) menu-bar allow-list helpers.

StatusKit can hide G when a *disabled* app's trackedApplications entry
lists G under menuItemLocations (often Cursor, if G was launched from it).
G's own isAllowed=true is not enough in that case — Settings → toggle G
also does nothing. See CodexBar #1440 / #1945.
"""

from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

BUNDLE_ID = "io.bytecode.g-onboard"
# Abandoned rename leftovers — strip if present
LEGACY_BUNDLE_IDS = frozenset({"io.bytecode.gg"})
OUR_IDS = frozenset({BUNDLE_ID}) | LEGACY_BUNDLE_IDS

_CC_PLIST = (
    Path.home()
    / "Library/Group Containers/group.com.apple.controlcenter"
    / "Library/Preferences/group.com.apple.controlcenter.plist"
)


def _stop_control_center() -> None:
    # Only Control Center — never kill cfprefsd from the app (causes system-wide lag).
    subprocess.run(["killall", "ControlCenter"], check=False)


def fix_statuskit_orphans(*, stop_control_center: bool = True) -> bool:
    """Remove our bundle IDs from other apps' menuItemLocations; allow G.

    Must stop Control Center before writing — otherwise it rewrites the orphan.
    Returns True if the plist was changed.
    """
    if not _CC_PLIST.exists():
        return False
    if stop_control_center:
        _stop_control_center()

    try:
        with _CC_PLIST.open("rb") as handle:
            data = plistlib.load(handle)
        raw = data.get("trackedApplications")
        if not raw:
            return False
        tracked = plistlib.loads(raw) if isinstance(raw, bytes) else raw
        if not isinstance(tracked, list):
            return False

        changed = False
        kept: list = []
        for index in range(0, len(tracked) - 1, 2):
            key = tracked[index]
            value = tracked[index + 1]
            if not isinstance(value, dict):
                kept.extend([key, value])
                continue
            owner = key.get("bundle", {}).get("_0") if isinstance(key, dict) else None
            locations = value.get("menuItemLocations") or []

            # Drop abandoned gg entry entirely
            if owner in LEGACY_BUNDLE_IDS:
                changed = True
                continue

            if owner == BUNDLE_ID:
                if value.get("isAllowed") is not True:
                    value["isAllowed"] = True
                    changed = True
                value["menuItemLocations"] = [{"bundle": {"_0": BUNDLE_ID}}]
                kept.extend([key, value])
                continue

            filtered = [
                entry
                for entry in locations
                if entry.get("bundle", {}).get("_0") not in OUR_IDS
            ]
            if len(filtered) != len(locations):
                value["menuItemLocations"] = filtered
                changed = True
            kept.extend([key, value])

        if not changed:
            return False
        data["trackedApplications"] = plistlib.dumps(kept)
        with _CC_PLIST.open("wb") as handle:
            plistlib.dump(data, handle)
        return True
    except OSError:
        return False


def force_status_item_visible(status_item) -> None:
    """Clear persisted hide state and show the item once (no Control Center loops)."""
    try:
        from Foundation import NSUserDefaults
    except ImportError:
        return
    defaults = NSUserDefaults.standardUserDefaults()
    for key in (
        f"NSStatusItem Visible {BUNDLE_ID}",
        f"NSStatusItem VisibleCC {BUNDLE_ID}",
        "NSStatusItem Visible Item-0",
        "NSStatusItem VisibleCC Item-0",
    ):
        defaults.removeObjectForKey_(key)
    defaults.synchronize()
    try:
        status_item.setAutosaveName_(BUNDLE_ID)
    except Exception:
        pass
    try:
        status_item.setVisible_(True)
    except Exception:
        pass


def launch_g_via_finder() -> None:
    """Launch G through Finder so StatusKit does not attribute it to Cursor/terminals."""
    subprocess.run(
        [
            "osascript",
            "-e",
            'tell application "Finder" to open application file "G.app" '
            'of folder "Applications" of startup disk',
        ],
        check=False,
    )
