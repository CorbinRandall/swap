"""macOS TCC helpers for Input Monitoring (ListenEvent)."""

from __future__ import annotations

import sys

BUNDLE_ID = "io.bytecode.g-onboard"


def listen_event_auth(client: str = BUNDLE_ID) -> int | None:
    """Return TCC auth_value (0=denied, 2=allowed) or None if unreadable."""
    if sys.platform != "darwin":
        return None
    import sqlite3

    try:
        row = sqlite3.connect("/Library/Application Support/com.apple.TCC/TCC.db").execute(
            "SELECT auth_value FROM access "
            "WHERE service='kTCCServiceListenEvent' AND client=? LIMIT 1",
            (client,),
        ).fetchone()
    except sqlite3.Error:
        return None
    return int(row[0]) if row else None


def listen_event_granted(client: str = BUNDLE_ID) -> bool:
    return listen_event_auth(client) == 2
