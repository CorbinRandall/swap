from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from swap.engine import Engine


class EngineTests(unittest.TestCase):
    def test_switches_real_hardware_profile_index(self) -> None:
        omm = MagicMock()
        omm.num_profiles = 3
        omm.profile_enabled = True
        omm.current_profile = 1
        omm.dev.wireless_receiver = False
        session = MagicMock(omm=omm)
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False
        with patch("swap.engine.hidpp_session", return_value=cm):
            Engine().set_active_slot(2)
        self.assertEqual(omm.current_profile, 2)


if __name__ == "__main__":
    unittest.main()
