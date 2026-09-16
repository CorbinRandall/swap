"""Dual-transport set_active_slot behavior (wired vs wireless)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from gcore.engine import Engine, EngineError


class SetActiveSlotTests(unittest.TestCase):
    def _session(self, *, receiver: bool, hw: int = 1, num_profiles: int = 5):
        omm = MagicMock()
        omm.num_profiles = num_profiles
        omm.profile_enabled = True
        omm.dev.wireless_receiver = receiver
        omm.current_profile = hw
        omm._overlay_switch_target = None

        def _set_profile(slot):
            if receiver:
                raise Exception("should not set via property on receiver in this test")
            omm.current_profile = slot

        type(omm).current_profile = property(
            lambda self: hw if not hasattr(omm, "_hw") else omm._hw,
            lambda self, val: setattr(omm, "_hw", val),
        )
        omm._hw = hw

        session = MagicMock()
        session.omm = omm
        session.device_key = "test"
        session.leave_mode = False
        return session, omm

    def test_wired_switches_hw_index_and_never_overlays(self) -> None:
        eng = Engine()
        session, omm = self._session(receiver=False, hw=1)

        def _setter(val):
            omm._hw = val

        type(omm).current_profile = property(lambda self: omm._hw, lambda self, v: _setter(v))

        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False

        with patch("gcore.engine.hidpp_session", return_value=cm), patch(
            "gcore.engine._quiet_omm", return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock())
        ), patch.object(eng, "_ensure_wireless_bindings") as overlay:
            eng.set_active_slot(2)
            self.assertEqual(omm._hw, 2)
            overlay.assert_not_called()
            self.assertIsNone(eng._logical_active_slot)

    def test_wireless_overlays_when_func3_fails(self) -> None:
        eng = Engine()
        session, omm = self._session(receiver=True, hw=1)

        def _get(_self=None):
            return omm._hw

        def _set(_self, val):
            from ghub_presets.omm.FeatureOnboardProfile import OnboardWriteError

            raise OnboardWriteError("wireless receiver cannot activate")

        type(omm).current_profile = property(_get, _set)
        omm._hw = 1
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False
        with patch("gcore.engine.hidpp_session", return_value=cm), patch(
            "gcore.engine._quiet_omm", return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock())
        ), patch.object(eng, "_ensure_wireless_bindings") as overlay:
            eng.set_active_slot(2)
            overlay.assert_called_once()
            self.assertEqual(eng._logical_active_slot, 2)

    def test_wireless_same_hw_index_still_overlays(self) -> None:
        """Selecting slot 1 while HW is 1 must still rewrite bindings."""
        eng = Engine()
        session, omm = self._session(receiver=True, hw=1)
        type(omm).current_profile = property(lambda self: 1, lambda self, v: None)
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False
        with patch("gcore.engine.hidpp_session", return_value=cm), patch(
            "gcore.engine._quiet_omm", return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock())
        ), patch.object(eng, "_ensure_wireless_bindings") as overlay:
            eng.set_active_slot(1)
            overlay.assert_called_once()
            args = overlay.call_args[0]
            self.assertEqual(args[1], 1)  # requested slot
            self.assertEqual(args[2], 1)  # hw slot
            self.assertEqual(eng._logical_active_slot, 1)


if __name__ == "__main__":
    unittest.main()
