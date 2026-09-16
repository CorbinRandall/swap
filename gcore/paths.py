"""Resolve data root and library folder."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# gcore/paths.py → repo root (dev checkout)
_REPO_ROOT = Path(__file__).resolve().parents[1]


def _migrate_legacy_support(parent: Path, name: str = "G") -> Path:
    """Prefer G; pull back abandoned gg rename data if present."""
    target = parent / name
    legacy_gg = parent / "gg"
    if not target.exists() and legacy_gg.exists():
        legacy_gg.rename(target)
    return target


def application_support_root() -> Path:
    """Writable per-user data for a installed /Applications (or Program Files) copy."""
    if sys.platform == "darwin":
        return _migrate_legacy_support(Path.home() / "Library" / "Application Support")
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return _migrate_legacy_support(Path(base))
    return _migrate_legacy_support(Path.home() / ".local" / "share")


def _frozen_repo_candidate() -> Path | None:
    """When run from a repo dist/ build, prefer the checkout beside dist/."""
    if not getattr(sys, "frozen", False):
        return None
    # .../dist/G.app/Contents/MacOS/G
    exe = Path(sys.executable).resolve()
    try:
        app_bundle = exe.parents[2]  # G.app
        repo = app_bundle.parent.parent  # dist → repo
        if (repo / "Put Presets Here").is_dir() and (repo / "ghub_presets").is_dir():
            return repo
    except IndexError:
        pass
    return None


def _app_bundle_path() -> Path | None:
    """Return .app bundle when running inside G (Xcode or py2app)."""
    candidates: list[Path] = []
    if sys.argv:
        candidates.append(Path(sys.argv[0]).resolve())
    candidates.append(Path(__file__).resolve())
    for start in candidates:
        for parent in [start, *start.parents]:
            if parent.suffix == ".app":
                return parent
            if parent.name == "Contents" and parent.parent.suffix == ".app":
                return parent.parent
    return None


def _bundle_engine_root(app: Path) -> Path | None:
    for rel in (
        "Contents/Resources/EngineResources/Engine",
        "Contents/Resources/Engine",
    ):
        root = app / rel
        if (root / "gcore").is_dir():
            return root
    return None


def _seed_installed_data(support: Path, bundle_engine: Path | None) -> None:
    """Copy default presets into Application Support on first install."""
    import shutil

    presets = support / "Put Presets Here"
    presets.mkdir(parents=True, exist_ok=True)
    (support / "Toolkit Data" / "onboard-archive").mkdir(parents=True, exist_ok=True)
    (support / "Toolkit Data" / "onboard-snapshots").mkdir(parents=True, exist_ok=True)
    if bundle_engine is None:
        return
    src = bundle_engine / "Put Presets Here"
    if not src.is_dir():
        return
    for f in src.glob("*.lghub-preset.json"):
        dest = presets / f.name
        if not dest.is_file():
            shutil.copy2(f, dest)


def toolkit_root() -> Path:
    """Data / repo root (kept name for compatibility with older env vars)."""
    env = os.environ.get("GHUB_PRESET_TOOLKIT_ROOT") or os.environ.get("G_ONBOARD_ROOT")
    if env:
        path = Path(env).expanduser().resolve()
        # Installed app: env may point at bundle engine — use Application Support for data.
        if _app_bundle_path() and "EngineResources" in str(path):
            support = application_support_root()
            _seed_installed_data(support, path)
            return support
        return path
    frozen = _frozen_repo_candidate()
    if frozen is not None:
        return frozen
    app = _app_bundle_path()
    if app is not None:
        support = application_support_root()
        _seed_installed_data(support, _bundle_engine_root(app))
        support.mkdir(parents=True, exist_ok=True)
        (support / "Put Presets Here").mkdir(parents=True, exist_ok=True)
        (support / "Toolkit Data" / "onboard-archive").mkdir(parents=True, exist_ok=True)
        return support
    if getattr(sys, "frozen", False):
        # py2app installed app (e.g. /Applications/G.app) — use Application Support.
        root = application_support_root()
        root.mkdir(parents=True, exist_ok=True)
        (root / "Put Presets Here").mkdir(parents=True, exist_ok=True)
        (root / "Toolkit Data" / "onboard-archive").mkdir(parents=True, exist_ok=True)
        return root
    return _REPO_ROOT


def g_app_root() -> Path:
    return toolkit_root()


def default_presets_dir() -> Path:
    """Built-in library folder under the data root (ignores presets.dir / env)."""
    path = toolkit_root() / "Put Presets Here"
    path.mkdir(parents=True, exist_ok=True)
    return path


def presets_dir_file() -> Path:
    return toolkit_root() / "presets.dir"


def presets_dir() -> Path:
    """Library of *.lghub-preset.json files.

    Resolution order:
      1. GHUB_PRESETS_DIR env
      2. presets.dir file in the data root (one path per line)
      3. Put Presets Here/ under the data root
    """
    env = os.environ.get("GHUB_PRESETS_DIR")
    if env:
        return Path(env).expanduser().resolve()
    link = presets_dir_file()
    if link.is_file():
        try:
            target = Path(link.read_text(encoding="utf-8").strip().splitlines()[0]).expanduser()
            if target.is_dir():
                return target.resolve()
        except (OSError, IndexError, ValueError):
            pass
    return default_presets_dir()


def set_presets_dir(folder: Path | str) -> Path:
    """Persist a custom library folder via presets.dir; return the resolved path."""
    path = Path(folder).expanduser().resolve()
    if not path.is_dir():
        raise FileNotFoundError(f"Presets folder does not exist: {path}")
    link = presets_dir_file()
    link.parent.mkdir(parents=True, exist_ok=True)
    link.write_text(str(path) + "\n", encoding="utf-8")
    return path


def clear_presets_dir() -> Path:
    """Remove presets.dir override and return the default Put Presets Here path."""
    link = presets_dir_file()
    if link.is_file():
        try:
            link.unlink()
        except OSError:
            pass
    return default_presets_dir()


def extracted_presets_dir(library: Path | None = None) -> Path:
    """Subfolder for Extract All output (kept out of Replace All assignment)."""
    root = Path(library) if library is not None else presets_dir()
    path = root / "Extracted"
    path.mkdir(parents=True, exist_ok=True)
    return path


def active_state_file() -> Path:
    """Persisted logical active slot + sticky names for Lightspeed overlay."""
    return toolkit_root() / "active-state.json"


def open_in_file_manager(path: Path | str) -> None:
    """Reveal a folder (or file's parent) in Finder / Explorer / xdg-open."""
    target = Path(path).expanduser().resolve()
    if target.is_file():
        target = target.parent
    target.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        subprocess.run(["open", str(target)], check=False)
    elif sys.platform == "win32":
        os.startfile(str(target))  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", str(target)], check=False)


def truncate_path(path: Path | str, max_len: int = 42) -> str:
    """Short display form for menu labels."""
    text = str(path)
    if len(text) <= max_len:
        return text
    return "…" + text[-(max_len - 1) :]


def archive_dir() -> Path:
    path = toolkit_root() / "Toolkit Data" / "onboard-archive"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_toolkit_on_path() -> Path:
    """Make this repo importable as ghub_presets / gcore / gui (dev only)."""
    root = _REPO_ROOT
    s = str(root)
    if s not in sys.path:
        sys.path.insert(0, s)
    return toolkit_root()
