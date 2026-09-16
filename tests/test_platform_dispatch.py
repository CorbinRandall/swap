"""The branding integration must keep each platform on its existing backend."""
import sys
import types
import unittest
from unittest.mock import Mock, patch

from swap import app


class PlatformDispatchTests(unittest.TestCase):
    def test_mac_uses_g_entry_and_not_windows_preflight(self):
        entry = types.ModuleType("gui.app")
        entry.main = Mock()
        with patch.object(sys, "platform", "darwin"), patch.dict(
            sys.modules, {"gui.app": entry}
        ), patch.object(app, "resolve_ghub_before_launch") as preflight:
            app.main()
        entry.main.assert_called_once_with()
        preflight.assert_not_called()

    def test_windows_keeps_swap_entry_and_preflight(self):
        entry = types.ModuleType("swap.app_win")
        entry.main = Mock()
        with patch.object(sys, "platform", "win32"), patch.dict(
            sys.modules, {"swap.app_win": entry}
        ), patch.object(app, "resolve_ghub_before_launch") as preflight:
            app.main()
        entry.main.assert_called_once_with()
        preflight.assert_called_once_with()
