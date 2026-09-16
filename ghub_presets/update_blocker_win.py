"""Windows G Hub update blocker."""

from __future__ import annotations

import ctypes
import subprocess
import sys
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

UPDATER_SERVICE = "LGHUBUpdaterService"
UPDATER_EXE = "lghub_updater.exe"
SOFTWARE_MANAGER_EXE = "lghub_software_manager.exe"
FIREWALL_RULE_PREFIX = "GHub Preset Toolkit - Block"
HOSTS_FILE = Path(r"C:\Windows\System32\drivers\etc\hosts")

REGISTRY_DISABLE_VALUES: dict[str, int] = {
    "AutoUpdateCheckEnabled": 0,
    "AutoUpdateDownloadEnabled": 0,
    "updateEnabled": 0,
}

SERVICE_START_TYPES = {2: "automatic", 3: "manual", 4: "disabled"}
SERVICE_START_KEYWORDS = {2: "auto", 3: "demand", 4: "disabled"}


@dataclass(frozen=True)
class UpdateBlockStatus:
    platform: str
    is_admin: bool
    install_dir: Path | None
    updater_service_exists: bool
    updater_service_running: bool
    updater_service_start: int | None
    updater_process_running: bool
    firewall_rules: list[str]
    hosts_entries: list[str]
    registry_values: dict[str, dict[str, int | None]]
    state_file: Path | None
    block_active: bool
    block_applied_at: str | None

    def summary_lines(self) -> list[str]:
        lines = [
            f"Platform: Windows",
            f"Administrator: {'yes' if self.is_admin else 'no (required to apply/remove block)'}",
        ]
        if self.install_dir:
            lines.append(f"G Hub install: {self.install_dir}")
        else:
            lines.append("G Hub install: not found under Program Files\\LGHUB")

        if self.updater_service_exists:
            start = SERVICE_START_TYPES.get(self.updater_service_start or 0, "unknown")
            run = "running" if self.updater_service_running else "stopped"
            lines.append(f"LGHUBUpdaterService: {run}, startup={start}")
        else:
            lines.append("LGHUBUpdaterService: not installed")

        lines.append(
            "lghub_updater.exe process: "
            + ("running" if self.updater_process_running else "not running")
        )

        if self.firewall_rules:
            lines.append(f"Toolkit firewall rules: {len(self.firewall_rules)} active")
            for rule in self.firewall_rules:
                lines.append(f"  - {rule}")
            lines.append(
                "WARNING: outbound firewall on lghub_updater.exe also blocks device "
                "depot downloads — re-apply block-updates to remove those rules."
            )
        else:
            lines.append("Toolkit firewall rules: none")

        risky = list_risky_hosts_entries(HOSTS_FILE)
        if self.hosts_entries:
            lines.append(f"Toolkit hosts blocks: {len(self.hosts_entries)}")
            for host in self.hosts_entries:
                suffix = (
                    " (DEVICE DEPOT — remove with unblock-updates)"
                    if host in risky
                    else ""
                )
                lines.append(f"  - {host}{suffix}")
        else:
            lines.append("Toolkit hosts blocks: none")

        if risky:
            lines.append(
                "WARNING: risky depot CDN hosts are still blocked; "
                "run unblock-updates (or re-apply block) to clear them."
            )

        if self.block_active:
            lines.append(f"Toolkit update block: ACTIVE (since {self.block_applied_at or 'unknown'})")
        else:
            lines.append("Toolkit update block: not active")

        for hive_name, values in self.registry_values.items():
            rendered = ", ".join(f"{k}={v}" for k, v in sorted(values.items()))
            lines.append(f"Registry {hive_name}: {rendered or '(no update keys set)'}")

        if self.state_file:
            lines.append(f"State file: {self.state_file}")
        return lines


def _registry_locations() -> tuple[tuple[int, str], ...]:
    import winreg

    return (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Logitech\GHUB"),
        (winreg.HKEY_CURRENT_USER, r"Software\Logitech\GHUB"),
    )


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def ghub_install_dir() -> Path | None:
    for base in (Path(r"C:\Program Files"), Path(r"C:\Program Files (x86)")):
        candidate = base / "LGHUB"
        if (candidate / UPDATER_EXE).is_file():
            return candidate
    return None


def _run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, errors="replace")


def _query_service() -> tuple[bool, bool, int | None]:
    import winreg

    proc = _run_command(["sc.exe", "query", UPDATER_SERVICE])
    if proc.returncode != 0:
        return False, False, None
    running = "RUNNING" in (proc.stdout or "").upper()
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            rf"SYSTEM\CurrentControlSet\Services\{UPDATER_SERVICE}",
        ) as key:
            start, _ = winreg.QueryValueEx(key, "Start")
            return True, running, int(start)
    except OSError:
        return True, running, None


