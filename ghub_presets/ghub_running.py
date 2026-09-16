"""Detect and stop Logitech G Hub (including menu-bar background processes)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass


# macOS `ps comm=` is only 15 chars — match full executable path, not workspace labels.
GHUB_EXECUTABLE_RE = re.compile(
    r"lghub\.app[\\/]Contents[\\/]MacOS[\\/]lghub",
    re.IGNORECASE,
)
WINDOWS_GHUB_RE = re.compile(r"lghub(?:_[a-z0-9]+)?\.exe", re.IGNORECASE)

# Processes that hold settings.db / sync profiles (must be stopped).
MACOS_BLOCKING_NAMES = (
    "lghub_agent",
    "lghub_system_tray",
    "lghub_ui",
    "lghub_sso_handler",
)

MACOS_QUIT_NAMES = MACOS_BLOCKING_NAMES + ("lghub_updater",)

WINDOWS_QUIT_NAMES = (
    "lghub.exe",
    "lghub_agent.exe",
    "lghub_ui.exe",
    "lghub_updater.exe",
)


@dataclass(frozen=True)
class GHubProcess:
    pid: int
    command: str


def _is_ghub_process_command(command: str) -> bool:
    lower = command.lower()
    if "extension-host" in lower or "cursor helper" in lower:
        return False
    if sys.platform == "win32":
        return bool(WINDOWS_GHUB_RE.search(command))
    return bool(GHUB_EXECUTABLE_RE.search(command))


def _process_name(command: str) -> str:
    if sys.platform == "win32":
        return command.lower()
    match = GHUB_EXECUTABLE_RE.search(command)
    if not match:
        return ""
    tail = command[match.start() :]
    # e.g. .../MacOS/lghub_agent.app/.../lghub_agent
    parts = tail.replace("\\", "/").split("/")
    for part in reversed(parts):
        if part.startswith("lghub"):
            return part.split(".")[0].lower()
    return ""


def _is_blocking_process(command: str) -> bool:
    if not _is_ghub_process_command(command):
        return False
    name = _process_name(command)
    if sys.platform == "win32":
        return "lghub" in name and "updater" not in name
    return name in {n.lower() for n in MACOS_BLOCKING_NAMES} or (
        "lghub_ui" in name or "lghub_agent" in name or "lghub_system_tray" in name
    )


def _filter_ghub_processes(
    processes: list[GHubProcess],
    *,
    blocking_only: bool,
) -> list[GHubProcess]:
    if not blocking_only:
        return processes
    return [proc for proc in processes if _is_blocking_process(proc.command)]


def _list_windows_ghub_processes() -> list[GHubProcess]:
    """Enumerate G Hub processes on Windows (Win11-safe; wmic is deprecated)."""
    names = ", ".join(f"'{name}'" for name in WINDOWS_QUIT_NAMES)
    ps_script = (
        f"$names = @({names}); "
        "$procs = foreach ($n in $names) { "
        "Get-CimInstance Win32_Process -Filter \"Name='$n'\" "
        "| Select-Object ProcessId, CommandLine, Name }; "
        "$procs | ConvertTo-Json -Compress"
    )
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", ps_script],
            stderr=subprocess.DEVNULL,
            text=True,
            errors="replace",
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        out = ""

    processes: list[GHubProcess] = []
    if out:
        try:
            data = json.loads(out)
            if isinstance(data, dict):
                data = [data]
            for item in data:
                pid = int(item.get("ProcessId", 0))
                command = item.get("CommandLine") or item.get("Name") or ""
                if pid and _is_ghub_process_command(command):
                    processes.append(GHubProcess(pid=pid, command=command))
        except (json.JSONDecodeError, TypeError, ValueError):
            processes = []

    if processes:
        return processes

    for image_name in WINDOWS_QUIT_NAMES:
        try:
            out = subprocess.check_output(
                ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"],
                stderr=subprocess.DEVNULL,
                text=True,
                errors="replace",
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
        for line in out.splitlines():
            line = line.strip().strip('"')
            if not line or line.startswith("INFO:"):
                continue
            parts = line.split('","')
            if len(parts) < 2:
                continue
            image = parts[0].strip('"')
            try:
                pid = int(parts[1].strip('"'))
            except ValueError:
                continue
            processes.append(GHubProcess(pid=pid, command=image))
    return processes


def list_ghub_processes(*, blocking_only: bool = False) -> list[GHubProcess]:
    if sys.platform == "win32":
        return _filter_ghub_processes(
            _list_windows_ghub_processes(),
            blocking_only=blocking_only,
        )

    processes: list[GHubProcess] = []
    try:
        out = subprocess.check_output(["ps", "-ax", "-o", "pid=,command="], text=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []

    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        command = parts[1]
        if _is_ghub_process_command(command):
            processes.append(GHubProcess(pid=pid, command=command))
    return _filter_ghub_processes(processes, blocking_only=blocking_only)


def is_ghub_running() -> bool:
    return bool(list_ghub_processes(blocking_only=True))


def list_all_ghub_processes() -> list[GHubProcess]:
    return list_ghub_processes(blocking_only=False)


def quit_ghub() -> list[str]:
    """Try to quit G Hub UI and kill background agents. Returns actions taken."""
    actions: list[str] = []

    if sys.platform == "win32":
        for name in WINDOWS_QUIT_NAMES:
            try:
                subprocess.run(
                    ["taskkill", "/IM", name, "/F"],
                    capture_output=True,
                    check=False,
                )
                actions.append(f"taskkill {name}")
            except FileNotFoundError:
                pass
        return actions

    subprocess.run(
        ["osascript", "-e", 'tell application "lghub" to quit'],
        capture_output=True,
        check=False,
    )
    actions.append("osascript quit lghub")

    for name in MACOS_QUIT_NAMES:
        proc = subprocess.run(["killall", name], capture_output=True, text=True)
        if proc.returncode == 0:
            actions.append(f"killall {name}")

    time.sleep(0.5)
    for proc in list_ghub_processes():
        subprocess.run(["kill", "-9", str(proc.pid)], capture_output=True, check=False)
        actions.append(f"kill -9 {proc.pid}")

    return actions


def ensure_ghub_stopped(
    *,
    timeout: float = 20.0,
    quit_first: bool = False,
) -> list[str]:
    """
    Block until no G Hub processes remain.
    If quit_first, attempt osascript/killall first (needed for menu-bar agent).
    """
    actions: list[str] = []
    if quit_first:
        actions.extend(quit_ghub())

    deadline = time.monotonic() + timeout
    last_kill = 0.0
    while time.monotonic() < deadline:
        if not is_ghub_running():
            from .db import prepare_settings_db

            prepare_settings_db()
            remaining = list_ghub_processes(blocking_only=False)
            non_blocking = [p for p in remaining if not _is_blocking_process(p.command)]
            if non_blocking:
                actions.append(
                    f"note: {len(non_blocking)} background updater process(es) still running (OK)"
                )
            return actions
        time.sleep(0.4)
        if quit_first and time.monotonic() - last_kill > 2.0:
            actions.extend(quit_ghub())
            last_kill = time.monotonic()

    remaining = list_ghub_processes(blocking_only=True)
    lines = [f"  pid {p.pid}: {p.command[:120]}" for p in remaining]
    raise RuntimeError(
        "Logitech G Hub agent/UI is still running (menu bar or background).\n"
        + "\n".join(lines)
        + "\n\nUse the menu bar icon → Quit Logitech G HUB, or Activity Monitor →"
        " force quit lghub_agent and lghub_system_tray."
    )


def require_ghub_stopped(*, quit_first: bool = False) -> None:
    if is_ghub_running():
        procs = list_ghub_processes()
        hint = "\n".join(f"  pid {p.pid}: {p.command[:100]}" for p in procs)
        extra = " Run: ghub-presets quit-ghub" if not quit_first else ""
        if quit_first:
            ensure_ghub_stopped(quit_first=True)
            return
        raise RuntimeError(
            "Logitech G Hub is running (including background menu-bar processes).\n"
            f"{hint}\n\nQuit G Hub completely before continuing.{extra}"
        )
