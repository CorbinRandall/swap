"""
G502 G Hub ↔ built-in preset ID Rosetta stone.

Reference for import/export and compare tooling. See also:
- devices.G502_SLOT_LABELS — physical button names (g1 = left click, …)
- compare.py — OMM onboard index N → G Hub slot g(N+1)
- convert.py PRESET_PREFIX + mouse_presets / _f_key_preset

Built-in card IDs use prefix 0f82f693-5b78-4cf5-867e- + 12 hex digits.
G Hub resolves many of these internally (no row in settings.cards required).

Critical: 02xx suffixes are overloaded — the SAME id on g1/g2/g3 is the
standard primary/secondary/middle mouse click, but on other slots 020100 means
F1, 020200 means F2, 020300 means F3. Always use slotId to disambiguate.
"""

from __future__ import annotations

import re

from .convert import PRESET_PREFIX
from .devices import G502_SLOT_LABELS
from .omm.HidppConstants import KeyCode

_HID_BY_NAME = {name: int(code) for name, code in KeyCode.__members__.items()}
_NAME_BY_HID = {code: name for name, code in _HID_BY_NAME.items()}

# Standard mouse clicks on g1–g3 (convert.py mouse_presets overlap F-key ids).
STANDARD_MOUSE_SUFFIX_BY_SLOT: dict[str, str] = {
    "g1": "020100000000",  # left_button
    "g2": "020200000000",  # right_button (same suffix family as F2 elsewhere)
    "g3": "020300000000",  # middle_button
}

SUFFIX_TO_STANDARD_MOUSE_SLOT = {
    suffix: slot for slot, suffix in STANDARD_MOUSE_SUFFIX_BY_SLOT.items()
}

# G Hub resolves these; do not materialize as custom cards on import.
SYSTEM_BUILTIN_SUFFIXES = frozenset(
    {
        "090700000000",  # disabled / G-Shift layer default
        "090500000000",  # cycle / next DPI
        "090100000000",  # scroll up
        "090600000000",  # scroll down
        "014000000000",  # G-Shift
        "016800000000",  # back (thumb)
        "016900000000",  # forward (thumb)
    }
)

_SLOT_RE = re.compile(r"_g(\d+)_m1(?:_shifted)?$")


def is_builtin_preset_id(card_id: str) -> bool:
    return str(card_id).startswith(PRESET_PREFIX)


def suffix(card_id: str) -> str:
    return card_id.split("-")[-1]


def ghub_button_slot(slot_id: str) -> str | None:
    """Return g1..g11 from a slotId like g502spectrum_g5_m1 or g502wireless_g5_m1_shifted."""
    match = _SLOT_RE.search(slot_id)
    if not match:
        return None
    return f"g{match.group(1)}"


def is_standard_mouse_click(slot_id: str, card_id: str) -> bool:
    """True when this assignment is the factory primary/secondary/middle click."""
    button = ghub_button_slot(slot_id)
    if button not in STANDARD_MOUSE_SUFFIX_BY_SLOT:
        return False
    return suffix(card_id) == STANDARD_MOUSE_SUFFIX_BY_SLOT[button]


def should_keep_builtin_reference(slot_id: str, card_id: str) -> bool:
    """Built-in ids G Hub must interpret itself (mouse clicks + system actions)."""
    if not is_builtin_preset_id(card_id):
        return False
    suf = suffix(card_id)
    if suf in SYSTEM_BUILTIN_SUFFIXES:
        return True
    if is_standard_mouse_click(slot_id, card_id):
        return True
    return False


def describe_builtin_suffix(suf: str, *, slot_id: str | None = None) -> str:
    if slot_id and is_standard_mouse_click(slot_id, f"{PRESET_PREFIX}{suf}"):
        button = ghub_button_slot(slot_id)
        if button:
            return G502_SLOT_LABELS.get(button, button)

    if suf in SYSTEM_BUILTIN_SUFFIXES:
        return {
            "090700000000": "Disabled",
            "090500000000": "Cycle DPI",
            "090100000000": "Scroll up",
            "090600000000": "Scroll down",
            "014000000000": "G-Shift",
            "016800000000": "Back",
            "016900000000": "Forward",
        }.get(suf, suf)

    if suf.startswith("020") and len(suf) >= 4:
        n = int(suf[2:4], 16)
        if 1 <= n <= 24:
            return f"F{n}"

    if suf.startswith("01") and len(suf) >= 4:
        code = int(suf[2:4], 16)
        if code in _NAME_BY_HID:
            return _NAME_BY_HID[code].upper()

    if suf.startswith("04") and len(suf) >= 4:
        code = int(suf[2:4], 16) + 3
        if code in _NAME_BY_HID:
            return _NAME_BY_HID[code].upper()

    return f"Built-in {suf}"


def decode_keystroke_suffix(suf: str, *, slot_id: str | None = None) -> tuple[int, list[int], str] | None:
    """Decode to (hid_code, modifiers, label) for materializing KEYSTROKE cards."""
    if suf in SYSTEM_BUILTIN_SUFFIXES:
        return None
    if slot_id and is_standard_mouse_click(slot_id, f"{PRESET_PREFIX}{suf}"):
        return None

    if suf.startswith("020") and len(suf) >= 4:
        n = int(suf[2:4], 16)
        if 1 <= n <= 24:
            f_name = f"f{n}"
            if f_name in _HID_BY_NAME:
                return _HID_BY_NAME[f_name], [], f"F{n}"

    if suf.startswith("01") and len(suf) >= 4:
        code = int(suf[2:4], 16)
        if code in _NAME_BY_HID:
            name = _NAME_BY_HID[code]
            label = name.upper() if len(name) <= 2 else name.upper()
            return code, [], label

    if suf.startswith("04") and len(suf) >= 4:
        code = int(suf[2:4], 16) + 3
        if code in _NAME_BY_HID:
            name = _NAME_BY_HID[code]
            return code, [], name.upper()

    return None
