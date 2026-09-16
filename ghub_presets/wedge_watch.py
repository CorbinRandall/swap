"""Continuous G502 wedge watcher.

Polls the G Hub agent + I/O Registry every few seconds and appends state
transitions to Toolkit Data/diagnostics/wedge_watch.log. The moment the
mouse flips from ACTIVE to the wedge signature (NOT_CONNECTED with no
activeInterfaces while still on USB), it automatically:

  * captures a full snapshot (scripts/capture_g502_snapshot.sh auto-wedge)
  * dumps the surrounding 3 minutes of unified log for USB/HID/lghub_agent
  * records which processes held HID clients on iface 1 in the polls
    immediately before the break

Run manually:   python3 -m ghub_presets.wedge_watch
Install at login: bash scripts/install_wedge_watch.sh
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from .live_status import agent_request

POLL_SECONDS = 3.0
TOOLKIT_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_SCRIPT = TOOLKIT_ROOT / "scripts" / "capture_g502_snapshot.sh"


def _diag_dir() -> Path:
    # GHUB_WEDGE_DIAG lets the LaunchAgent write outside TCC-gated folders
    # (launchd-spawned processes cannot touch ~/Documents).
    override = os.environ.get("GHUB_WEDGE_DIAG")
    if override:
        d = Path(override)
    else:
        from .paths import toolkit_data_dir

        d = toolkit_data_dir() / "diagnostics"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _usb_location() -> str:
    try:
        out = subprocess.check_output(
            ["ioreg", "-p", "IOUSB", "-w0"],
            text=True, stderr=subprocess.DEVNULL, timeout=5,
        )
    except Exception:
        return "?"
    m = re.search(r"Gaming Mouse G502@(\w+)", out)
    return m.group(1) if m else "unplugged"


def _iface1_holders() -> list[str]:
    """Process names with HID user clients on G502 interface 1."""
    try:
        text = subprocess.check_output(
            ["ioreg", "-r", "-n", "Gaming Mouse G502", "-l", "-w0"],
            text=True, stderr=subprocess.DEVNULL, timeout=5,
        )
    except Exception:
        return []
    iface = None
    holders: list[str] = []
    for line in text.splitlines():
        m = re.search(r"IOUSBHostInterface@(\d+)", line)
        if m:
            iface = m.group(1)
        if iface == "1" and "IOUserClientCreator" in line:
            m = re.search(r'pid (\d+), ([^"]+)"', line)
            if m:
                holders.append(f"{m.group(2)}({m.group(1)})")
    return sorted(set(holders))


def _agent_state() -> tuple[str, int]:
    """(state, active_interface_count); AGENT_DOWN if unreachable."""
    try:
        infos = (
            agent_request("GET", "/devices/list", timeout=2.5).get("payload") or {}
        ).get("deviceInfos") or []
    except Exception:
        return "AGENT_DOWN", 0
    if not infos:
        return "NO_DEVICE", 0
    info = infos[0]
    return str(info.get("state") or "?"), len(info.get("activeInterfaces") or [])


def _capture_snapshot(log, label: str) -> None:
    """Full diagnostic snapshot (used for both wedge and recovery moments)."""
    try:
        res = subprocess.run(
            ["bash", str(SNAPSHOT_SCRIPT), label],
            capture_output=True, text=True, timeout=180,
        )
        for out_line in (res.stdout or "").splitlines()[-1:]:
            log(f"  snapshot: {out_line}")
    except Exception as exc:
        log(f"  snapshot failed: {exc}")


def _capture_break(log, recent: list[str]) -> None:
    log(f"*** WEDGE TRANSITION — capturing snapshot ***")
    log("holders in the last polls before break:")
    for line in recent:
        log(f"  {line}")
    try:
        res = subprocess.run(
            ["bash", str(SNAPSHOT_SCRIPT), "auto-wedge"],
            capture_output=True, text=True, timeout=180,
        )
        for out_line in (res.stdout or "").splitlines()[-3:]:
            log(f"  snapshot: {out_line}")
    except Exception as exc:
        log(f"  snapshot failed: {exc}")
    # Unified log around the break (USB/HID/agent senders).
    try:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dump = _diag_dir() / f"break-log-{stamp}.txt"
        with dump.open("w") as fh:
            subprocess.run(
                [
                    "log", "show", "--last", "3m", "--style", "compact",
                    "--predicate",
                    'sender CONTAINS "USB" OR sender CONTAINS "IOHID" '
                    'OR process == "lghub_agent" OR sender CONTAINS "hub"',
                ],
                stdout=fh, stderr=subprocess.STDOUT, timeout=120,
            )
        log(f"  unified log around break: {dump}")
    except Exception as exc:
        log(f"  log dump failed: {exc}")


def main() -> int:
    log_path = _diag_dir() / "wedge_watch.log"

    def log(msg: str) -> None:
        line = f"{_now()} {msg}"
        print(line, flush=True)
        with log_path.open("a") as fh:
            fh.write(line + "\n")

    log(f"watcher started (pid {subprocess.os.getpid()}, poll {POLL_SECONDS}s)")

    last: tuple | None = None
    was_active = False
    recent_holders: list[str] = []  # rolling window of recent holder lines

    while True:
        state, n_ifaces = _agent_state()
        loc = _usb_location()
        holders = _iface1_holders()
        cur = (state, n_ifaces, loc, tuple(holders))

        holder_line = f"state={state} ifaces={n_ifaces} usb={loc} iface1_holders={','.join(holders) or '-'}"
        recent_holders.append(f"{_now()} {holder_line}")
        recent_holders = recent_holders[-10:]

        if cur != last:
            log(f"CHANGE {holder_line}")
            wedged_now = (
                state in ("NOT_CONNECTED", "REQUIRES_ASSISTANCE")
                and n_ifaces == 0
                and loc not in ("unplugged", "?")
            )
            if was_active and wedged_now:
                _capture_break(log, recent_holders)
            if state == "ACTIVE" and not was_active:
                # RECOVERY moment (e.g. first Active after reboot): capture
                # the healthy fingerprint so we can diff it against the
                # wedged one and see what actually changed.
                log("*** RECOVERED to ACTIVE — capturing healthy snapshot ***")
                _capture_snapshot(log, "auto-recovered")
            if state == "ACTIVE":
                was_active = True
            elif loc == "unplugged":
                # Physical unplug is not the wedge; reset the edge detector.
                was_active = False
            last = cur
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
