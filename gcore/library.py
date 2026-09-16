"""Preset library backed by Put Presets Here/ (or a user-chosen folder)."""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .paths import presets_dir

_PRESET_SUFFIX = ".lghub-preset.json"


@dataclass(frozen=True)
class LibraryPreset:
    name: str
    path: Path


def scan_library(folder: Path | None = None) -> list[LibraryPreset]:
    root = folder or presets_dir()
    if not root.is_dir():
        return []
    out: list[LibraryPreset] = []
    for path in sorted(root.glob(f"*{_PRESET_SUFFIX}")):
        name = path.name[: -len(_PRESET_SUFFIX)].replace("_", " ")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            name = str(data.get("name") or name)
        except (OSError, json.JSONDecodeError, TypeError):
            pass
        out.append(LibraryPreset(name=name, path=path.resolve()))
    return out


def load_preset(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_stem(name: str) -> str:
    safe = re.sub(r"[^\w\- ]+", "", name).strip().replace(" ", "_")
    return safe or "preset"


def _unique_path(folder: Path, stem: str) -> Path:
    candidate = folder / f"{stem}{_PRESET_SUFFIX}"
    if not candidate.exists():
        return candidate
    n = 2
    while True:
        candidate = folder / f"{stem}_{n}{_PRESET_SUFFIX}"
        if not candidate.exists():
            return candidate
        n += 1


def write_preset(folder: Path, preset: dict, *, stem: str | None = None) -> Path:
    """Write a preset JSON into folder; returns the new path."""
    folder.mkdir(parents=True, exist_ok=True)
    name = str(preset.get("name") or stem or "preset")
    path = _unique_path(folder, _safe_stem(stem or name))
    path.write_text(json.dumps(preset, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path.resolve()


def duplicate_preset(source: Path, *, new_name: str | None = None) -> Path:
    """Copy a library preset beside the original with a new display name."""
    source = Path(source).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    preset = load_preset(source)
    base = new_name or f"{preset.get('name', source.stem)} copy"
    preset["name"] = base
    return write_preset(source.parent, preset, stem=_safe_stem(base))


def remove_preset(path: Path) -> None:
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    if not path.name.endswith(_PRESET_SUFFIX):
        raise ValueError(f"Not a library preset file: {path.name}")
    path.unlink()


def rename_preset(path: Path, new_name: str) -> Path:
    """Update display name and rename the file stem to match."""
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    preset = load_preset(path)
    preset["name"] = new_name.strip() or preset.get("name") or "preset"
    dest = _unique_path(path.parent, _safe_stem(str(preset["name"])))
    dest.write_text(json.dumps(preset, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if dest.resolve() != path.resolve():
        path.unlink()
    return dest.resolve()


class LibraryWatcher:
    """Watch presets folder; call on_change when JSON files change."""

    def __init__(self, folder: Path | None = None, on_change: Optional[Callable[[], None]] = None):
        self.folder = folder or presets_dir()
        self.on_change = on_change
        self._observer = None
        self._lock = threading.Lock()

    def start(self) -> None:
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            return

        folder = self.folder
        folder.mkdir(parents=True, exist_ok=True)
        callback = self.on_change

        class Handler(FileSystemEventHandler):
            def on_any_event(self, event):  # type: ignore[no-untyped-def]
                if event.is_directory:
                    return
                src = str(getattr(event, "src_path", "") or "")
                if not src.endswith(_PRESET_SUFFIX):
                    return
                if callback:
                    callback()

        observer = Observer()
        observer.schedule(Handler(), str(folder), recursive=False)
        observer.daemon = True
        observer.start()
        with self._lock:
            self._observer = observer

    def stop(self) -> None:
        with self._lock:
            obs = self._observer
            self._observer = None
        if obs is not None:
            obs.stop()
            obs.join(timeout=2)

    def set_folder(self, folder: Path) -> None:
        """Point the watcher at a new library folder (restart if running)."""
        running = False
        with self._lock:
            running = self._observer is not None
        if running:
            self.stop()
        self.folder = Path(folder).resolve()
        if running:
            self.start()
