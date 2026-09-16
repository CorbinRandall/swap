"""Import portable preset files into G Hub settings.db."""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .db import (
    backup_settings_db,
    detect_slot_prefix,
    get_desktop_application_id,
    list_profiles,
    prepare_settings_db,
    read_settings,
    write_settings,
)
from .devices import DEVICES, get_device, remap_slot_id, slot_prefix_for_model
from .export import load_preset_file
from .builtin_cards import materialize_builtin_cards
from .system_profile import (
    LEGACY_SYSTEM_PROFILE_NAME,
    SYSTEM_PROFILE_LABEL,
    collect_import_paths,
    collect_user_import_paths,
    ensure_system_profile_file,
    ghub_system_profile_keep_names,
    is_system_preset_path,
    preset_for_ghub_system_restore,
    user_profile_names_from_paths,
)
from .ghub_running import ensure_ghub_stopped, require_ghub_stopped
from .paths import presets_dir


ConflictMode = Literal["skip", "replace", "rename"]


@dataclass
class ImportResult:
    imported: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    replaced: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    cards_removed: int = 0


def _new_id() -> str:
    return str(uuid.uuid4())


def _cards_list(settings: dict[str, Any]) -> list[dict[str, Any]]:
    return settings.setdefault("cards", {}).setdefault("cards", [])


def _profiles_list(settings: dict[str, Any]) -> list[dict[str, Any]]:
    return settings.setdefault("profiles", {}).setdefault("profiles", [])


