from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from swap.choices import acquire_startup_status, active_choice_index, preset_choices
from swap.engine import EngineStatus, SlotInfo


class ChoiceTests(unittest.TestCase):
    def test_enabled_slots_and_active_index(self) -> None:
        status = EngineStatus(
            "g502",
            "G502",
            True,
            2,
            [SlotInfo(1, True, False, "Gaming"), SlotInfo(2, True, True, "Work")],
        )
        choices = preset_choices(status)
        self.assertEqual([item.label for item in choices], ["Gaming", "Work"])
        self.assertEqual(active_choice_index(status, choices), 1)

    def test_startup_retries_without_user_action(self) -> None:
        unavailable = EngineStatus(None, "No device", False, None)
        connected = EngineStatus("g502", "G502", True, 1, [SlotInfo(1, True, True, "Gaming")])
        engine = MagicMock()
        engine.status.side_effect = [unavailable, connected]
        with patch("swap.choices.time.sleep"):
            self.assertIs(acquire_startup_status(engine), connected)
        self.assertEqual(engine.status.call_count, 2)


if __name__ == "__main__":
    unittest.main()
