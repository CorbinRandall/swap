"""macOS G Hub update blocker."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .update_blocker_common import (
    UPDATE_HOSTS,
    add_hosts_entries,
    list_hosts_entries,
    list_risky_hosts_entries,
    load_state,
    remove_hosts_entries,
    save_state,
    state_file,
    utc_now_iso,
)

HOSTS_FILE = Path("/etc/hosts")
GHUB_APP = Path("/Applications/lghub.app")
UPDATER_PLIST = Path("/Library/LaunchDaemons/com.logi.ghub.updater.plist")
UPDATER_LABEL = "com.logi.ghub.updater"
UPDATER_PROCESS = "lghub_updater"
PERIODIC_CHECK_FILE = Path("/Library/Application Support/Logi/LGHUB/periodic_check.json")


@dataclass(frozen=True)
class UpdateBlockStatus:
    platform: str
    is_admin: bool
    install_dir: Path | None
    updater_daemon_installed: bool
    updater_daemon_running: bool
    updater_process_running: bool
    hosts_entries: list[str]
    risky_hosts_entries: list[str]
    periodic_checks_disabled: bool | None
    state_file: Path | None
    block_active: bool
    block_applied_at: str | None

    def summary_lines(self) -> list[str]:
        lines = [
            "Platform: macOS",
            f"Administrator: {'yes' if self.is_admin else 'no (sudo required to apply/remove block)'}",
        ]
        if self.install_dir:
            lines.append(f"G Hub install: {self.install_dir}")
        else:
            lines.append("G Hub install: not found at /Applications/lghub.app")

        if self.updater_daemon_installed:
            run = "running" if self.updater_daemon_running else "not running"
            lines.append(f"{UPDATER_LABEL} launchd daemon: installed, {run}")
        else:
            lines.append(f"{UPDATER_LABEL} launchd daemon: not installed")

        lines.append(
            "lghub_updater process: "
            + ("running" if self.updater_process_running else "not running")
        )

        if self.periodic_checks_disabled is True:
            lines.append("G Hub periodic_check.json: checkPeriodically=false")
        elif self.periodic_checks_disabled is False:
            lines.append("G Hub periodic_check.json: checkPeriodically=true")
        else:
            lines.append("G Hub periodic_check.json: not found")

        if self.hosts_entries:
            lines.append(f"Toolkit hosts blocks: {len(self.hosts_entries)}")
            for host in self.hosts_entries:
                suffix = (
                    " (DEVICE DEPOT — remove with unblock-updates)"
                    if host in self.risky_hosts_entries
                    else ""
                )
                lines.append(f"  - {host}{suffix}")
        else:
            lines.append("Toolkit hosts blocks: none")

        if self.risky_hosts_entries:
            lines.append(
                "WARNING: risky depot CDN hosts are still blocked; "
                "run unblock-updates (or re-apply block) to clear them."
            )

        if self.block_active:
            lines.append(f"Toolkit update block: ACTIVE (since {self.block_applied_at or 'unknown'})")
        else:
            lines.append("Toolkit update block: not active")

        if self.state_file:
            lines.append(f"State file: {self.state_file}")
        return lines


def _is_admin() -> bool:
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


def ghub_install_dir() -> Path | None:
    return GHUB_APP if GHUB_APP.is_dir() else None


def _run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, errors="replace")


def _query_updater_daemon() -> tuple[bool, bool]:
    installed = UPDATER_PLIST.is_file()
    if not installed:
        return False, False

    proc = _run_command(["launchctl", "print", f"system/{UPDATER_LABEL}"])
    if proc.returncode != 0:
        return True, False
    text = (proc.stdout or "").lower()
    running = "state = running" in text or "\tstate = running" in text
    return True, running


def _updater_process_running() -> bool:
    proc = _run_command(["pgrep", "-x", UPDATER_PROCESS])
    return proc.returncode == 0


def _ensure_updater_daemon_ready() -> list[str]:
    """Keep updater daemon loaded — never kill/restart it (that wedges device IPC)."""
    actions: list[str] = []
    installed, running = _query_updater_daemon()
    if not installed:
        return actions
    if running:
        return actions
    # kickstart without -k: start if stopped; do not SIGKILL a live updater
    proc = _run_command(["launchctl", "kickstart", f"system/{UPDATER_LABEL}"])
    if proc.returncode == 0:
        actions.append(f"start system/{UPDATER_LABEL} (no kill)")
    return actions


def _read_periodic_check() -> dict[str, Any] | None:
    if not PERIODIC_CHECK_FILE.is_file():
        return None
    try:
        return json.loads(PERIODIC_CHECK_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_periodic_check(payload: dict[str, Any]) -> None:
    PERIODIC_CHECK_FILE.parent.mkdir(parents=True, exist_ok=True)
    PERIODIC_CHECK_FILE.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )


def _disable_periodic_checks() -> tuple[list[str], dict[str, Any] | None]:
    """Primary macOS update brake: stop scheduled updater checks without blocking depot CDN."""
    actions: list[str] = []
    previous = _read_periodic_check()
    if previous is None:
        payload = {
            "checkPeriodically": False,
            "intervalInSeconds": 86400,
            "nextCheckDueDate": "",
            "downloadAutomatically": False,
            "nextCheckDueTimestamp": "",
        }
    else:
        payload = dict(previous)
        payload["checkPeriodically"] = False
        payload["downloadAutomatically"] = False
    _write_periodic_check(payload)
    actions.append(
        f"set {PERIODIC_CHECK_FILE} checkPeriodically=false downloadAutomatically=false"
    )
    return actions, previous


def _restore_periodic_checks(saved: dict[str, Any] | None) -> list[str]:
    if saved is None:
        return []
    _write_periodic_check(saved)
    return [f"restore {PERIODIC_CHECK_FILE}"]


def get_update_block_status(*, library: Path | None = None) -> UpdateBlockStatus:
    path = state_file(library)
    state = load_state(path)
    installed, running = _query_updater_daemon()
    hosts = list_hosts_entries(HOSTS_FILE)
    risky = list_risky_hosts_entries(HOSTS_FILE)
    periodic = _read_periodic_check()
    periodic_disabled = None if periodic is None else (periodic.get("checkPeriodically") is False)
    block_active = bool(state and state.get("active")) or bool(hosts) or bool(periodic_disabled)
    return UpdateBlockStatus(
        platform="macos",
        is_admin=_is_admin(),
        install_dir=ghub_install_dir(),
        updater_daemon_installed=installed,
        updater_daemon_running=running,
        updater_process_running=_updater_process_running(),
        hosts_entries=hosts,
        risky_hosts_entries=risky,
        periodic_checks_disabled=periodic_disabled,
        state_file=path if path.is_file() else None,
        block_active=block_active,
        block_applied_at=state.get("appliedAt") if state else None,
    )


def apply_update_block(*, library: Path | None = None) -> list[str]:
    if not _is_admin():
        raise RuntimeError(
            "Administrator privileges are required to block G Hub updates.\n"
            "Re-run with sudo or use Executables/mac/0c Block G Hub Updates.command"
        )

    if ghub_install_dir() is None:
        raise RuntimeError("Could not find G Hub at /Applications/lghub.app.")

    path = state_file(library)
    if load_state(path):
        raise RuntimeError(
            f"Update block already active. State file: {path}\n"
            "Run 'ghub-presets unblock-updates' first if you want to re-apply."
        )

    actions: list[str] = []
    hosts_added = False
    periodic_saved: dict[str, Any] | None = None
    try:
        # Clear any legacy risky hosts from older toolkit versions first.
        risky = list_risky_hosts_entries(HOSTS_FILE)
        if risky:
            actions.extend(remove_hosts_entries(HOSTS_FILE))
            actions.append(
                "removed legacy hosts blocks that break device depots "
                f"({', '.join(risky)})"
            )

        actions.extend(_ensure_updater_daemon_ready())
        periodic_actions, periodic_saved = _disable_periodic_checks()
        actions.extend(periodic_actions)
        actions.extend(add_hosts_entries(HOSTS_FILE))
        hosts_added = True
        save_state(
            path,
            {
                "active": True,
                "platform": "macos",
                "appliedAt": utc_now_iso(),
                "installDir": str(GHUB_APP),
                "hosts": list(UPDATE_HOSTS),
                "periodicCheck": periodic_saved,
                "updaterPlist": str(UPDATER_PLIST),
                "toolkitVersion": "1.1.0",
            },
        )
        actions.append(f"state saved: {path}")
        actions.append(
            f"note: {UPDATER_LABEL} stays loaded; device depot CDN is NOT blocked "
            "(blocking it makes mice show Inactive)"
        )
        return actions
    except Exception:
        if hosts_added:
            remove_hosts_entries(HOSTS_FILE)
        if periodic_saved is not None:
            _restore_periodic_checks(periodic_saved)
        raise


def remove_update_block(*, library: Path | None = None) -> list[str]:
    if not _is_admin():
        raise RuntimeError(
            "Administrator privileges are required to unblock G Hub updates.\n"
            "Re-run with sudo or use Executables/mac/0d Unblock G Hub Updates.command"
        )

    path = state_file(library)
    state = load_state(path)
    actions = remove_hosts_entries(HOSTS_FILE)
    if state and "periodicCheck" in state:
        actions.extend(_restore_periodic_checks(state.get("periodicCheck")))
    if path.is_file():
        path.unlink()
        actions.append(f"state removed: {path}")
    if state:
        actions.extend(_ensure_updater_daemon_ready())
    if not actions:
        actions.append("No toolkit update block state found (nothing to undo).")
    return actions
