"""Remap onboard shortcut semantics between operating systems."""

from __future__ import annotations

import copy
import re
from typing import Any

# Windows Ctrl+letter editing shortcuts → Mac Cmd+letter.
_WINDOWS_CTRL_EDIT_KEYS = frozenset("acvfwxz")

_MODIFIER_TOKEN = re.compile(r"^([+-])(lctrl|rctrl|lshift|rshift|lalt|ralt|lgui|rgui)$")


def _remap_macro_text_for_mac(text: str) -> str:
    """Remap lone Ctrl chords to Cmd for standard editing shortcuts."""
    if "lgui" in text or "rgui" in text:
        return text
    tokens = text.strip().split()
    key_token = next((t for t in tokens if t.startswith("+") and t[1:].lower() in _WINDOWS_CTRL_EDIT_KEYS), None)
    if key_token is None:
        return text
    if not any(t in ("+lctrl", "+rctrl") for t in tokens):
        return text
    return text.replace("+lctrl", "+lgui").replace("-lctrl", "-lgui").replace("+rctrl", "+rgui").replace("-rctrl", "-rgui")


def _remap_key_action_for_mac(action: dict[str, Any]) -> dict[str, Any]:
    mod = (action.get("modifier") or "").lower()
    key = (action.get("value") or "").lower()
    if key not in _WINDOWS_CTRL_EDIT_KEYS:
        return action
    if mod in ("lctrl", "rctrl"):
        out = copy.copy(action)
        out["modifier"] = "lgui" if mod == "lctrl" else "rgui"
        return out
    return action


def remap_omm_for_platform(omm_json: dict[str, Any], target_platform: str) -> dict[str, Any]:
    """Return a copy of omm JSON with shortcuts adjusted for *target_platform*."""
    if target_platform.lower() != "mac":
        return omm_json

    out = copy.deepcopy(omm_json)
    for field in ("buttons", "buttons_gshift"):
        remapped: list[dict[str, Any]] = []
        for action in out.get(field, []):
            action = copy.copy(action)
            kind = action.get("action")
            if kind == "key":
                action = _remap_key_action_for_mac(action)
            elif kind == "macro" and action.get("value"):
                action["value"] = _remap_macro_text_for_mac(action["value"])
            remapped.append(action)
        out[field] = remapped
    return out
