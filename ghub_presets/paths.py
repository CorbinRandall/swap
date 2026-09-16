"""Cross-platform path detection for G Hub and preset toolkit."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# User-facing preset library folder at toolkit root (empty in git; fill via Export).
PRESETS_DIR_NAME = "Put Presets Here"
LEGACY_PRESETS_DIR_NAME = "Presets"

# Toolkit-managed data (system profile, raw pulls, DB backups) — not user presets.
TOOLKIT_DATA_DIR_NAME = "Toolkit Data"


def toolkit_root() -> Path | None:
    """Repo / toolkit root when launched from Executables (set by wrapper scripts)."""
    env = os.environ.get("GHUB_PRESET_TOOLKIT_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    # Running from repo: parent of ghub_presets package
    here = Path(__file__).resolve().parent.parent
    if (
        (here / PRESETS_DIR_NAME).is_dir()
        or (here / LEGACY_PRESETS_DIR_NAME).is_dir()
        or (here / TOOLKIT_DATA_DIR_NAME).is_dir()
        or (here / "Executables").is_dir()
    ):
        return here
    return None


def _toolkit_root_for_library(library: Path | None) -> Path | None:
    root = toolkit_root()
    if root:
        return root
    if library is None:
        return None
    lib = library.resolve()
    if lib.name in (PRESETS_DIR_NAME, LEGACY_PRESETS_DIR_NAME):
        candidate = lib.parent
        if (candidate / "Executables").is_dir():
            return candidate
    parent = lib.parent
    if (parent / "Executables").is_dir():
        return parent
    return None


def presets_folder_in_root(root: Path) -> Path:
    """Resolve preset library folder; prefer new name, fall back to legacy Presets/."""
    for name in (PRESETS_DIR_NAME, LEGACY_PRESETS_DIR_NAME):
        path = root / name
        if path.is_dir():
            return path
    return root / PRESETS_DIR_NAME


def ghub_settings_dir() -> Path:
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            raise RuntimeError("LOCALAPPDATA is not set")
        return Path(local) / "LGHUB"
    return Path.home() / "Library" / "Application Support" / "lghub"


def ghub_settings_db() -> Path:
    return ghub_settings_dir() / "settings.db"


def default_presets_dir() -> Path:
    """Folder of .lghub-preset.json files to import (toolkit: ./Put Presets Here)."""
    env = os.environ.get("GHUB_PRESETS_DIR")
    if env:
        return Path(env).expanduser().resolve()
    root = toolkit_root()
    if root:
        return presets_folder_in_root(root)
    return Path.home() / "LogitechPresets"


def presets_dir(library: Path | None = None) -> Path:
    """Same as default_presets_dir — flat import/export folder."""
    return library or default_presets_dir()


def toolkit_data_dir(library: Path | None = None) -> Path:
    """Root for toolkit-managed folders (system, onboard, archive)."""
    root = _toolkit_root_for_library(library)
    if root:
        return root / TOOLKIT_DATA_DIR_NAME
    return presets_dir(library)


def _legacy_onboard_dir(library: Path | None) -> Path:
    return presets_dir(library) / "onboard"


def _legacy_archive_dir(library: Path | None) -> Path:
    return presets_dir(library) / "_archive"


def _legacy_reference_dir(library: Path | None) -> Path:
    return presets_dir(library) / "reference"


def _legacy_system_dir(library: Path | None) -> Path:
    return presets_dir(library) / "_system"


def system_dir(library: Path | None = None) -> Path:
    """Logitech factory default backup (DONT_TOUCH_SYSTEM)."""
    root = _toolkit_root_for_library(library)
    if root:
        return root / TOOLKIT_DATA_DIR_NAME / "system"
    return _legacy_system_dir(library)


def onboard_dir(library: Path | None = None) -> Path:
    """Raw mouse pulls (not imported into G Hub)."""
    root = _toolkit_root_for_library(library)
    if root:
        return root / TOOLKIT_DATA_DIR_NAME / "onboard"
    return _legacy_onboard_dir(library)


def archive_dir(library: Path | None = None) -> Path:
    """settings.db backups and update-block undo metadata."""
    root = _toolkit_root_for_library(library)
    if root:
        return root / TOOLKIT_DATA_DIR_NAME / "archive"
    return _legacy_archive_dir(library)


def reference_dir(library: Path | None = None) -> Path:
    root = _toolkit_root_for_library(library)
    if root:
        return root / TOOLKIT_DATA_DIR_NAME / "reference"
    return _legacy_reference_dir(library)


# Legacy names used elsewhere in the codebase
def profiles_dir(library: Path | None = None) -> Path:
    return presets_dir(library)


def _merge_dir_contents(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dest / item.name
        if item.is_dir():
            _merge_dir_contents(item, target)
            try:
                item.rmdir()
            except OSError:
                pass
        elif not target.exists():
            shutil.move(str(item), str(target))
        else:
            pass
    try:
        src.rmdir()
    except OSError:
        pass


def migrate_toolkit_data_layout(library: Path | None = None) -> list[str]:
    """Move system/onboard/archive out of the presets folder into Toolkit Data/."""
    lib = presets_dir(library)
    root = _toolkit_root_for_library(library)
    if not root:
        return []

    data = root / TOOLKIT_DATA_DIR_NAME
    moves: list[str] = []
    pairs = (
        (lib / "_system", data / "system"),
        (lib / "onboard", data / "onboard"),
        (lib / "_archive", data / "archive"),
        (lib / "reference", data / "reference"),
    )
    for src, dest in pairs:
        if not src.is_dir():
            continue
        if not any(src.iterdir()):
            try:
                src.rmdir()
            except OSError:
                pass
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            shutil.move(str(src), str(dest))
            moves.append(f"{src.relative_to(root)} -> {dest.relative_to(root)}")
        else:
            _merge_dir_contents(src, dest)
            moves.append(f"merged {src.relative_to(root)} -> {dest.relative_to(root)}")
    return moves


def ensure_toolkit_data_dirs(library: Path | None = None) -> None:
    """Create Toolkit Data layout and migrate legacy paths under the presets folder."""
    migrate_toolkit_data_layout(library)
    for path in (
        system_dir(library),
        onboard_dir(library),
        archive_dir(library),
        reference_dir(library),
    ):
        path.mkdir(parents=True, exist_ok=True)
