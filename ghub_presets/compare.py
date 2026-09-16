"""Compare onboard pull vs G Hub export (Rosetta stone)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .export import load_preset_file
from .rosetta import describe_builtin_suffix, is_builtin_preset_id, suffix as builtin_suffix


def _fmt_omm(action: dict[str, Any]) -> str:
    kind = action.get("action")
    if kind == "button":
        return action.get("value", "?")
    if kind == "key":
        mod = action.get("modifier") or ""
        key = action.get("value", "")
        return f"key({mod}+{key})" if mod else f"key({key})"
    if kind in ("macro", "macro_unparsed"):
        val = action.get("value") or action.get("bytes", "")
        return f"macro: {val[:60]}{'…' if len(str(val)) > 60 else ''}"
    if kind == "unknown":
        return f"unknown({action.get('bytes', '')!r})"
    return str(action)


def _ghub_assignment(data: dict[str, Any], slot: str, *, shifted: bool = False) -> str:
    cards = {c["id"]: c for c in data.get("cards", [])}
    sid = f"g502spectrum_{slot}_m1" + ("_shifted" if shifted else "")
    for assignment in data["profile"].get("assignments", []):
        if assignment.get("slotId") != sid:
            continue
        card = cards.get(assignment["cardId"], {})
        macro = card.get("macro", {})
        if macro.get("type") == "KEYSTROKE":
            ks = macro["keystroke"]
            return f"{card.get('name', macro.get('actionName', '?'))} (KEYSTROKE)"
        if macro.get("type") == "SEQUENCE":
            return f"{card.get('name', '?')} (SEQUENCE)"
        cid = assignment["cardId"]
        if is_builtin_preset_id(cid):
            return describe_builtin_suffix(builtin_suffix(cid), slot_id=sid)
        return card.get("name") or cid[-12:]
    return "(none)"


def compare_onboard_to_ghub(
    onboard_path: Path,
    ghub_path: Path,
) -> dict[str, Any]:
    onboard = json.loads(onboard_path.read_text(encoding="utf-8"))
    ghub = load_preset_file(ghub_path)
    omm = onboard.get("ommRaw") or onboard
    buttons = omm.get("buttons") or onboard.get("buttons_normal", [])
    buttons_gshift = omm.get("buttons_gshift") or onboard.get("buttons_gshift", [])

    rows = []
    for i in range(max(len(buttons), 11)):
        gh_slot = f"g{i + 1}"
        row: dict[str, Any] = {
            "omm_index": i,
            "ghub_slot": gh_slot,
            "onboard_normal": _fmt_omm(buttons[i]) if i < len(buttons) else None,
            "ghub_normal": _ghub_assignment(ghub, gh_slot, shifted=False),
        }
        if i < len(buttons_gshift):
            row["onboard_gshift"] = _fmt_omm(buttons_gshift[i])
            row["ghub_gshift"] = _ghub_assignment(ghub, gh_slot, shifted=True)
        rows.append(row)

    return {
        "format": "lghub-rosetta-v1",
        "onboard_file": str(onboard_path),
        "ghub_file": str(ghub_path),
        "onboard_profile": omm.get("profile_name") or onboard.get("profile_name"),
        "ghub_profile": ghub.get("name"),
        "mapping_rule": "omm_index N → G Hub slot g(N+1)",
        "rows": rows,
    }


def print_compare_report(report: dict[str, Any]) -> None:
    print(f"\nRosetta: {report.get('onboard_profile')!r} (pull) vs {report.get('ghub_profile')!r} (G Hub)")
    print(f"Rule: {report.get('mapping_rule')}\n")
    print(f"{'idx':>3} {'slot':>4}  {'ONBOARD':<42}  {'G HUB (from device)':<36}")
    print("-" * 90)
    for row in report["rows"]:
        if row["onboard_normal"] is None:
            continue
        print(
            f"{row['omm_index']:3} {row['ghub_slot']:4}  "
            f"{row['onboard_normal']:<42}  {row['ghub_normal']:<36}"
        )
        if row.get("onboard_gshift"):
            print(
                f"     {'':4}  "
                f"shift:{row['onboard_gshift']:<38}  {row.get('ghub_gshift', ''):<36}"
            )
