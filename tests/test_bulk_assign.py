"""Tests for bulk slot assignment planning."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from gcore.engine import build_slot_assignments
from gcore.library import LibraryPreset, write_preset


def _write(root: Path, stem: str, *, slot: int | None = None, omm: bool = False) -> Path:
    data: dict = {
        "format": "lghub-preset-v1",
        "name": stem.replace("_", " "),
        "profile": {},
    }
    if slot is not None:
        data["readable"] = {"onboardSlot": slot}
        data["ommRaw"] = {
            "onboardSlot": slot,
            "profile_name": data["name"],
            "buttons": [{"action": "button", "value": "left_button"}] * 11,
            "dpi_list": [800, 1600, 3200, 0, 0],
        } if omm else {"onboardSlot": slot}
    elif omm:
        data["ommRaw"] = {
            "profile_name": data["name"],
            "buttons": [{"action": "button", "value": "left_button"}] * 11,
            "dpi_list": [800, 1600, 3200, 0, 0],
        }
    path = root / f"{stem}.lghub-preset.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class BulkAssignTests(unittest.TestCase):
    def test_prefers_omm_raw_with_onboard_slot_over_extras(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "Gaming_1", slot=1, omm=True)
            _write(root, "Mac_F1", slot=2, omm=True)
            _write(root, "Windows_Work", slot=3, omm=True)
            _write(root, "Mac_F1_2", slot=2, omm=True)  # extract duplicate
            _write(root, "Zebra", omm=False)

            library = [
                LibraryPreset(name=p.stem, path=p)
                for p in sorted(root.glob("*.lghub-preset.json"))
            ]
            plan = build_slot_assignments(library)
            self.assertEqual(plan.assignments[1].name, "Gaming_1.lghub-preset.json")
            self.assertEqual(plan.assignments[2].name, "Mac_F1.lghub-preset.json")
            self.assertEqual(plan.assignments[3].name, "Windows_Work.lghub-preset.json")
            skipped_joined = " ".join(plan.skipped)
            self.assertIn("Mac_F1_2", skipped_joined)
            self.assertIn("Zebra", skipped_joined)

    def test_three_or_fewer_respects_onboard_slot(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for slot, stem in ((1, "Gaming"), (3, "Mac_F1")):
                path = root / f"{stem}.lghub-preset.json"
                path.write_text(
                    json.dumps(
                        {
                            "format": "lghub-preset-v1",
                            "name": stem,
                            "profile": {},
                            "readable": {"onboardSlot": slot},
                            "ommRaw": {
                                "onboardSlot": slot,
                                "buttons": [{"action": "button", "value": "left_button"}] * 11,
                                "dpi_list": [800, 1600, 0, 0, 0],
                            },
                        }
                    ),
                    encoding="utf-8",
                )
            write_preset(root, {"format": "lghub-preset-v1", "name": "Windows Work", "profile": {}})

            library = [
                LibraryPreset(name=p.stem, path=p)
                for p in sorted(root.glob("*.lghub-preset.json"))
            ]
            plan = build_slot_assignments(library)
            self.assertIn(1, plan.assignments)
            self.assertIn(3, plan.assignments)
            self.assertEqual(plan.assignments[1].name, "Gaming.lghub-preset.json")
            self.assertEqual(plan.assignments[3].name, "Mac_F1.lghub-preset.json")


if __name__ == "__main__":
    unittest.main()