def _set_service_start(start_type: int) -> None:
    keyword = SERVICE_START_KEYWORDS.get(start_type)
    if keyword is None:
        raise RuntimeError(f"Unsupported service start type: {start_type}")
    proc = _run_command(["sc.exe", "config", UPDATER_SERVICE, "start=", keyword])
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"Could not configure {UPDATER_SERVICE}: {detail}")


def _ensure_updater_service_ready() -> list[str]:
    actions: list[str] = []
    exists, running, start = _query_service()
    if not exists:
        return actions
    if start == 4:
        _set_service_start(2)
        actions.append(f"restore {UPDATER_SERVICE} startup=automatic (was disabled)")
    if not running:
        _run_command(["sc.exe", "start", UPDATER_SERVICE])
        actions.append(f"start {UPDATER_SERVICE}")
    return actions


def _read_registry_values() -> dict[str, dict[str, int | None]]:
    import winreg

    result: dict[str, dict[str, int | None]] = {}
    for hive, subkey in _registry_locations():
        hive_name = "HKLM" if hive == winreg.HKEY_LOCAL_MACHINE else "HKCU"
        values: dict[str, int | None] = {}
        try:
            with winreg.OpenKey(hive, subkey) as key:
                for name in REGISTRY_DISABLE_VALUES:
                    try:
                        raw, reg_type = winreg.QueryValueEx(key, name)
                        values[name] = int(raw) if reg_type == winreg.REG_DWORD else None
                    except OSError:
                        values[name] = None
        except OSError:
            for name in REGISTRY_DISABLE_VALUES:
                values[name] = None
        result[hive_name] = values
    return result


def _write_registry_values(values: dict[str, int]) -> list[str]:
    import winreg

    actions: list[str] = []
    for hive, subkey in _registry_locations():
        hive_name = "HKLM" if hive == winreg.HKEY_LOCAL_MACHINE else "HKCU"
        with winreg.CreateKey(hive, subkey) as key:
            for name, value in values.items():
                winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, int(value))
                actions.append(f"registry {hive_name}\\{subkey} {name}={value}")
    return actions


def _restore_registry_values(saved: dict[str, dict[str, int | None]]) -> list[str]:
    import winreg

    actions: list[str] = []
    hive_map = {"HKLM": winreg.HKEY_LOCAL_MACHINE, "HKCU": winreg.HKEY_CURRENT_USER}
    subkey_map = {"HKLM": r"SOFTWARE\Logitech\GHUB", "HKCU": r"Software\Logitech\GHUB"}
    for hive_name, values in saved.items():
        hive = hive_map.get(hive_name)
        subkey = subkey_map.get(hive_name)
        if hive is None or subkey is None:
            continue
        try:
            with winreg.OpenKey(hive, subkey, 0, winreg.KEY_SET_VALUE) as key:
                for name, value in values.items():
                    if value is None:
                        try:
                            winreg.DeleteValue(key, name)
                            actions.append(f"registry delete {hive_name}\\{subkey} {name}")
                        except OSError:
                            pass
                    else:
                        winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, int(value))
                        actions.append(f"registry restore {hive_name}\\{subkey} {name}={value}")
        except OSError:
            if any(v is not None for v in values.values()):
                actions.append(f"registry skip {hive_name}\\{subkey} (key missing)")
    return actions


def _firewall_rule_names() -> list[str]:
    return [
        f"{FIREWALL_RULE_PREFIX} lghub_updater",
        f"{FIREWALL_RULE_PREFIX} lghub_software_manager",
    ]


def _list_firewall_rules() -> list[str]:
    proc = _run_command(["netsh", "advfirewall", "firewall", "show", "rule", "name=all"])
    if proc.returncode != 0:
        return []
    return [name for name in _firewall_rule_names() if name in (proc.stdout or "")]


def _add_firewall_rules(install_dir: Path) -> list[str]:
    actions: list[str] = []
    targets = (install_dir / UPDATER_EXE, install_dir / SOFTWARE_MANAGER_EXE)
    for exe, rule_name in zip(targets, _firewall_rule_names(), strict=True):
        if not exe.is_file():
            continue
        proc = _run_command(
            [
                "netsh", "advfirewall", "firewall", "add", "rule",
                f"name={rule_name}", "dir=out", "action=block",
                f"program={exe}", "enable=yes",
            ],
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"Could not add firewall rule '{rule_name}': {detail}")
        actions.append(f"firewall block outbound {exe.name}")
    return actions


