"""Convert omm onboard profile JSON to G Hub preset format."""

from __future__ import annotations

import re
import struct
import uuid
from datetime import datetime, timezone
from typing import Any

from .devices import DeviceConfig, G502_BUTTONS, G502_SLOT_LABELS, omm_index_to_ghub_slot
from .omm.HidppConstants import KeyCode
from .platform_remap import remap_omm_for_platform
from .preset_format import FORMAT_VERSION

PRESET_PREFIX = "0f82f693-5b78-4cf5-867e-"

# HID modifier usage codes used by G Hub (partial).
MODIFIER_HID = {
    "lctrl": 224,
    "lshift": 225,
    "lalt": 226,
    "lgui": 227,
    "rctrl": 228,
    "rshift": 229,
    "ralt": 230,
    "rgui": 231,
}

KEY_HID: dict[str, int] = {name: int(code) for name, code in KeyCode.__members__.items()}

KEY_DISPLAY = {
    "lctrl": "CTRL",
    "rctrl": "CTRL",
    "lshift": "SHIFT",
    "rshift": "SHIFT",
    "lalt": "ALT",
    "ralt": "ALT",
    "lgui": "CMD",
    "rgui": "CMD",
    "space": "SPACEBAR",
    "left": "LEFT",
    "rightarrow": "RIGHT",
    "leftarrow": "LEFT",
    "up": "UP",
    "uparrow": "UP",
    "down": "DOWN",
    "downarrow": "DOWN",
    "right": "RIGHT",
    "enter": "ENTER",
    "tab": "TAB",
    "backspace": "BACKSPACE",
    "escape": "ESC",
}


def _new_id() -> str:
    return str(uuid.uuid4())


def _preset_card_id(suffix: str) -> str:
    return f"{PRESET_PREFIX}{suffix}"


def _f_key_preset(n: int) -> str:
    return _preset_card_id(f"02{n:02x}00000000")


def _key_hid(name: str) -> int | None:
    key = name.lower()
    if key in MODIFIER_HID:
        return MODIFIER_HID[key]
    if key in KEY_HID:
        return KEY_HID[key]
    return None


def _key_display(name: str) -> str:
    key = name.lower()
    return KEY_DISPLAY.get(key, key.upper())


def _normalize_action(action: dict[str, Any]) -> dict[str, Any]:
    if action.get("action") != "unknown":
        return action
    raw = action.get("bytes", "")
    if not isinstance(raw, str):
        return action
    try:
        val = struct.unpack(">I", raw.encode("latin-1"))[0]
    except (struct.error, UnicodeEncodeError):
        return action
    if (val >> 16) & 0xFFFF == 0x900B:
        return {"action": "button", "value": "g_shift"}
    return action


def _macro_label(text: str) -> str:
    parts: list[str] = []
    for tok in text.strip().split():
        if tok.startswith(("+", "-")):
            key = tok[1:].lower()
            if key in ("lgui", "rgui"):
                parts.append("⌘")
            elif key in ("lctrl", "rctrl"):
                parts.append("⌃")
            elif key in ("lshift", "rshift"):
                parts.append("⇧")
            elif key in ("lalt", "ralt"):
                parts.append("⌥")
            elif tok.startswith("+") and key in KEY_HID:
                parts.append(_key_display(key))
    compact = "".join(parts)
    return compact or "Onboard Macro"


def _omm_macro_to_components(text: str) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    for tok in text.strip().split():
        if not tok:
            continue
        sleep_match = re.fullmatch(r"sleep\((\d+)\)", tok)
        if sleep_match:
            components.append({"delay": {"durationMs": int(sleep_match.group(1))}})
            continue
        if tok[0] not in "+-":
            continue
        is_down = tok[0] == "+"
        key_name = tok[1:].lower()
        hid = _key_hid(key_name)
        if hid is None:
            continue
        keyboard: dict[str, Any] = {
            "displayName": _key_display(key_name),
            "hidUsage": str(hid),
        }
        if is_down:
            keyboard["isDown"] = True
        components.append({"keyboard": keyboard})
    return components


def _parse_chord_keystroke(text: str) -> tuple[list[int], int, str] | None:
    """If *text* is one modified key press, return modifiers, key code, and label."""
    modifiers: list[int] = []
    key_code: int | None = None
    key_name: str | None = None
    key_downs = 0

    for tok in text.strip().split():
        if re.fullmatch(r"sleep\(\d+\)", tok):
            continue
        if tok[0] not in "+-":
            continue
        is_down = tok[0] == "+"
        name = tok[1:].lower()
        hid = _key_hid(name)
        if hid is None:
            return None
        if name in MODIFIER_HID:
            if is_down:
                modifiers.append(hid)
            continue
        if is_down:
            key_downs += 1
            key_code = hid
            key_name = name
        elif key_name != name:
            return None

    if key_downs != 1 or key_code is None or key_name is None:
        return None

    label_parts: list[str] = []
    if 227 in modifiers or 231 in modifiers:
        label_parts.append("⌘")
    if 225 in modifiers or 229 in modifiers:
        label_parts.append("⇧")
    if 224 in modifiers or 228 in modifiers:
        label_parts.append("⌃")
    if 226 in modifiers or 230 in modifiers:
        label_parts.append("⌥")
    label_parts.append(_key_display(key_name))
    return modifiers, key_code, "".join(label_parts)


