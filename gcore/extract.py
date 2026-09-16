"""Extract onboard slots into library presets with exact page snapshots."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ghub_presets.convert import omm_to_ghub_preset

from .device import hidpp_session
from .library import write_preset


def _safe_stem(name: str) -> str:
    safe = re.sub(r"[^\w\- ]+", "", name).strip().replace(" ", "_")
    return safe or "onboard_preset"


def capture_slot_snapshot(omm: Any, slot: int, *, device_key: str) -> dict[str, Any]:
    """Read raw memory pages for a slot into a gOnboardSnapshot dict."""
    omm.dest_profile = slot
    page_numbers = list(omm.page_layout[slot])
    pages_hex: list[str] = []
    for page_index, page_number in enumerate(page_numbers):
        raw = omm.read_memory_page(page_number, verify=(page_index == 0))
        pages_hex.append(bytes(raw).hex())
    return {
        "version": 1,
        "deviceKey": device_key,
        "sourceSlot": slot,
        "pageSize": int(omm.page_size),
        "pageNumbers": page_numbers,
        "pages": pages_hex,
        "capturedAt": datetime.now(timezone.utc).isoformat(),
    }


def extract_slot_to_library(
    slot: int,
    library_dir: Path,
    *,
    profile_name: str | None = None,
) -> Path:
    """Pull an onboard slot into the library with ommRaw + gOnboardSnapshot.

    Leaves the mouse in onboard mode (G daily-driver contract).
    """
    if slot not in (1, 2, 3, 4, 5):
        raise ValueError(f"Invalid slot {slot}")

    with hidpp_session(force_onboard=True) as session:
        omm = session.omm
        session.leave_mode = True
        if slot > omm.num_profiles:
            raise RuntimeError(f"Device only has {omm.num_profiles} slots")
        omm.dest_profile = slot
        if not omm.profile_enabled:
            raise RuntimeError(f"Onboard slot {slot} is disabled on the mouse")

        data = omm.onboard_profile_to_bin()
        omm_json = omm.profile_bin_to_json(data)
        name = (profile_name or str(omm_json.get("profile_name") or f"Slot {slot}")).strip()
        snapshot = capture_slot_snapshot(omm, slot, device_key=session.device_key)
        preset = omm_to_ghub_preset(
            omm_json,
            session.device,
            profile_name=name,
            slot=slot,
        )
        preset["gOnboardSnapshot"] = snapshot

    return write_preset(library_dir, preset, stem=_safe_stem(name))


def extract_slots_to_library(
    slots: tuple[int, ...],
    library_dir: Path,
) -> list[Path]:
    """Pull multiple onboard slots into the library in one HID++ session."""
    if not slots:
        return []

    paths: list[Path] = []
    with hidpp_session(force_onboard=True) as session:
        omm = session.omm
        session.leave_mode = True
        for slot in slots:
            if slot not in (1, 2, 3, 4, 5):
                raise ValueError(f"Invalid slot {slot}")
            if slot > omm.num_profiles:
                raise RuntimeError(f"Device only has {omm.num_profiles} slots")
            omm.dest_profile = slot
            if not omm.profile_enabled:
                raise RuntimeError(f"Onboard slot {slot} is disabled on the mouse")

            data = omm.onboard_profile_to_bin()
            omm_json = omm.profile_bin_to_json(data)
            name = str(omm_json.get("profile_name") or f"Slot {slot}").strip()
            snapshot = capture_slot_snapshot(omm, slot, device_key=session.device_key)
            preset = omm_to_ghub_preset(
                omm_json,
                session.device,
                profile_name=name,
                slot=slot,
            )
            preset["gOnboardSnapshot"] = snapshot
            paths.append(write_preset(library_dir, preset, stem=_safe_stem(name)))
    return paths
