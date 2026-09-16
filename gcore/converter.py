"""Convert lghub-preset-v1 JSON → OMM onboard profile JSON for HID++ writes."""

from __future__ import annotations

from typing import Any

from .paths import ensure_toolkit_on_path

ensure_toolkit_on_path()

from ghub_presets.convert import KEY_HID, MODIFIER_HID, PRESET_PREFIX  # noqa: E402
from ghub_presets.devices import G502_BUTTONS  # noqa: E402
from ghub_presets.omm.HidppConstants import KeyCode  # noqa: E402
from ghub_presets.rosetta import (  # noqa: E402
    SYSTEM_BUILTIN_SUFFIXES,
    ghub_button_slot,
    is_builtin_preset_id,
    is_standard_mouse_click,
    suffix,
)

# Template paddings / RGB from a known-good G502 page layout (wired Spectrum).
_DEFAULT_CHUNK1 = "ÿ\x00ÿÿÿÿÿÿÿÿÿÿÿÿÿÿ"
_DEFAULT_BUTTONS_PADDING = "ÿÿÿÿÿÿÿÿÿÿÿÿÿÿÿÿÿÿÿÿ"
_DEFAULT_GSHIFT_PADDING = "ÿÿÿÿÿÿÿÿÿÿÿÿÿÿÿÿÿÿÿÿ"
_DEFAULT_CHUNK2 = "\x00ÿ"
_DEFAULT_RGB = [
    {"mode": "cycling", "color": "0x000000", "duration": 8000, "brightness": 100},
    {"mode": "cycling", "color": "0x000000", "duration": 8000, "brightness": 100},
    {"mode": "unknown", "bytes": "ÿÿÿÿÿÿÿÿÿÿÿ"},
    {"mode": "unknown", "bytes": "ÿÿÿÿÿÿÿÿÿÿÿ"},
]

_HID_NAME = {int(code): name for name, code in KeyCode.__members__.items()}

_SYSTEM_SUFFIX_TO_ACTION: dict[str, dict[str, Any]] = {
    "090700000000": {"action": "button", "value": "no_action"},
    "090500000000": {"action": "button", "value": "cycle_dpi"},
    "090100000000": {"action": "button", "value": "scroll_up"},
    "090600000000": {"action": "button", "value": "scroll_down"},
    "014000000000": {"action": "button", "value": "g_shift"},
    "016800000000": {"action": "button", "value": "backward_button"},
    "016900000000": {"action": "button", "value": "forward_button"},
}

_MOUSE_BY_SLOT = {
    "g1": {"action": "button", "value": "left_button"},
    "g2": {"action": "button", "value": "right_button"},
    "g3": {"action": "button", "value": "middle_button"},
}

_NO_ACTION = {"action": "button", "value": "no_action"}


def _dpi_index(levels: list[int], dpi: int | None) -> int:
    cleaned = [d for d in levels if d > 0][:5]
    while len(cleaned) < 5:
        cleaned.append(0)
    if dpi is None or dpi <= 0:
        return 1 if cleaned[0] else 0
    for i, level in enumerate(cleaned):
        if level == dpi:
            return i + 1  # OMM uses 1-based dpi_default
    return 1


def _modifier_names(hid_mods: list[int]) -> str:
    rev = {v: k for k, v in MODIFIER_HID.items()}
    parts = [rev[m] for m in hid_mods if m in rev]
    return "+".join(parts)


def _key_action(code: int, modifiers: list[int] | None = None) -> dict[str, Any]:
    name = _HID_NAME.get(code)
    if name is None:
        # Fall back through KEY_HID reverse
        for k, v in KEY_HID.items():
            if v == code:
                name = k
                break
    if name is None:
        return dict(_NO_ACTION)
    return {
        "action": "key",
        "modifier": _modifier_names(modifiers or []),
        "value": name,
    }


def _macro_text_from_sequence(macro: dict[str, Any]) -> str | None:
    seq = macro.get("sequence") or {}
    simple = seq.get("simpleSequence") or {}
    components = simple.get("components") or []
    tokens: list[str] = []
    for comp in components:
        ctype = (comp.get("type") or "").upper()
        if ctype == "KEY_DOWN":
            code = int(comp.get("code") or 0)
            name = _HID_NAME.get(code)
            if name:
                tokens.append(f"+{name}")
        elif ctype == "KEY_UP":
            code = int(comp.get("code") or 0)
            name = _HID_NAME.get(code)
            if name:
                tokens.append(f"-{name}")
        elif ctype == "DELAY":
            # OMM macro text uses sleep opcodes via Macro.macro_bin_from_text;
            # skip explicit delays for v1 simple chords.
            continue
    return " ".join(tokens) if tokens else None


