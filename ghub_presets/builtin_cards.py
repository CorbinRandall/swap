"""Decode Logitech G Hub built-in preset card IDs into importable macro cards."""

from __future__ import annotations

import copy
from typing import Any

from .rosetta import (
    decode_keystroke_suffix,
    describe_builtin_suffix,
    is_builtin_preset_id,
    should_keep_builtin_reference,
    suffix,
)


def builtin_card_from_id(
    card_id: str,
    *,
    slot_id: str | None = None,
    profile_id: str | None = None,
) -> dict[str, Any] | None:
    if not is_builtin_preset_id(card_id):
        return None
    if should_keep_builtin_reference(slot_id or "", card_id):
        return None

    decoded = decode_keystroke_suffix(suffix(card_id), slot_id=slot_id)
    if decoded is None:
        return None

    code, modifiers, label = decoded
    card: dict[str, Any] = {
        "id": card_id,
        "name": label,
        "attribute": "MACRO_PLAYBACK",
        "macro": {
            "type": "KEYSTROKE",
            "actionName": label,
            "keystroke": {"code": code, "modifiers": modifiers},
        },
    }
    if profile_id:
        card["profileId"] = profile_id
    return card


def _card_id_to_slots(preset: dict[str, Any]) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    profile = preset.get("profile", {})
    for assignment in profile.get("assignments", []):
        cid = assignment.get("cardId")
        sid = assignment.get("slotId")
        if cid and sid:
            mapping.setdefault(cid, []).append(sid)
    return mapping


def materialize_builtin_cards(preset: dict[str, Any]) -> int:
    """
    Add MACRO_PLAYBACK card bodies for built-in preset IDs referenced by assignments
    but missing from preset['cards']. Standard g1/g2/g3 mouse clicks and system
    actions stay as built-in references. Returns count of cards added.
    """
    cards: list[dict[str, Any]] = list(preset.get("cards", []))
    known = {c["id"] for c in cards if c.get("id")}
    profile_id = preset.get("profile", {}).get("id")
    id_to_slots = _card_id_to_slots(preset)

    needed: set[str] = set()
    profile = preset.get("profile", {})
    for assignment in profile.get("assignments", []):
        cid = assignment.get("cardId")
        if cid:
            needed.add(cid)
    lighting = profile.get("lightingCard")
    if lighting:
        needed.add(lighting)

    added = 0
    for cid in needed:
        if cid in known or not is_builtin_preset_id(cid):
            continue
        slots = id_to_slots.get(cid, [None])
        card = None
        for slot_id in slots:
            card = builtin_card_from_id(cid, slot_id=slot_id, profile_id=profile_id)
            if card is not None:
                break
        if card is None:
            continue
        cards.append(copy.deepcopy(card))
        known.add(cid)
        added += 1

    preset["cards"] = cards
    return added


def describe_builtin_card(card_id: str, *, slot_id: str | None = None) -> str:
    return describe_builtin_suffix(suffix(card_id), slot_id=slot_id)