def _find_profile_by_name(profiles: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for profile in profiles:
        if profile.get("name") == name:
            return profile
    return None


def _remove_profile(profiles: list[dict[str, Any]], profile_id: str) -> None:
    profiles[:] = [p for p in profiles if p.get("id") != profile_id]


def _collect_profile_card_ids(profile: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for assignment in profile.get("assignments", []):
        cid = assignment.get("cardId")
        if cid:
            ids.add(cid)
    lighting = profile.get("lightingCard")
    if lighting:
        ids.add(lighting)
    return ids


def _is_desktop_mouse_profile(profile: dict[str, Any], desktop_id: str | None) -> bool:
    """True if this looks like a global/desktop mouse profile (safe to manage via toolkit)."""
    if desktop_id and profile.get("applicationId") == desktop_id:
        return True
    for assignment in profile.get("assignments", []):
        slot = assignment.get("slotId", "")
        if "spectrum_" in slot or "wireless_" in slot or "_mouse" in slot or "_g1_m1" in slot:
            return True
    return False


def _prune_orphan_cards(settings: dict[str, Any]) -> int:
    profiles = _profiles_list(settings)
    profile_ids = {p.get("id") for p in profiles if p.get("id")}
    referenced: set[str] = set()
    for profile in profiles:
        referenced |= _collect_profile_card_ids(profile)

    cards = _cards_list(settings)
    before = len(cards)
    kept: list[dict[str, Any]] = []
    for card in cards:
        cid = card.get("id")
        pid = card.get("profileId")
        if cid in referenced:
            kept.append(card)
        elif pid and pid in profile_ids:
            kept.append(card)
        elif not pid:
            kept.append(card)
    cards[:] = kept
    return before - len(cards)


def _upsert_card(cards: list[dict[str, Any]], card: dict[str, Any]) -> None:
    card_id = card.get("id")
    for idx, existing in enumerate(cards):
        if existing.get("id") == card_id:
            cards[idx] = card
            return
    cards.append(card)


def _resolve_target_slot_prefix(
    settings: dict[str, Any],
    preset: dict[str, Any],
    target_device: str | None,
) -> str | None:
    if target_device:
        prefix = slot_prefix_for_model(target_device)
        if prefix:
            return prefix
    return detect_slot_prefix(settings) or preset.get("slotPrefix")


def _apply_slot_remap(profile: dict[str, Any], from_prefix: str | None, to_prefix: str | None) -> None:
    if not from_prefix or not to_prefix or from_prefix == to_prefix:
        return
    for assignment in profile.get("assignments", []):
        slot_id = assignment.get("slotId")
        if slot_id:
            assignment["slotId"] = remap_slot_id(slot_id, from_prefix, to_prefix)


def _filter_assignments_for_prefix(profile: dict[str, Any], prefix: str | None) -> None:
    if not prefix:
        return
    profile["assignments"] = [
        assignment
        for assignment in profile.get("assignments", [])
        if assignment.get("slotId", "").startswith(prefix)
    ]


def _known_slot_prefixes(settings: dict[str, Any]) -> set[str]:
    prefixes: set[str] = set()
    for device in settings.get("devices/known", {}).get("knownList", []):
        prefix = slot_prefix_for_model(device.get("modelId", ""))
        if prefix:
            prefixes.add(prefix)
    return prefixes


def _mirror_companion_assignments(
    profile: dict[str, Any],
    source_prefix: str | None,
    settings: dict[str, Any],
) -> None:
    """Copy bindings to other known G502 slot prefixes (G Hub adds them on startup)."""
    if not source_prefix:
        return
    companions = _known_slot_prefixes(settings) - {source_prefix}
    if not companions:
        return

    existing = {assignment.get("slotId") for assignment in profile.get("assignments", [])}
    additions: list[dict[str, Any]] = []
    for assignment in profile.get("assignments", []):
        slot_id = assignment.get("slotId", "")
        if not slot_id.startswith(source_prefix):
            continue
        for companion in companions:
            mirrored = remap_slot_id(slot_id, source_prefix, companion)
            if mirrored in existing:
                continue
            additions.append({"cardId": assignment["cardId"], "slotId": mirrored})
            existing.add(mirrored)
    profile.setdefault("assignments", []).extend(additions)


def _regenerate_ids(
    profile: dict[str, Any],
    cards: list[dict[str, Any]],
) -> None:
    old_profile_id = profile.get("id", _new_id())
    new_profile_id = _new_id()
    id_map: dict[str, str] = {old_profile_id: new_profile_id}
    profile["id"] = new_profile_id

    for card in cards:
        old_card_id = card.get("id")
        if not old_card_id:
            continue
        new_card_id = _new_id()
        id_map[old_card_id] = new_card_id
        card["id"] = new_card_id
        if card.get("profileId") == old_profile_id:
            card["profileId"] = new_profile_id

    lighting = profile.get("lightingCard")
    if lighting in id_map:
        profile["lightingCard"] = id_map[lighting]

    for assignment in profile.get("assignments", []):
        cid = assignment.get("cardId")
        if cid in id_map:
            assignment["cardId"] = id_map[cid]


def _device_key_for_preset(preset: dict[str, Any]) -> str:
    model = preset.get("sourceDevice", "")
    for key, cfg in DEVICES.items():
        if cfg.model_id == model:
            return key
    return "g502"


def _maybe_reconvert_for_platform(preset: dict[str, Any], target_platform: str | None) -> dict[str, Any]:
    omm_raw = preset.get("ommRaw")
    if not target_platform or not omm_raw:
        return preset
    from .convert import omm_to_ghub_preset

    device = get_device(_device_key_for_preset(preset))
    return omm_to_ghub_preset(
        omm_raw,
        device,
        profile_name=preset.get("name"),
        slot=preset.get("readable", {}).get("onboardSlot"),
        target_platform=target_platform,
    )


def _find_desktop_system_profile(
    profiles: list[dict[str, Any]],
    desktop_id: str | None,
) -> dict[str, Any] | None:
    if not desktop_id:
        return None
    for profile in profiles:
        if profile.get("applicationId") != desktop_id:
            continue
        if profile.get("name") in {LEGACY_SYSTEM_PROFILE_NAME, SYSTEM_PROFILE_LABEL}:
            return profile
        if profile.get("id") == desktop_id:
            return profile
    return None


def _remove_visible_system_profile_duplicates(
    settings: dict[str, Any],
) -> list[str]:
    """Drop desktop profiles wrongly named DONT_TOUCH_SYSTEM (disk-only label)."""
    desktop_id = get_desktop_application_id(settings)
    if not desktop_id:
        return []
    profiles = _profiles_list(settings)
    removed: list[str] = []
    for profile in list(profiles):
        name = profile.get("name")
        if name == SYSTEM_PROFILE_LABEL and profile.get("applicationId") == desktop_id:
            removed.append(name)
            _remove_profile(profiles, profile["id"])
    return removed


def restore_system_profile_to_settings(
    settings: dict[str, Any],
    library: Path,
    *,
    target_device: str | None = None,
) -> bool:
    """
    Ensure Logitech's desktop factory default exists under PROFILE_NAME_DEFAULT.
    The on-disk DONT_TOUCH_SYSTEM backup is never imported as a visible profile.
    """
    _remove_visible_system_profile_duplicates(settings)
    path = ensure_system_profile_file(library)
    preset = preset_for_ghub_system_restore(load_preset_file(path))
    desktop_id = get_desktop_application_id(settings)
    existing = _find_desktop_system_profile(_profiles_list(settings), desktop_id)
    merged = merge_preset_into_settings(
        settings,
        preset,
        conflict_mode="replace",
        target_device=target_device,
        preserve_ids=existing is not None,
        profile_id=existing.get("id") if existing else None,
    )
    return merged is not None


def merge_preset_into_settings(
    settings: dict[str, Any],
    preset: dict[str, Any],
    *,
    conflict_mode: ConflictMode = "skip",
    rename_to: str | None = None,
    target_device: str | None = None,
    preserve_ids: bool = False,
    profile_id: str | None = None,
) -> str | None:
    profile = copy.deepcopy(preset["profile"])
    cards = copy.deepcopy(preset.get("cards", []))
    profile_name = rename_to or preset.get("name") or profile.get("name", "Imported")
    source_prefix = preset.get("slotPrefix")

    import_payload = {
        "profile": profile,
        "cards": cards,
        "readable": preset.get("readable"),
    }
    materialize_builtin_cards(import_payload)
    cards = import_payload["cards"]
    _filter_assignments_for_prefix(profile, source_prefix)

    profiles = _profiles_list(settings)
    existing = _find_profile_by_name(profiles, profile_name)

    if existing and conflict_mode == "skip":
        return None

    if existing and conflict_mode == "replace":
        _remove_profile(profiles, existing["id"])

    if preserve_ids:
        if profile_id:
            profile["id"] = profile_id
    else:
        _regenerate_ids(profile, cards)

    profile["name"] = profile_name

    desktop_id = get_desktop_application_id(settings)
    if desktop_id:
        profile["applicationId"] = desktop_id
        for card in cards:
            app_id = card.get("applicationId")
            prof_id = card.get("profileId")
            if app_id or prof_id == preset["profile"].get("id"):
                if app_id:
                    card["applicationId"] = desktop_id

    from_prefix = source_prefix
    to_prefix = _resolve_target_slot_prefix(settings, preset, target_device)
    _apply_slot_remap(profile, from_prefix, to_prefix)
    _mirror_companion_assignments(profile, to_prefix or from_prefix, settings)

    card_store = _cards_list(settings)
    for card in cards:
        if card.get("profileId") == preset["profile"].get("id"):
            card["profileId"] = profile["id"]
        _upsert_card(card_store, card)

    profiles.append(profile)
    return profile_name


def import_presets(
    paths: list[Path],
    *,
    conflict_mode: ConflictMode = "skip",
    rename_to: str | None = None,
    target_device: str | None = None,
    target_platform: str | None = None,
    db_path: Path | None = None,
    dry_run: bool = False,
    library: Path | None = None,
) -> ImportResult:
    if not dry_run:
        require_ghub_stopped()
        backup_settings_db(db_path)

    settings = read_settings(db_path)
    result = ImportResult()

    for path in paths:
        if is_system_preset_path(path):
            continue
        preset = load_preset_file(path)
        preset = _maybe_reconvert_for_platform(preset, target_platform)
        name = rename_to or preset.get("name", path.stem)

        existing = _find_profile_by_name(_profiles_list(settings), name)
        if existing and conflict_mode == "skip":
            result.skipped.append(name)
            continue

        if dry_run:
            if existing and conflict_mode == "replace":
                result.replaced.append(name)
            else:
                result.imported.append(name)
            continue

        merged = merge_preset_into_settings(
            settings,
            preset,
            conflict_mode=conflict_mode,
            rename_to=rename_to if len(paths) == 1 else None,
            target_device=target_device,
        )
        if merged is None:
            result.skipped.append(name)
        elif existing and conflict_mode == "replace":
            result.replaced.append(merged)
        else:
            result.imported.append(merged)

    presets_library = library or (paths[0].parent if paths else presets_dir())

    if not dry_run:
        restore_system_profile_to_settings(
            settings,
            presets_library,
            target_device=target_device,
        )
        write_settings(settings, db_path)

    return result


def replace_library_with_presets(
    presets_dir: Path,
    *,
    target_device: str | None = None,
    target_platform: str | None = None,
    db_path: Path | None = None,
    dry_run: bool = False,
    desktop_only: bool = True,
) -> ImportResult:
    """
    Make G Hub match the Presets folder: remove extra desktop mouse profiles and
    orphaned macros, then import every preset file (replace by name).
    """
    if not dry_run:
        ensure_ghub_stopped(quit_first=True)
    user_paths = collect_preset_paths(presets_dir)
    if not user_paths:
        raise FileNotFoundError(
            f"No user preset files in {presets_dir}. "
            f"Add .lghub-preset.json files to {presets_dir.name}/."
        )
    paths = collect_user_import_paths(presets_dir)

    keep_names = (
        user_profile_names_from_paths(user_paths)
        | ghub_system_profile_keep_names()
    )

    if not dry_run:
        backup_settings_db(db_path)
        prepare_settings_db(db_path)

    settings = read_settings(db_path)
    result = ImportResult()
    desktop_id = get_desktop_application_id(settings)
    profiles = _profiles_list(settings)

    for profile in list(profiles):
        name = profile.get("name", "?")
        if name in keep_names:
            continue
        if desktop_only and not _is_desktop_mouse_profile(profile, desktop_id):
            continue
        result.removed.append(name)
        if not dry_run:
            _remove_profile(profiles, profile["id"])

    if not dry_run:
        result.cards_removed = _prune_orphan_cards(settings)

    for path in paths:
        preset = load_preset_file(path)
        preset = _maybe_reconvert_for_platform(preset, target_platform)
        name = preset.get("name", path.stem)
        existing = _find_profile_by_name(_profiles_list(settings), name)

        if dry_run:
            if existing:
                result.replaced.append(name)
            else:
                result.imported.append(name)
            continue

        merged = merge_preset_into_settings(
            settings,
            preset,
            conflict_mode="replace",
            target_device=target_device,
        )
        if merged:
            if existing:
                result.replaced.append(merged)
            else:
                result.imported.append(merged)

    if not dry_run:
        restore_system_profile_to_settings(
            settings,
            presets_dir,
            target_device=target_device,
        )
        write_settings(settings, db_path)
        prepare_settings_db(db_path)
        final_names = {p.get("name") for p in list_profiles(read_settings(db_path))}
        extra = sorted(final_names - keep_names)
        missing = sorted(keep_names - final_names)
        if SYSTEM_PROFILE_LABEL in final_names:
            extra = sorted(set(extra) | {SYSTEM_PROFILE_LABEL})
        if extra or missing:
            raise RuntimeError(
                "Replace did not stick in settings.db. "
                f"Extra profiles: {extra or 'none'}; missing: {missing or 'none'}. "
                "Quit G Hub completely (check Activity Monitor for lghub) and run again."
            )

    return result


def collect_preset_paths(target: Path, *, recursive: bool = True) -> list[Path]:
    if target.is_file():
        return [target]
    if not target.is_dir():
        raise FileNotFoundError(f"Not found: {target}")

    resolved = target.resolve()
    if recursive and resolved == presets_dir(resolved).resolve():
        return collect_user_import_paths(resolved)

    from .library import scan_user_preset_files

    if recursive:
        return scan_user_preset_files(resolved)

    return sorted(target.glob("*.lghub-preset.json"))
