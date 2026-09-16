"""Manifest tracking for preset library folder."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MANIFEST_NAME = "manifest.json"


def load_manifest(folder: Path) -> dict[str, Any]:
    path = folder / MANIFEST_NAME
    if not path.exists():
        return {"version": 1, "updatedAt": None, "presets": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(folder: Path, manifest: dict[str, Any]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    manifest["updatedAt"] = datetime.now(timezone.utc).isoformat()
    path = folder / MANIFEST_NAME
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def upsert_manifest_entry(folder: Path, filename: str, name: str, source: str) -> None:
    manifest = load_manifest(folder)
    presets = manifest.setdefault("presets", [])
    presets = [p for p in presets if p.get("file") != filename]
    presets.append(
        {
            "file": filename,
            "name": name,
            "source": source,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }
    )
    manifest["presets"] = presets
    save_manifest(folder, manifest)


def remove_manifest_entry(folder: Path, file_key: str) -> None:
    manifest = load_manifest(folder)
    manifest["presets"] = [p for p in manifest.get("presets", []) if p.get("file") != file_key]
    save_manifest(folder, manifest)


def list_manifest_presets(folder: Path) -> list[dict[str, Any]]:
    return load_manifest(folder).get("presets", [])
