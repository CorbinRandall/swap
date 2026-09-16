"""Preset library file operations (duplicate, remove, sync manifest)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .export import load_preset_file, write_preset_file
from .manifest import load_manifest, remove_manifest_entry, save_manifest
from .paths import archive_dir, default_presets_dir, ensure_toolkit_data_dirs, onboard_dir, profiles_dir, reference_dir


def _relative_manifest_key(folder: Path, path: Path) -> str:
    try:
        return str(path.relative_to(folder))
    except ValueError:
        return path.name


_SKIP_PARTS = frozenset({"onboard", "reference", "_archive"})


def _should_skip_library_path(path: Path, library: Path) -> bool:
    try:
        rel = path.relative_to(library)
    except ValueError:
        return False
    return any(part.startswith("_") or part in _SKIP_PARTS for part in rel.parts)


def scan_preset_files(library: Path) -> list[Path]:
    """User-visible presets only (excludes _system, onboard, _archive)."""
    return scan_user_preset_files(library)


def scan_user_preset_files(library: Path) -> list[Path]:
    """Presets the user manages — not the hidden G Hub factory default."""
    library = library.resolve()
    if not library.is_dir():
        return []
    found: list[Path] = []
    for path in sorted(library.rglob("*.lghub-preset.json")):
        if _should_skip_library_path(path, library):
            continue
        found.append(path)
    return found


def sync_manifest(folder: Path) -> int:
    """Rebuild manifest.json from preset files on disk."""
    from .export import load_preset_file

    folder = folder.resolve()
    manifest: dict[str, Any] = {"version": 1, "updatedAt": None, "presets": []}
    for path in scan_preset_files(folder):
        try:
            preset = load_preset_file(path)
        except Exception:
            continue
        source = "unknown"
        if path.parent.name == "onboard" or "onboard" in path.parts:
            source = "mouse-pull"
        elif "ghub_export" in path.name or preset.get("ommRaw") is None:
            source = "ghub-export"
        manifest["presets"].append(
            {
                "file": _relative_manifest_key(folder, path),
                "name": preset.get("name", "?"),
                "source": source,
            }
        )
    save_manifest(folder, manifest)
    return len(manifest["presets"])


def duplicate_preset(
    source: Path,
    *,
    new_name: str | None = None,
    output: Path | None = None,
) -> Path:
    preset = load_preset_file(source)
    base_name = new_name or f"{preset.get('name', 'preset')} copy"
    preset["name"] = base_name
    dest_dir = output.parent if output else source.parent
    dest_dir.mkdir(parents=True, exist_ok=True)
    if output:
        safe = output.name if output.suffix == ".json" else None
        if safe:
            path = output
            path.write_text(json.dumps(preset, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            return path
    return write_preset_file(dest_dir, preset)


def remove_preset(path: Path, library: Path | None = None) -> None:
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    library = (library or default_presets_dir()).resolve()
    key = _relative_manifest_key(library, path) if path.is_relative_to(library) else path.name
    path.unlink()
    if (library / "manifest.json").exists():
        remove_manifest_entry(library, key)


def organize_library(library: Path | None = None) -> list[str]:
    """Tidy preset folder: move tests to archive/, raw pulls to onboard/."""
    ensure_toolkit_data_dirs(library)
    library = (library or default_presets_dir()).resolve()
    onboard = onboard_dir(library)
    reference = reference_dir(library)
    archive = archive_dir(library)
    for d in (onboard, reference, archive):
        d.mkdir(parents=True, exist_ok=True)

    moves: list[str] = []

    def move(src: Path, dest: Path) -> None:
        if not src.exists() or src == dest:
            return
        if dest.exists():
            dest = archive / dest.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        moves.append(f"{src.name} -> {dest.relative_to(library)}")

    for path in sorted(library.glob("*.lghub-preset.json")):
        name = path.name
        if name.startswith("test_") or name.startswith("test"):
            move(path, archive / name)

    for path in sorted(library.glob("onboard_raw_slot*.json")):
        slot = path.stem.replace("onboard_raw_slot", "")
        move(path, onboard / f"slot{slot}.json")

    for path in sorted(library.glob("test_pull_slot*.json")):
        move(path, archive / path.name)

    for path in sorted(library.glob("ROSETTA_*.json")):
        move(path, reference / path.name)

    for path in sorted(library.glob("test_*.json")):
        if path.parent == library:
            move(path, archive / path.name)

    return moves