def _card_to_action(card: dict[str, Any] | None, slot_id: str, card_id: str) -> dict[str, Any]:
    button = ghub_button_slot(slot_id) or ""

    if is_builtin_preset_id(card_id):
        suf = suffix(card_id)
        if is_standard_mouse_click(slot_id, card_id):
            return dict(_MOUSE_BY_SLOT.get(button, _NO_ACTION))
        if suf in _SYSTEM_SUFFIX_TO_ACTION:
            return dict(_SYSTEM_SUFFIX_TO_ACTION[suf])
        if suf.startswith("020") and len(suf) >= 4:
            n = int(suf[2:4], 16)
            if 1 <= n <= 24:
                # On g1/g2/g3 the 02xx overload is mouse click (handled above).
                return _key_action(KEY_HID.get(f"f{n}", 0))
        if suf.startswith("01") and len(suf) >= 4:
            code = int(suf[2:4], 16)
            return _key_action(code)
        if suf.startswith("04") and len(suf) >= 4:
            code = int(suf[2:4], 16) + 3
            return _key_action(code)

    if card:
        attr = card.get("attribute")
        macro = card.get("macro") or {}
        if attr == "MACRO_PLAYBACK" or macro:
            mtype = (macro.get("type") or "").upper()
            if mtype == "KEYSTROKE":
                ks = macro.get("keystroke") or {}
                code = int(ks.get("code") or 0)
                mods = list(ks.get("modifiers") or [])
                return _key_action(code, mods)
            if mtype == "SEQUENCE":
                text = _macro_text_from_sequence(macro)
                if text:
                    return {"action": "macro", "value": text}

    return dict(_NO_ACTION)


def _find_card(cards: list[dict[str, Any]], card_id: str) -> dict[str, Any] | None:
    for card in cards:
        if card.get("id") == card_id:
            return card
    return None


def _assignment_map(preset: dict[str, Any]) -> dict[str, str]:
    """Map bare gN[_shifted] → cardId (prefer spectrum/wireless prefix present)."""
    profile = preset.get("profile") or {}
    out: dict[str, str] = {}
    for assignment in profile.get("assignments") or []:
        slot_id = str(assignment.get("slotId") or "")
        card_id = str(assignment.get("cardId") or "")
        if not slot_id or not card_id:
            continue
        button = ghub_button_slot(slot_id)
        if not button:
            continue
        shifted = slot_id.endswith("_shifted")
        key = f"{button}_shifted" if shifted else button
        # Prefer first seen; both spectrum and wireless often duplicate
        out.setdefault(key, card_id)
        out.setdefault(f"__full__{key}", slot_id)
    return out


def ghub_preset_to_omm(preset: dict[str, Any]) -> dict[str, Any]:
    """
    Build OMM JSON suitable for FeatureOnboardProfile.profile_bin_from_json.

    Prefer embedded ommRaw when present (exact device dump). Otherwise reverse
    map G Hub assignments/cards using the Rosetta rules.
    """
    raw = preset.get("ommRaw")
    if isinstance(raw, dict) and raw.get("buttons") and raw.get("dpi_list") is not None:
        out = dict(raw)
        # Keep name in sync with preset display name when present
        if preset.get("name"):
            out["profile_name"] = str(preset["name"])
        return out

    profile = preset.get("profile") or {}
    cards = list(preset.get("cards") or [])
    name = str(preset.get("name") or profile.get("name") or "Preset")

    mouse_card = None
    for card in cards:
        if card.get("attribute") == "MOUSE_SETTINGS":
            mouse_card = card
            break
    ms = (mouse_card or {}).get("mouseSettings") or {}
    dpi_table = ms.get("dpiTable") or {}
    levels = list(dpi_table.get("levels") or [800, 1600, 3200])
    dpi_list = [int(x) for x in levels if int(x) > 0][:5]
    while len(dpi_list) < 5:
        dpi_list.append(0)

    report_rate = int((ms.get("reportRate") or {}).get("value") or 1000)
    default_dpi = int(dpi_table.get("defaultDpi") or dpi_list[0] or 800)
    # G Hub always stores shiftDpi in mouseSettings, even when the profile
    # never uses DPI-shift. Writing a non-zero onboard dpi_shift makes the
    # firmware change DPI while G-Shift is held — which looks like "G-Shift
    # is broken / changing my DPI". Only enable it when a button is actually
    # bound to the dpi_shift action (sniper button).
    raw_shift = dpi_table.get("shiftDpi")

    amap = _assignment_map(preset)
    buttons: list[dict[str, Any]] = []
    buttons_gshift: list[dict[str, Any]] = []

    for g_slot in G502_BUTTONS:
        # normal
        card_id = amap.get(g_slot, f"{PRESET_PREFIX}090700000000")
        full_slot = amap.get(f"__full__{g_slot}", f"g502spectrum_{g_slot}_m1")
        card = _find_card(cards, card_id)
        buttons.append(_card_to_action(card, full_slot, card_id))
        # g-shift
        card_id_s = amap.get(f"{g_slot}_shifted", f"{PRESET_PREFIX}090700000000")
        full_slot_s = amap.get(f"__full__{g_slot}_shifted", f"g502spectrum_{g_slot}_m1_shifted")
        card_s = _find_card(cards, card_id_s)
        buttons_gshift.append(_card_to_action(card_s, full_slot_s, card_id_s))

    uses_dpi_shift = any(
        (b.get("action") == "button" and b.get("value") == "dpi_shift")
        for b in (*buttons, *buttons_gshift)
    )
    if uses_dpi_shift and raw_shift:
        dpi_shift = _dpi_index(dpi_list, int(raw_shift))
    else:
        dpi_shift = 0

    return {
        "profile_name": name,
        "report_rate": report_rate,
        "dpi_default": _dpi_index(dpi_list, default_dpi),
        "dpi_shift": dpi_shift,
        "dpi_list": dpi_list,
        "color": "0xffffff",
        "chunk1": _DEFAULT_CHUNK1,
        "buttons": buttons,
        "buttons_padding": _DEFAULT_BUTTONS_PADDING,
        "buttons_gshift": buttons_gshift,
        "buttons_gshift_padding": _DEFAULT_GSHIFT_PADDING,
        "rgb": list(_DEFAULT_RGB),
        "chunk2": _DEFAULT_CHUNK2,
    }
