"""Shared update-blocker constants and hosts-file helpers."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import archive_dir, default_presets_dir

MAC_STATE_DIR = Path("/Library/Application Support/ghub-presets-toolkit")

STATE_FILENAME = "ghub-update-block.json"
HOSTS_MARKER = "# GHub Preset Toolkit update block"

# Hosts that MUST stay reachable. G Hub downloads device support packages
# (e.g. g502.depot) from the updates CDN / pipeline. Blocking these makes
# devices show as Inactive after a G Hub upgrade until depots can refresh.
DEVICE_REQUIRED_HOSTS: tuple[str, ...] = (
    "updates.ghub.logitechg.com",
    "pipeline.logitech.io",
    "2pipeline.s3.amazonaws.com",
)

# Safe-to-block staging / telemetry endpoints only. Do not put device-depot
# hosts in this list.
UPDATE_HOSTS: tuple[str, ...] = (
    "stg-pipeline.np.logitech.io",
    "stg-datapipeline.np.logitech.io",
    "datapipeline.logitech.io",
)


def _legacy_state_file(library: Path | None = None) -> Path:
    root = library or default_presets_dir()
    return archive_dir(root) / STATE_FILENAME


def state_file(library: Path | None = None) -> Path:
    if sys.platform == "darwin":
        system = MAC_STATE_DIR / STATE_FILENAME
        legacy = _legacy_state_file(library)
        if system.is_file():
            return system
        if legacy.is_file():
            return legacy
        system.parent.mkdir(parents=True, exist_ok=True)
        return system

    path = _legacy_state_file(library)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_state(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_state(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def list_hosts_entries(hosts_file: Path) -> list[str]:
    """Return every host the toolkit marked in /etc/hosts (including legacy risky ones)."""
    if not hosts_file.is_file():
        return []
    active: list[str] = []
    for line in hosts_file.read_text(encoding="utf-8", errors="replace").splitlines():
        if HOSTS_MARKER not in line:
            continue
        parts = line.split()
        if len(parts) >= 2:
            active.append(parts[1])
    return sorted(set(active))


def list_risky_hosts_entries(hosts_file: Path) -> list[str]:
    """Toolkit hosts blocks that can break device activation."""
    return [h for h in list_hosts_entries(hosts_file) if h in DEVICE_REQUIRED_HOSTS]


def add_hosts_entries(hosts_file: Path) -> list[str]:
    if not hosts_file.is_file():
        raise RuntimeError(f"Hosts file not found: {hosts_file}")

    lines = hosts_file.read_text(encoding="utf-8", errors="replace").splitlines()
    existing = set(list_hosts_entries(hosts_file))
    actions: list[str] = []
    for host in UPDATE_HOSTS:
        if host in existing:
            continue
        lines.append(f"127.0.0.1 {host} {HOSTS_MARKER}")
        actions.append(f"hosts block {host}")

    if actions:
        text = "\n".join(lines).rstrip() + "\n"
        hosts_file.write_text(text, encoding="utf-8")
    return actions


def remove_hosts_entries(hosts_file: Path) -> list[str]:
    if not hosts_file.is_file():
        return []

    actions: list[str] = []
    kept: list[str] = []
    for line in hosts_file.read_text(encoding="utf-8", errors="replace").splitlines():
        if HOSTS_MARKER in line:
            parts = line.split()
            if len(parts) >= 2:
                actions.append(f"hosts remove {parts[1]}")
            continue
        kept.append(line)

    if actions:
        text = "\n".join(kept).rstrip()
        if text:
            text += "\n"
        hosts_file.write_text(text, encoding="utf-8")
    return actions


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
