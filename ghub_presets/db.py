"""Read/write Logitech G Hub settings.db."""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import archive_dir, ensure_toolkit_data_dirs, ghub_settings_db


def backup_settings_db(db_path: Path | None = None) -> Path:
    db_path = db_path or ghub_settings_db()
    if not db_path.exists():
        raise FileNotFoundError(f"settings.db not found: {db_path}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = db_path.with_name(f"settings.db.backup-{stamp}")
    shutil.copy2(db_path, backup)
    return backup


def backup_settings_to_archive(library: Path | None = None) -> Path:
    """Copy settings.db into Toolkit Data/archive/ before export/import/replace."""
    ensure_toolkit_data_dirs(library)
    db_path = ghub_settings_db()
    if not db_path.exists():
        raise FileNotFoundError(f"settings.db not found: {db_path}")

    dest_dir = archive_dir(library)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = dest_dir / f"settings.db.backup-{stamp}"
    shutil.copy2(db_path, backup)
    return backup


def _connect(db_path: Path) -> sqlite3.Connection:
    wal_path = Path(str(db_path) + "-wal")
    wal_mode = wal_path.exists()

    conn = sqlite3.connect(db_path)
    if wal_mode:
        conn.execute("PRAGMA journal_mode=WAL;")
    else:
        conn.execute("PRAGMA journal_mode=DELETE;")
    return conn


def prepare_settings_db(db_path: Path | None = None) -> None:
    """Merge WAL into the main DB so reads/writes see the latest data (G Hub must be quit)."""
    db_path = db_path or ghub_settings_db()
    if not db_path.exists():
        raise FileNotFoundError(f"settings.db not found: {db_path}")

    conn = _connect(db_path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        conn.commit()
    finally:
        conn.close()


def read_settings(db_path: Path | None = None) -> dict[str, Any]:
    db_path = db_path or ghub_settings_db()
    if not db_path.exists():
        raise FileNotFoundError(f"settings.db not found: {db_path}")

    prepare_settings_db(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT _id, file FROM data ORDER BY _id DESC LIMIT 1").fetchone()
        if not row:
            raise RuntimeError("No data row found in settings.db")
        blob = row[1]
        return json.loads(blob.decode("utf-8"))
    finally:
        conn.close()


def write_settings(settings: dict[str, Any], db_path: Path | None = None) -> None:
    db_path = db_path or ghub_settings_db()
    if not db_path.exists():
        raise FileNotFoundError(f"settings.db not found: {db_path}")

    payload = json.dumps(settings, indent=2, ensure_ascii=False).encode("utf-8")
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT _id FROM data ORDER BY _id DESC LIMIT 1").fetchone()
        if not row:
            raise RuntimeError("No data row found in settings.db")
        data_id = row[0]
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "REPLACE INTO data (_id, _date_created, file) VALUES (?, ?, ?)",
            (data_id, now, payload),
        )
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        conn.commit()
    finally:
        conn.close()

    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(db_path) + suffix)
        if sidecar.exists():
            sidecar.unlink()


def list_profiles(settings: dict[str, Any]) -> list[dict[str, Any]]:
    profiles = settings.get("profiles", {}).get("profiles", [])
    return list(profiles)


def get_desktop_application_id(settings: dict[str, Any]) -> str | None:
    apps = settings.get("applications", {}).get("applications", [])
    for app in apps:
        name = (app.get("name") or "").upper()
        if "DESKTOP" in name or app.get("category") == "DEFAULT":
            return app.get("applicationId")
    if apps:
        return apps[0].get("applicationId")

    profiles = list_profiles(settings)
    if profiles:
        return profiles[0].get("applicationId")
    return None


def detect_slot_prefix(settings: dict[str, Any]) -> str | None:
    for profile in list_profiles(settings):
        for assignment in profile.get("assignments", []):
            slot_id = assignment.get("slotId", "")
            if "_mouse_settings" in slot_id:
                return slot_id.split("_mouse_settings")[0] + "_"
            if "_g1_m1" in slot_id:
                return slot_id.split("_g1_m1")[0] + "_"
    known = settings.get("devices/known", {}).get("knownList", [])
    if known:
        model = known[0].get("modelId", "")
        from .devices import slot_prefix_for_model

        return slot_prefix_for_model(model)
    return None