def _keystroke_card(
    modifiers: list[int],
    code: int,
    label: str,
    cards: list[dict[str, Any]],
    profile_id: str,
) -> str:
    card_id = _new_id()
    cards.append(
        {
            "id": card_id,
            "name": label,
            "attribute": "MACRO_PLAYBACK",
            "profileId": profile_id,
            "macro": {
                "type": "KEYSTROKE",
                "actionName": label,
                "keystroke": {"code": code, "modifiers": modifiers},
            },
        }
    )
    return card_id


def _macro_sequence_card(
    text: str,
    cards: list[dict[str, Any]],
    profile_id: str,
) -> str:
    components = _omm_macro_to_components(text)
    label = _macro_label(text)
    card_id = _new_id()
    cards.append(
        {
            "id": card_id,
            "name": label,
            "attribute": "MACRO_PLAYBACK",
            "profileId": profile_id,
            "macro": {
                "type": "SEQUENCE",
                "onboardable": True,
                "sequence": {
                    "defaultDelay": 50,
                    "heldSequence": {},
                    "pressSequence": {},
                    "releaseSequence": {},
                    "showUpDown": True,
                    "simpleSequence": {"components": components},
                    "toggleSequence": {},
                    "useDefaultDelay": True,
                    "useSimpleActions": True,
                },
            },
        }
    )
    return card_id


def _action_to_card(action: dict[str, Any], cards: list[dict[str, Any]], profile_id: str) -> str:
    action = _normalize_action(action)
    kind = action.get("action")
    if kind == "button":
        value = action.get("value", "")
        if value in ("no_action", "no_button"):
            return _preset_card_id("090700000000")
        mouse_presets = {
            "left_button": "020100000000",
            "middle_button": "020300000000",
            "backward_button": "016800000000",
            "forward_button": "016900000000",
            "next_dpi": "090500000000",
            "cycle_dpi": "090500000000",
            "g_shift": "014000000000",
        }
        suffix = mouse_presets.get(value)
        if suffix:
            return _preset_card_id(suffix)
    elif kind == "key":
        key_name = (action.get("value") or "").lower()
        if key_name.startswith("f") and key_name[1:].isdigit():
            n = int(key_name[1:])
            if 1 <= n <= 24:
                return _f_key_preset(n)
        modifiers = []
        mod_str = action.get("modifier") or ""
        for part in mod_str.replace(",", "+").split("+"):
            part = part.strip().lower()
            if part in MODIFIER_HID:
                modifiers.append(MODIFIER_HID[part])
        code = _key_hid(key_name)
        if code is not None:
            card_id = _new_id()
            label_parts = []
            if "lgui" in mod_str.lower():
                label_parts.append("⌘")
            if "lshift" in mod_str.lower():
                label_parts.append("⇧")
            if "lctrl" in mod_str.lower():
                label_parts.append("⌃")
            if "lalt" in mod_str.lower():
                label_parts.append("⌥")
            label_parts.append(_key_display(key_name))
            cards.append(
                {
                    "id": card_id,
                    "name": "".join(label_parts) or key_name,
                    "attribute": "MACRO_PLAYBACK",
                    "profileId": profile_id,
                    "macro": {
                        "type": "KEYSTROKE",
                        "actionName": "".join(label_parts) or key_name,
                        "keystroke": {"code": code, "modifiers": modifiers},
                    },
                }
            )
            return card_id
    elif kind in ("macro", "macro_unparsed"):
        text = action.get("value") or ""
        if text:
            chord = _parse_chord_keystroke(text)
            if chord is not None:
                modifiers, code, label = chord
                return _keystroke_card(modifiers, code, label, cards, profile_id)
            return _macro_sequence_card(text, cards, profile_id)
        card_id = _new_id()
        cards.append(
            {
                "id": card_id,
                "name": "Onboard Macro",
                "attribute": "MACRO_PLAYBACK",
                "profileId": profile_id,
                "macro": {
                    "type": "SEQUENCE",
                    "onboardable": True,
                    "sequence": {
                        "useDefaultDelay": True,
                        "defaultDelay": 50,
                        "useSimpleActions": True,
                        "simpleSequence": {"components": []},
                    },
                },
            }
        )
        return card_id

    return _preset_card_id("090700000000")


