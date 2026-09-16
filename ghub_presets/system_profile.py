"""G Hub built-in default profile (do not delete from the toolkit)."""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path
from typing import Any

from .paths import TOOLKIT_DATA_DIR_NAME, ensure_toolkit_data_dirs, system_dir
from .preset_format import FORMAT_VERSION

# Shown in G Hub and on disk — makes it obvious not to delete.
SYSTEM_PROFILE_LABEL = "DONT_TOUCH_SYSTEM"
SYSTEM_PROFILE_FILENAME = f"{SYSTEM_PROFILE_LABEL}.lghub-preset.json"

# Logitech's original internal name (still recognized on import/export).
LEGACY_SYSTEM_PROFILE_NAME = "PROFILE_NAME_DEFAULT"
LEGACY_SYSTEM_PROFILE_FILENAME = f"{LEGACY_SYSTEM_PROFILE_NAME}.lghub-preset.json"

_LEGACY_NAMES = frozenset({LEGACY_SYSTEM_PROFILE_NAME, SYSTEM_PROFILE_LABEL})


def is_system_profile_name(name: str | None) -> bool:
    n = (name or "").strip()
    if n in _LEGACY_NAMES:
        return True
    return n.upper().startswith("DONT_TOUCH")


def is_system_preset_path(path: Path) -> bool:
    if path.suffix != ".json":
        return False
    parent = path.parent
    if parent.name == "_system":
        return True
    return parent.name == "system" and parent.parent.name == TOOLKIT_DATA_DIR_NAME


def system_presets_dir(library: Path | None = None) -> Path:
    return system_dir(library)


def bundled_system_profile_path() -> Path:
    return Path(__file__).resolve().parent / "bundled" / SYSTEM_PROFILE_FILENAME


def normalize_system_preset(preset: dict[str, Any]) -> dict[str, Any]:
    """Use the friendly label in exports while keeping profile data intact."""
    out = copy.deepcopy(preset)
    out["name"] = SYSTEM_PROFILE_LABEL
    if "profile" in out and isinstance(out["profile"], dict):
        out["profile"]["name"] = SYSTEM_PROFILE_LABEL
    return out


def _write_system_preset(path: Path, preset: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(normalize_system_preset(preset), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _migrate_legacy_system_file(dest_dir: Path) -> Path | None:
    legacy = dest_dir / LEGACY_SYSTEM_PROFILE_FILENAME
    if not legacy.exists():
        return None
    preset = json.loads(legacy.read_text(encoding="utf-8"))
    dest = dest_dir / SYSTEM_PROFILE_FILENAME
    _write_system_preset(dest, preset)
    legacy.unlink()
    return dest


def ensure_system_profile_file(library: Path | None = None) -> Path:
    """Ensure Toolkit Data/system/DONT_TOUCH_SYSTEM.lghub-preset.json exists."""
    ensure_toolkit_data_dirs(library)
    dest_dir = system_presets_dir(library)
    migrated = _migrate_legacy_system_file(dest_dir)
    if migrated:
        return migrated

    dest = dest_dir / SYSTEM_PROFILE_FILENAME
    if dest.exists():
        data = json.loads(dest.read_text(encoding="utf-8"))
        if data.get("name") != SYSTEM_PROFILE_LABEL:
            _write_system_preset(dest, data)
        return dest

    bundled = bundled_system_profile_path()
    if not bundled.exists():
        legacy_bundled = bundled.parent / LEGACY_SYSTEM_PROFILE_FILENAME
        if legacy_bundled.exists():
            preset = json.loads(legacy_bundled.read_text(encoding="utf-8"))
            _write_system_preset(dest, preset)
            return dest
        raise FileNotFoundError(
            f"Missing bundled system profile: {bundled}. Reinstall the toolkit."
        )
    shutil.copy2(bundled, dest)
    data = json.loads(dest.read_text(encoding="utf-8"))
    if data.get("name") != SYSTEM_PROFILE_LABEL:
        _write_system_preset(dest, data)
    return dest


def collect_user_import_paths(library: Path) -> list[Path]:
    """User presets in Presets/ only (excludes _system factory backup)."""
    from .library import scan_user_preset_files

    return scan_user_preset_files(library)


def collect_import_paths(library: Path) -> list[Path]:
    """Alias for user import paths — system profile is restored separately."""
    return collect_user_import_paths(library)


def preset_for_ghub_system_restore(preset: dict[str, Any]) -> dict[str, Any]:
    """Use G Hub's internal default name so the profile stays hidden in the UI."""
    out = copy.deepcopy(preset)
    out["name"] = LEGACY_SYSTEM_PROFILE_NAME
    if "profile" in out and isinstance(out["profile"], dict):
        out["profile"]["name"] = LEGACY_SYSTEM_PROFILE_NAME
    return out


def ghub_system_profile_keep_names() -> set[str]:
    """Profile names that must survive Replace in settings.db (G Hub internal names)."""
    return {LEGACY_SYSTEM_PROFILE_NAME}


def read_preset_name(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("format") != FORMAT_VERSION:
        raise ValueError(f"Unsupported preset format in {path}")
    return data.get("name") or path.stem


def user_profile_names_from_paths(paths: list[Path]) -> set[str]:
    names: set[str] = set()
    for path in paths:
        if is_system_preset_path(path):
            continue
        names.add(read_preset_name(path))
    return names
