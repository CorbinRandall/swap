"""Recover a G502 that G Hub shows as Inactive — without rebooting.

Lesson from the 2026-07-17 incident: the mouse worked (pointer, buttons,
Input Monitoring) and macOS enumerated it, but G Hub reported NOT_CONNECTED
with fwInfo=[] — the HID++ handshake on interface 1 never completed. Nothing
G Hub- or device-side fixed it (reinstall, device re-enumeration, replug into
the same port); a reboot did. The wedge lived in kernel USB state for the
*hub chain above the mouse*, which no mouse-level reset touches.

This module reproduces the relevant part of a reboot in software:

  1. Detect the wedge signature (device on USB + agent NOT_CONNECTED + empty
     fwInfo). Anything else is not this failure and gets reported as such.
  2. Restart lghub_agent (cheap, occasionally sufficient).
  3. Re-enumerate the G502 itself (USBDeviceReEnumerate, needs admin).
  4. Escalate: re-enumerate each ancestor hub, nearest first. This tears
     down and rebuilds the kernel state a reboot would clear. Other devices
     on that hub briefly disconnect and come back.
  5. If all levels fail: power-cycle the dock/KVM, then reboot.

The native helper (ghub_presets/native/reenum_usb.c) is compiled on demand
with clang and cached in Toolkit Data.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .live_status import agent_request

LOGI_VID = 0x046D
G502_PID = 0xC332

_NATIVE_SRC = Path(__file__).resolve().parent / "native" / "reenum_usb.c"


# ---------------------------------------------------------------------------
# Detection


@dataclass
class WedgeCheck:
    verdict: str  # ACTIVE | WEDGED | UNPLUGGED | AGENT_DOWN | OTHER
    detail: str = ""
    state: str | None = None
    fw_empty: bool | None = None


def _usb_present() -> bool:
    try:
        out = subprocess.check_output(
            ["ioreg", "-p", "IOUSB", "-w0"], text=True, stderr=subprocess.DEVNULL, timeout=5
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return "Gaming Mouse G502" in out


def check_wedge(timeout: float = 3.0) -> WedgeCheck:
    """Classify the current G502/G Hub state (macOS)."""
    usb = _usb_present()
    if not usb:
        return WedgeCheck("UNPLUGGED", "G502 not on the USB bus — check cable/hub.")
    try:
        infos = (
            agent_request("GET", "/devices/list", timeout=timeout).get("payload") or {}
        ).get("deviceInfos") or []
    except Exception as exc:
        return WedgeCheck("AGENT_DOWN", f"G Hub agent unreachable: {exc}")
    if not infos:
        return WedgeCheck("OTHER", "Agent runs but reports no devices.")
    info = infos[0]
    state = str(info.get("state") or "?")
    fw_empty = not info.get("fwInfo")
    ifaces_empty = not info.get("activeInterfaces")
    if state == "ACTIVE":
        return WedgeCheck("ACTIVE", "Mouse is Active in G Hub.", state, fw_empty)
    # Wedge signature: device is on USB (checked above) but the agent has no
    # live HID++ session. fwInfo may be empty (never handshook) or stale
    # (handshake succeeded earlier, then the link died) — either way
    # activeInterfaces is empty and the state is NOT_CONNECTED.
    if state in ("NOT_CONNECTED", "REQUIRES_ASSISTANCE") and (fw_empty or ifaces_empty):
        return WedgeCheck(
            "WEDGED",
            "Wedge signature: mouse on USB + basic input works, but no live "
            f"HID++ session (state {state}, fwInfo empty={fw_empty}, "
            f"activeInterfaces empty={ifaces_empty}).",
            state,
            fw_empty,
        )
    return WedgeCheck("OTHER", f"Agent state {state} (fwInfo empty={fw_empty}).", state, fw_empty)


def _wait_active(seconds: float, poll: float = 2.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            infos = (
                agent_request("GET", "/devices/list").get("payload") or {}
            ).get("deviceInfos") or []
            if infos and infos[0].get("state") == "ACTIVE":
                return True
        except Exception:
            pass
        time.sleep(poll)
    return False


# ---------------------------------------------------------------------------
# Actions


def _restart_agent(log: list[str]) -> None:
    subprocess.run(
        ["killall", "lghub_agent", "lghub_ui", "lghub_system_tray"],
        stderr=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
    )
    time.sleep(3)
    subprocess.run(["open", "-a", "lghub"], check=False)
    log.append("Restarted G Hub (agent + UI).")


def _native_tool(log: list[str]) -> Path | None:
    """Compile (once) and return the reenum_usb helper path."""
    from .paths import toolkit_data_dir

    cache = toolkit_data_dir() / "bin"
    cache.mkdir(parents=True, exist_ok=True)
    binary = cache / "reenum_usb"
    if binary.exists() and binary.stat().st_mtime >= _NATIVE_SRC.stat().st_mtime:
        return binary
    clang = shutil.which("clang")
    if not clang:
        log.append("clang not found — install Xcode Command Line Tools for hub re-enumeration.")
        return None
    try:
        subprocess.run(
            [
                clang,
                "-o",
                str(binary),
                str(_NATIVE_SRC),
                "-framework",
                "CoreFoundation",
                "-framework",
                "IOKit",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        err = getattr(exc, "stderr", "") or str(exc)
        log.append(f"Failed to build reenum_usb: {err.strip()[:200]}")
        return None
    log.append(f"Built native helper: {binary}")
    return binary


def _run_as_admin(cmd: list[str], log: list[str]) -> bool:
    """Run cmd as root: directly if already root, else GUI admin prompt."""
    if os.geteuid() == 0:
        res = subprocess.run(cmd, capture_output=True, text=True)
    else:
        quoted = " ".join(f"'{c}'" for c in cmd)
        script = f'do shell script "{quoted}" with administrator privileges'
        res = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True
        )
    out = (res.stdout or "") + (res.stderr or "")
    log.append(f"$ {' '.join(cmd)} -> rc={res.returncode} {out.strip()[:160]}")
    return res.returncode == 0


def chain_levels(tool: Path) -> int:
    """Number of nodes (device + ancestor hubs) available to re-enumerate."""
    try:
        out = subprocess.check_output([str(tool), "--list"], text=True, timeout=10)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return 0
    return sum(1 for line in out.splitlines() if line.startswith("level "))


# ---------------------------------------------------------------------------
# Orchestration


@dataclass
class RecoverResult:
    ok: bool
    verdict: str
    steps: list[str] = field(default_factory=list)

    def summary_lines(self) -> list[str]:
        lines = list(self.steps)
        lines.append(f"Result: {self.verdict}")
        return lines


def recover_inactive(
    *,
    max_hub_level: int | None = None,
    settle_seconds: float = 18.0,
    dry_run: bool = False,
) -> RecoverResult:
    """Escalating no-reboot recovery for the Inactive/HID++ wedge."""
    if sys.platform != "darwin":
        return RecoverResult(False, "macOS only.", [])

    log: list[str] = []
    check = check_wedge()
    log.append(f"Diagnosis: {check.verdict} — {check.detail}")

    if check.verdict == "ACTIVE":
        return RecoverResult(True, "Nothing to do — mouse already Active.", log)
    if check.verdict == "UNPLUGGED":
        return RecoverResult(False, "Mouse not on USB; recovery cannot help.", log)

    if dry_run:
        log.append("(dry run — no actions taken)")
        return RecoverResult(False, "Dry run complete.", log)

    # Step 1: agent restart (also fixes AGENT_DOWN).
    _restart_agent(log)
    if _wait_active(settle_seconds):
        return RecoverResult(True, "Fixed by restarting G Hub.", log)
    log.append("Still not Active after agent restart.")

    tool = _native_tool(log)
    if tool is None:
        return RecoverResult(
            False,
            "Cannot escalate without native helper. Power-cycle the dock/KVM "
            "(unplug its power AND upstream cable 30s), then reboot if needed.",
            log,
        )

    levels = chain_levels(tool)
    if levels == 0:
        return RecoverResult(False, "Could not read USB chain.", log)
    top = levels - 1 if max_hub_level is None else min(max_hub_level, levels - 1)

    # Step 2..N: re-enumerate device (level 0), then ancestor hubs.
    for level in range(0, top + 1):
        what = "G502 device" if level == 0 else f"ancestor hub (level {level})"
        log.append(f"Re-enumerating {what}...")
        if not _run_as_admin([str(tool), "--level", str(level)], log):
            log.append(f"Re-enumeration at level {level} failed; trying next level.")
            continue
        time.sleep(6)  # let macOS re-enumerate the subtree
        _restart_agent(log)
        if _wait_active(settle_seconds):
            return RecoverResult(True, f"Fixed by re-enumerating {what}.", log)
        log.append(f"Still not Active after {what}.")

    return RecoverResult(
        False,
        "Software recovery exhausted. Next: power-cycle the dock/KVM (unplug "
        "its power AND upstream cable for 30s), and if that fails, reboot the Mac.",
        log,
    )
