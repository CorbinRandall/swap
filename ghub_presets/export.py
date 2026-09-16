"""Export G Hub profiles to portable preset files."""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import detect_slot_prefix, get_desktop_application_id, list_profiles, read_settings
from .devices import slot_prefix_for_model
from .preset_format import FORMAT_VERSION
from .paths import presets_dir
from .builtin_cards import materialize_builtin_cards
from .rosetta import describe_builtin_suffix, is_builtin_preset_id, suffix as builtin_suffix
from .system_profile import (
    is_system_profile_name,
    normalize_system_preset,
    system_presets_dir,
    SYSTEM_PROFILE_FILENAME,
)


def _cards_index(settings: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["id"]: c for c in settings.get("cards", {}).get("cards", []) if "id" in c}


def _collect_card_ids(profile: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for assignment in profile.get("assignments", []):
        cid = assignment.get("cardId")
        if cid:
            ids.add(cid)
    lighting = profile.get("lightingCard")
    if lighting:
        ids.add(lighting)
    return ids


def _describe_card(
    card: dict[str, Any] | None,
    card_id: str,
    *,
    slot_id: str | None = None,
) -> str:
    if not card and is_builtin_preset_id(card_id):
        return describe_builtin_suffix(builtin_suffix(card_id), slot_id=slot_id)

    if not card:
        suf = builtin_suffix(card_id)
        if suf == "090700000000":
            return "Disabled / G-Shift default"
        return f"Built-in preset {suf}"

    name = card.get("name")
    if name and not name.startswith("DEFAULT_CARD_NAME"):
        return name
    macro = card.get("macro", {})
    if macro.get("actionName"):
        return macro["actionName"]
    if macro.get("type"):
        return macro["type"]
    return card.get("attribute", card_id)


def _build_readable(profile: dict[str, Any], cards: dict[str, dict[str, Any]]) -> dict[str, Any]:
    buttons = []
    dpi = None
    for assignment in profile.get("assignments", []):
        slot = assignment.get("slotId", "")
        cid = assignment.get("cardId", "")
        card = cards.get(cid)
        entry = {
            "slot": slot,
            "cardId": cid,
            "action": _describe_card(card, cid, slot_id=slot),
        }
        buttons.append(entry)
        if card and card.get("attribute") == "MOUSE_SETTINGS":
            dpi = card.get("mouseSettings")
    return {"buttons": buttons, "dpi": dpi}


def _infer_source_device(settings: dict[str, Any], profile: dict[str, Any]) -> tuple[str | None, str | None]:
    prefix = detect_slot_prefix(settings)
    if prefix:
        for model_id, model_prefix in (
            ("g502_spectrum", "g502spectrum_"),
            ("g502wireless", "g502wireless_"),
        ):
            if prefix == model_prefix:
                return model_id, prefix

    for assignment in profile.get("assignments", []):
        slot_id = assignment.get("slotId", "")
        if slot_id.startswith("g502spectrum_"):
            return "g502_spectrum", "g502spectrum_"
        if slot_id.startswith("g502wireless_"):
            return "g502wireless", "g502wireless_"

    known = settings.get("devices/known", {}).get("knownList", [])
    if known:
        model_id = known[0].get("modelId")
        return model_id, slot_prefix_for_model(model_id or "")
    return None, prefix


def profile_to_preset(settings: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    cards_map = _cards_index(settings)
    card_ids = _collect_card_ids(profile)
    cards = [copy.deepcopy(cards_map[cid]) for cid in card_ids if cid in cards_map]
    source_device, slot_prefix = _infer_source_device(settings, profile)

    preset = {
        "format": FORMAT_VERSION,
        "name": profile.get("name", "Unnamed"),
        "sourceDevice": source_device,
        "slotPrefix": slot_prefix,
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "profile": copy.deepcopy(profile),
        "cards": cards,
        "ommRaw": None,
        "readable": _build_readable(profile, cards_map),
    }
    materialize_builtin_cards(preset)
    return preset


def export_profile_by_name(
    name: str,
    folder: Path,
    *,
    db_path: Path | None = None,
) -> Path:
    settings = read_settings(db_path)
    for profile in list_profiles(settings):
        if profile.get("name") == name:
            return write_preset_file(folder, profile_to_preset(settings, profile))
    names = [p.get("name", "?") for p in list_profiles(settings)]
    raise ValueError(f"Profile {name!r} not found. Available: {', '.join(names)}")


def export_all_profiles(folder: Path, *, db_path: Path | None = None) -> list[Path]:
    settings = read_settings(db_path)
    desktop_id = get_desktop_application_id(settings)
    paths: list[Path] = []
    system_written = False
    for profile in list_profiles(settings):
        preset = profile_to_preset(settings, profile)
        name = profile.get("name", "")
        if is_system_profile_name(name):
            if system_written:
                continue
            app_id = profile.get("applicationId")
            prof_id = profile.get("id")
            if desktop_id and app_id != desktop_id and prof_id != desktop_id:
                continue
            system_written = True
            out_dir = system_presets_dir(folder)
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / SYSTEM_PROFILE_FILENAME
            path.write_text(
                json.dumps(normalize_system_preset(preset), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        else:
            path = write_preset_file(folder, preset)
        paths.append(path)
    return paths


def write_preset_file(folder: Path, preset: dict[str, Any]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^\w\- ]+", "", preset.get("name", "preset")).strip().replace(" ", "_")
    if not safe_name:
        safe_name = "preset"
    path = folder / f"{safe_name}.lghub-preset.json"
    path.write_text(json.dumps(preset, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_preset_file(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("format") != FORMAT_VERSION:
        raise ValueError(f"Unsupported preset format in {path}: {data.get('format')}")
    return data
