"""Platform entry point for Swap."""

from __future__ import annotations

import sys

from swap.ghub import resolve_ghub_before_launch


def main() -> None:
    if sys.platform == "darwin":
        # Preserve the proven G app's launch checks and Mac backend.
        from gui.app import main as run_mac

        run_mac()
        return
    resolve_ghub_before_launch()
    if sys.platform == "win32":
        from swap.app_win import main as run
    else:
        raise SystemExit("Swap supports Windows and macOS.")
    run()


if __name__ == "__main__":
    main()
