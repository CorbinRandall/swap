"""Optional G Hub update blocker. See SECURITY.md."""

from __future__ import annotations

import sys
from pathlib import Path

if sys.platform == "win32":
    from .update_blocker_win import (
        UpdateBlockStatus,
        apply_update_block,
        get_update_block_status,
        ghub_install_dir,
        remove_update_block,
    )
elif sys.platform == "darwin":
    from .update_blocker_mac import (
        UpdateBlockStatus,
        apply_update_block,
        get_update_block_status,
        ghub_install_dir,
        remove_update_block,
    )
else:

    class UpdateBlockStatus:  # type: ignore[no-redef]
        def summary_lines(self) -> list[str]:
            return ["Update blocking is only implemented on Windows and macOS."]

    def get_update_block_status(*, library: Path | None = None) -> UpdateBlockStatus:
        return UpdateBlockStatus()

    def apply_update_block(*, library: Path | None = None) -> list[str]:
        raise RuntimeError("G Hub update blocking is only supported on Windows and macOS.")

    def remove_update_block(*, library: Path | None = None) -> list[str]:
        raise RuntimeError("G Hub update blocking is only supported on Windows and macOS.")

    def ghub_install_dir() -> Path | None:
        return None