def _dpi_levels(raw: list[int]) -> list[int]:
    levels = [d for d in raw if d > 0]
    return levels[:5] if levels else [800, 1600, 3200]


def _dpi_from_index(levels: list[int], index: int | None) -> int:
    if not levels:
        return 800
    if index is None or index < 1:
        return levels[0]
    if index <= len(levels):
        return levels[index - 1]
    return levels[-1]


def omm_to_ghub_preset(
    omm_json: dict[str, Any],
    device: DeviceConfig,
    *,
    profile_name: str | None = None,
    slot: int | None = None,
    target_platform: str | None = None,
) -> dict[str, Any]:
    if target_platform:
        omm_json = remap_omm_for_platform(omm_json, target_platform)
    profile_id = _new_id()
    prefix = device.slot_prefix
    cards: list[dict[str, Any]] = []

    dpi_levels = _dpi_levels(omm_json.get("dpi_list", []))
    default_dpi = _dpi_from_index(dpi_levels, omm_json.get("dpi_default"))
    shift_dpi = _dpi_from_index(dpi_levels, omm_json.get("dpi_shift"))
    active_dpi = default_dpi

    mouse_settings_id = _new_id()
    report_rate = omm_json.get("extended_report_rate") or omm_json.get("report_rate") or 1000
    cards.append(
        {
            "id": mouse_settings_id,
            "name": "DEFAULT_CARD_NAME_MOUSE_SETTINGS",
            "attribute": "MOUSE_SETTINGS",
            "profileId": profile_id,
            "mouseSettings": {
                "dpiTable": {
                    "activeDpi": active_dpi,
                    "defaultDpi": default_dpi,
                    "levels": dpi_levels,
                    "shiftDpi": shift_dpi,
                },
                "reportRate": {"value": report_rate},
            },
        }
    )

    lighting_id = _new_id()
    cards.append(
        {
            "id": lighting_id,
            "name": "DEFAULT_CARD_NAME_FIRMWARE_LIGHTING_SETTINGS",
            "attribute": "FIRMWARE_LIGHTING_SETTINGS",
            "readOnly": True,
            "firmwareLightingSettings": {
                "effects": [
                    {
                        "id": "CYCLE",
                        "zoneType": "ZONE_BRANDING",
                        "cycleParams": {"intensity": 1, "periodInMs": 8000},
                    },
                    {
                        "id": "CYCLE",
                        "zoneType": "ZONE_PRIMARY",
                        "cycleParams": {"intensity": 1, "periodInMs": 8000},
                    },
                ]
            },
        }
    )

    assignments: list[dict[str, Any]] = []
    buttons = omm_json.get("buttons", [])
    buttons_gshift = omm_json.get("buttons_gshift", [])

    for idx, g_slot in enumerate(G502_BUTTONS):
        slot_base = f"{prefix}{g_slot}_m1"
        slot_shift = f"{prefix}{g_slot}_m1_shifted"
        if idx < len(buttons):
            card_id = _action_to_card(buttons[idx], cards, profile_id)
            assignments.append({"slotId": slot_base, "cardId": card_id})
        if idx < len(buttons_gshift):
            card_id = _action_to_card(buttons_gshift[idx], cards, profile_id)
            assignments.append({"slotId": slot_shift, "cardId": card_id})

    assignments.append({"slotId": f"{prefix}mouse_settings", "cardId": mouse_settings_id})
    assignments.append({"slotId": f"{prefix}lighting_setting_firmware", "cardId": lighting_id})

    display_name = profile_name or omm_json.get("profile_name") or f"Onboard Slot {slot or 1}"

    profile = {
        "id": profile_id,
        "name": display_name.strip("\x00").strip() or display_name,
        "applicationId": "",
        "activeForApplication": False,
        "assignments": assignments,
        "lightingCard": lighting_id,
    }

    readable_buttons = []
    for idx in range(max(len(buttons), len(buttons_gshift))):
        g_slot = omm_index_to_ghub_slot(idx)
        label = G502_SLOT_LABELS.get(g_slot, g_slot)
        if idx < len(buttons):
            readable_buttons.append(
                {"omm_index": idx, "slot": g_slot, "label": label, "layer": "normal", "action": buttons[idx]}
            )
        if idx < len(buttons_gshift):
            readable_buttons.append(
                {
                    "omm_index": idx,
                    "slot": g_slot,
                    "label": label,
                    "layer": "gshift",
                    "action": buttons_gshift[idx],
                }
            )

    return {
        "format": FORMAT_VERSION,
        "name": profile["name"],
        "sourceDevice": device.model_id,
        "slotPrefix": prefix,
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "profile": profile,
        "cards": cards,
        "ommRaw": omm_json,
        "readable": {
            "buttons": readable_buttons,
            "dpi": cards[0]["mouseSettings"],
            "onboardSlot": slot,
        },
    }
