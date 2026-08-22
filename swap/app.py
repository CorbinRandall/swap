"""Platform entry point for Swap."""

from __future__ import annotations

import sys

from swap.ghub import resolve_ghub_before_launch


def main() -> None:
    resolve_ghub_before_launch()
    if sys.platform == "win32":
        from swap.app_win import main as run
    elif sys.platform == "darwin":
        from swap.app_mac import main as run
    else:
        raise SystemExit("Swap supports Windows and macOS.")
    run()


if __name__ == "__main__":
    main()