def _remove_firewall_rules() -> list[str]:
    actions: list[str] = []
    for rule_name in _firewall_rule_names():
        proc = _run_command(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={rule_name}"])
        if proc.returncode == 0:
            actions.append(f"firewall remove {rule_name}")
    return actions


def _updater_process_running() -> bool:
    proc = _run_command(["tasklist", "/FI", f"IMAGENAME eq {UPDATER_EXE}", "/NH"])
    text = (proc.stdout or "").lower()
    return UPDATER_EXE.lower() in text and "no tasks" not in text


def get_update_block_status(*, library: Path | None = None) -> UpdateBlockStatus:
    path = state_file(library)
    state = load_state(path)
    exists, running, start = _query_service()
    firewall = _list_firewall_rules()
    hosts = list_hosts_entries(HOSTS_FILE)
    block_active = bool(state and state.get("active")) or bool(firewall) or bool(hosts)
    return UpdateBlockStatus(
        platform="windows",
        is_admin=_is_admin(),
        install_dir=ghub_install_dir(),
        updater_service_exists=exists,
        updater_service_running=running,
        updater_service_start=start,
        updater_process_running=_updater_process_running(),
        firewall_rules=firewall,
        hosts_entries=hosts,
        registry_values=_read_registry_values(),
        state_file=path if path.is_file() else None,
        block_active=block_active,
        block_applied_at=state.get("appliedAt") if state else None,
    )


def apply_update_block(*, library: Path | None = None) -> list[str]:
    if not _is_admin():
        raise RuntimeError(
            "Administrator privileges are required to block G Hub updates.\n"
            "Re-run from an elevated Command Prompt or use "
            "Executables\\windows\\0c Block G Hub Updates.bat"
        )

    install_dir = ghub_install_dir()
    if install_dir is None:
        raise RuntimeError(
            "Could not find G Hub under Program Files\\LGHUB. Is Logitech G Hub installed?"
        )

    path = state_file(library)
    if load_state(path):
        raise RuntimeError(
            f"Update block already active. State file: {path}\n"
            "Run 'ghub-presets unblock-updates' first if you want to re-apply."
        )

    exists, _, original_start = _query_service()
    if not exists:
        raise RuntimeError(f"{UPDATER_SERVICE} is not installed.")

    saved_registry = _read_registry_values()
    actions: list[str] = []
    registry_written = hosts_added = False
    try:
        actions.extend(_ensure_updater_service_ready())
        # Remove legacy outbound blocks on lghub_updater.exe — that also stops
        # device depot downloads and can leave mice Inactive after a G Hub upgrade.
        removed_fw = _remove_firewall_rules()
        if removed_fw:
            actions.extend(removed_fw)
            actions.append(
                "note: no longer firewall-blocks lghub_updater.exe "
                "(it serves device packages as well as app updates)"
            )
        risky = list_risky_hosts_entries(HOSTS_FILE)
        if risky:
            actions.extend(remove_hosts_entries(HOSTS_FILE))
            actions.append(
                "removed legacy hosts blocks that break device depots "
                f"({', '.join(risky)})"
            )
        actions.extend(add_hosts_entries(HOSTS_FILE))
        hosts_added = True
        actions.extend(_write_registry_values(REGISTRY_DISABLE_VALUES))
        registry_written = True
        save_state(
            path,
            {
                "active": True,
                "platform": "windows",
                "appliedAt": utc_now_iso(),
                "installDir": str(install_dir),
                "serviceStartType": original_start,
                "registry": saved_registry,
                "firewallRules": [],
                "hosts": list(UPDATE_HOSTS),
                "toolkitVersion": "1.1.0",
            },
        )
        actions.append(f"state saved: {path}")
        actions.append(
            f"note: {UPDATER_SERVICE} stays automatic/running; "
            "updates blocked via registry + staging hosts "
            "(device depot CDN is not blocked)"
        )
        return actions
    except Exception:
        if registry_written:
            _restore_registry_values(saved_registry)
        if hosts_added:
            remove_hosts_entries(HOSTS_FILE)
        raise


def remove_update_block(*, library: Path | None = None) -> list[str]:
    if not _is_admin():
        raise RuntimeError(
            "Administrator privileges are required to unblock G Hub updates.\n"
            "Re-run from an elevated Command Prompt or use "
            "Executables\\windows\\0d Unblock G Hub Updates.bat"
        )

    path = state_file(library)
    state = load_state(path)
    actions: list[str] = []

    exists, _, current_start = _query_service()
    if exists and state and state.get("serviceStartType") is not None:
        start_type = int(state["serviceStartType"])
        if current_start != start_type:
            _set_service_start(start_type)
            actions.append(
                f"restore {UPDATER_SERVICE} startup={SERVICE_START_TYPES.get(start_type, start_type)}"
            )

    actions.extend(remove_hosts_entries(HOSTS_FILE))
    actions.extend(_remove_firewall_rules())
    if state and state.get("registry"):
        actions.extend(_restore_registry_values(state["registry"]))
    if path.is_file():
        path.unlink()
        actions.append(f"state removed: {path}")
    if not actions:
        actions.append("No toolkit update block state found (nothing to undo).")
    return actions
