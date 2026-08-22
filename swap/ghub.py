"""Keep Logitech G HUB from competing with Swap for HID++ access."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class GHubStatus:
    running: bool
    blocking: bool
    process_names: tuple[str, ...]


WINDOWS_BLOCKING = ("lghub.exe", "lghub_agent.exe", "lghub_ui.exe")
WINDOWS_ALL = WINDOWS_BLOCKING + ("lghub_updater.exe",)
MAC_BLOCKING = ("lghub_agent", "lghub_system_tray", "lghub_ui", "lghub_sso_handler")


def _windows_process_names() -> tuple[str, ...]:
    script = (
        "Get-CimInstance Win32_Process | Where-Object {$_.Name -like 'lghub*.exe'} "
        "| Select-Object -ExpandProperty Name | ConvertTo-Json -Compress"
    )
    try:
        raw = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", script],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        data = json.loads(raw) if raw else []
        if isinstance(data, str):
            data = [data]
        return tuple(sorted({str(name).lower() for name in data}))
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
        return ()


def _mac_process_names() -> tuple[str, ...]:
    try:
        raw = subprocess.check_output(
            ["ps", "-ax", "-o", "command="], text=True, stderr=subprocess.DEVNULL
        )
    except (OSError, subprocess.CalledProcessError):
        return ()
    names = set()
    for line in raw.splitlines():
        lower = line.lower()
        for name in (*MAC_BLOCKING, "lghub_updater"):
            if name in lower and "swap" not in lower:
                names.add(name)
    return tuple(sorted(names))


def ghub_status() -> GHubStatus:
    names = _windows_process_names() if sys.platform == "win32" else _mac_process_names()
    blocking_set = set(WINDOWS_BLOCKING if sys.platform == "win32" else MAC_BLOCKING)
    return GHubStatus(bool(names), bool(blocking_set.intersection(names)), names)


def require_ghub_idle() -> None:
    status = ghub_status()
    if status.blocking:
        raise RuntimeError(
            "Logitech G HUB is running and owns the mouse. Quit it before using Swap."
        )


def quit_ghub() -> None:
    if sys.platform == "win32":
        for name in WINDOWS_BLOCKING:
            subprocess.run(
                ["taskkill", "/IM", name, "/F"], capture_output=True, check=False
            )
        return
    subprocess.run(
        ["osascript", "-e", 'tell application "lghub" to quit'],
        capture_output=True,
        check=False,
    )
    for name in MAC_BLOCKING:
        subprocess.run(["killall", name], capture_output=True, check=False)


def _prompt_to_quit() -> bool:
    message = (
        "Logitech G HUB is running. Swap needs exclusive access to the mouse.\n\n"
        "Quit G HUB and open Swap?"
    )
    if sys.platform == "win32":
        import ctypes

        return ctypes.windll.user32.MessageBoxW(
            0, message, "Swap", 0x04 | 0x30 | 0x40000
        ) == 6
    script = (
        'display dialog "Logitech G HUB is running. Quit it and open Swap?" '
        'with title "Swap" with icon caution buttons {"Cancel", "Quit G HUB"} '
        'default button "Quit G HUB" cancel button "Cancel"'
    )
    return subprocess.run(["osascript", "-e", script], check=False).returncode == 0


def resolve_ghub_before_launch(timeout: float = 20.0) -> None:
    if not ghub_status().blocking:
        return
    if not _prompt_to_quit():
        raise SystemExit(0)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        quit_ghub()
        if not ghub_status().blocking:
            return
        time.sleep(0.4)
    raise SystemExit("Could not quit Logitech G HUB. Quit it manually, then reopen Swap.")
