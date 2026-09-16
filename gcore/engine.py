"""Public engine API for G."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .converter import ghub_preset_to_omm
from .device import detect_device_key, hidpp_session
from .extract import capture_slot_snapshot, extract_slot_to_library
from .library import (
    LibraryPreset,
    duplicate_preset,
    load_preset,
    remove_preset,
    rename_preset,
    scan_library,
)
from .mutex import ghub_status, quit_ghub
from .paths import (
    active_state_file,
    archive_dir,
    clear_presets_dir,
    extracted_presets_dir,
    presets_dir,
    set_presets_dir,
)


@contextlib.contextmanager
def _quiet_omm():
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink):
        yield


class EngineError(RuntimeError):
    pass


@dataclass
class SlotInfo:
    index: int
    enabled: bool
    active: bool
    name: str


@dataclass
class ArchiveEntry:
    path: Path
    slot: int
    label: str
    modified: float


@dataclass
class EngineStatus:
    device_key: Optional[str]
    device_label: str
    connected: bool
    onboard_mode: Optional[bool]
    active_slot: Optional[int]
    num_profiles: int = 0
    slots: list[SlotInfo] = field(default_factory=list)
    ghub_running: bool = False
    ghub_blocking: bool = False
    library_count: int = 0
    library_path: str = ""
    last_error: str = ""
    note: str = ""


DEFAULT_ONBOARD_SLOTS = (1, 2, 3)
MAX_ONBOARD_SLOTS = 5


def onboard_slot_indices(num_profiles: int) -> tuple[int, ...]:
    """Onboard slot numbers 1..N reported by HID++ feature 0x8100."""
    n = max(0, min(int(num_profiles), MAX_ONBOARD_SLOTS))
    return tuple(range(1, n + 1))


def display_profile_name(raw: str | None, *, slot: int) -> str:
    """Human-readable slot label; garbage device names → Slot N."""
    name = str(raw or "").strip()
    if not name or all(c in "\ufffd?" for c in name):
        return f"Slot {slot}"
    if name.count("\ufffd") >= max(1, len(name) // 2):
        return f"Slot {slot}"
    return name


def profile_name_is_unreliable(name: str) -> bool:
    """True when read-back name cannot be trusted for write verify."""
    cleaned = str(name or "").strip()
    if not cleaned:
        return True
    if all(c in "\ufffd?" for c in cleaned):
        return True
    return cleaned.count("\ufffd") >= max(1, len(cleaned) // 2)


def profile_content_fingerprint(meta: dict) -> tuple[tuple, tuple]:
    """Stable compare key for onboard profile payload (DPI + button map)."""
    dpi = tuple(meta.get("dpi_list") or [])
    buttons = tuple(meta.get("buttons") or [])
    return (dpi, buttons)


def write_blocked_hint() -> str:
    return (
        "Mouse memory was not changed — macOS blocked HID++ writes.\n"
        "• Enable Input Monitoring for G\n"
        "• Turn OFF Logitech G HUB HID Driver (Login Items → Driver Extensions)\n"
        "• Reboot once, then Scan Mouse and try again"
    )


def write_failure_hint(failed: list[str] | None = None) -> str:
    """Tailored recovery steps based on observed write errors."""
    text = "\n".join(failed or [])
    hidfilter_active = False
    if sys.platform == "darwin":
        try:
            from ghub_presets.live_status import mac_hidfilter_state

            state = (mac_hidfilter_state() or "").lower()
            hidfilter_active = "activated" in state or "enabled" in state
        except Exception:
            pass

    if "InvalidArgument" in text or hidfilter_active:
        lines = [
            "The Logitech HID Driver Extension is likely intercepting writes.",
            "• Quit Logitech G Hub completely",
            "• System Settings → General → Login Items & Extensions → Driver Extensions",
            "• Turn OFF “Logitech G HUB HID Driver Extension”",
            "• Reboot, then Scan Mouse and try Replace All again",
        ]
        if hidfilter_active:
            lines.insert(0, "Logitech G HUB HID Driver Extension is active on this Mac.")
        return "\n".join(lines)

    if any(
        s in text.lower()
        for s in ("not permitted", "blocked setreport", "input monitoring", "0xe000")
    ):
        return (
            "macOS blocked HID writes.\n"
            "• Enable Input Monitoring for G (Privacy & Security)\n"
            "• Turn OFF Logitech G HUB HID Driver Extension, then reboot"
        )

    return write_blocked_hint()


def preset_target_slot(
    preset: dict, *, valid_slots: tuple[int, ...] | None = None
) -> int | None:
    """Best-effort slot number encoded in a library preset file."""
    allowed = set(valid_slots or tuple(range(1, MAX_ONBOARD_SLOTS + 1)))
    snap = preset.get("gOnboardSnapshot")
    if isinstance(snap, dict):
        slot = snap.get("sourceSlot")
        if isinstance(slot, int) and slot in allowed:
            return slot
    for key in ("readable", "ommRaw"):
        block = preset.get(key)
        if isinstance(block, dict):
            slot = block.get("onboardSlot")
            if isinstance(slot, int) and slot in allowed:
                return slot
    return None


@dataclass
class BulkAssignPlan:
    assignments: dict[int, Path]
    skipped: list[str]


def build_slot_assignments(
    library: list[LibraryPreset],
    slots: tuple[int, ...] = DEFAULT_ONBOARD_SLOTS,
) -> BulkAssignPlan:
    """Map onboard slots to library files.

    Prefers presets with ``ommRaw`` and an explicit onboard/source slot.
    Extra files (extracts, duplicates) are skipped rather than displacing
    the primary Gaming / Mac F1 / Windows Work set.
    """
    if not library or not slots:
        return BulkAssignPlan(assignments={}, skipped=[])

    slot_set = tuple(slots)
    by_slot: dict[int, Path] = {}
    ranked: list[tuple[int, str, LibraryPreset]] = []

    for item in library:
        try:
            data = load_preset(item.path)
        except (OSError, json.JSONDecodeError, TypeError):
            ranked.append((3, item.path.name.lower(), item))
            continue
        has_omm = isinstance(data.get("ommRaw"), dict) and bool(
            (data.get("ommRaw") or {}).get("buttons")
        )
        target = preset_target_slot(data, valid_slots=slot_set)
        if has_omm and target is not None:
            priority = 0
        elif has_omm:
            priority = 1
        elif target is not None:
            priority = 2
        else:
            priority = 3
        ranked.append((priority, item.path.name.lower(), item))

    ranked.sort(key=lambda row: (row[0], row[1]))
    unassigned: list[LibraryPreset] = []
    for priority, _name, item in ranked:
        try:
            data = load_preset(item.path)
            target = preset_target_slot(data, valid_slots=slot_set)
        except (OSError, json.JSONDecodeError, TypeError):
            unassigned.append(item)
            continue
        if target is not None and target not in by_slot and priority <= 2:
            by_slot[target] = item.path
        else:
            unassigned.append(item)

    for item in unassigned:
        for slot in slot_set:
            if slot not in by_slot:
                by_slot[slot] = item.path
                break

    assigned_paths = set(by_slot.values())
    skipped = [item.name for item in library if item.path.resolve() not in {
        p.resolve() for p in assigned_paths
    }]
    return BulkAssignPlan(assignments=by_slot, skipped=skipped)


class Engine:
    def __init__(self, library_dir: Path | None = None):
        self.library_dir = Path(library_dir).resolve() if library_dir else presets_dir()
        self.last_error = ""
        # Lightspeed cannot change HW profile index; logical slot is UI/source-of-truth.
        self._logical_active_slot: int | None = None
        self._sticky_slot_names: dict[int, str] = {}
        self._load_active_state()

    def _load_active_state(self) -> None:
        path = active_state_file()
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return
        logical = data.get("logical_active_slot")
        if isinstance(logical, int) and logical in range(1, MAX_ONBOARD_SLOTS + 1):
            self._logical_active_slot = logical
        names = data.get("slot_names") or {}
        if isinstance(names, dict):
            out: dict[int, str] = {}
            for key, value in names.items():
                try:
                    idx = int(key)
                except (TypeError, ValueError):
                    continue
                label = str(value or "").strip()
                if label and not profile_name_is_unreliable(label) and label != f"Slot {idx}":
                    out[idx] = label
            self._sticky_slot_names = out

    def _save_active_state(self) -> None:
        path = active_state_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "logical_active_slot": self._logical_active_slot,
                "slot_names": {
                    str(k): v for k, v in sorted(self._sticky_slot_names.items())
                },
            }
            path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass

    def _remember_slot_name(self, slot: int, name: str) -> None:
        label = str(name or "").strip()
        if not label or profile_name_is_unreliable(label) or label == f"Slot {slot}":
            return
        if self._sticky_slot_names.get(slot) != label:
            self._sticky_slot_names[slot] = label
            self._save_active_state()

    def _resolve_slot_name(self, slot: int, decoded: str) -> str:
        name = display_profile_name(decoded, slot=slot)
        if name != f"Slot {slot}":
            self._remember_slot_name(slot, name)
            return name
        sticky = self._sticky_slot_names.get(slot)
        if sticky:
            return sticky
        return name

    def set_library_dir(self, folder: Path | str, *, persist: bool = True) -> Path:
        """Point the engine at a library folder; optionally write presets.dir."""
        path = Path(folder).expanduser().resolve()
        if not path.is_dir():
            raise EngineError(f"Presets folder does not exist: {path}")
        if persist:
            set_presets_dir(path)
        self.library_dir = path
        return path

    def use_default_library(self) -> Path:
        path = clear_presets_dir()
        self.library_dir = path
        return path

    def list_library(self) -> list[LibraryPreset]:
        return scan_library(self.library_dir)

    def duplicate_library_preset(self, path: Path, *, new_name: str | None = None) -> Path:
        try:
            return duplicate_preset(path, new_name=new_name)
        except Exception as exc:  # noqa: BLE001
            raise EngineError(str(exc)) from exc

    def remove_library_preset(self, path: Path) -> None:
        try:
            remove_preset(path)
        except Exception as exc:  # noqa: BLE001
            raise EngineError(str(exc)) from exc

    def rename_library_preset(self, path: Path, new_name: str) -> Path:
        try:
            return rename_preset(path, new_name)
        except Exception as exc:  # noqa: BLE001
            raise EngineError(str(exc)) from exc

    def status(self, *, read_names: bool = True, open_attempts: int = 1) -> EngineStatus:
        """Probe mouse + library.

        ``open_attempts=1`` keeps UI scans from stacking multi-retry HID hangs.
        ``read_names=False`` skips pulling each slot's onboard bin (much faster).
        """
        mutex = ghub_status()
        lib = self.list_library()
        lib_path = str(self.library_dir)
        key = None
        label = "No device"
        connected = False
        onboard = None
        active = None
        num_profiles = 0
        slots: list[SlotInfo] = []
        note = ""

        if mutex.blocking:
            note = "G Hub is running — quit it to use G"
            return EngineStatus(
                device_key=None,
                device_label=label,
                connected=False,
                onboard_mode=None,
                active_slot=None,
                slots=[],
                ghub_running=mutex.running,
                ghub_blocking=mutex.blocking,
                library_count=len(lib),
                library_path=lib_path,
                last_error=self.last_error,
                note=note,
            )

        try:
            key = detect_device_key()
            if not key:
                try:
                    from ghub_presets.pull import connected_pull_device_keys

                    if connected_pull_device_keys():
                        note = "Mouse busy or HID++ blocked — quit G Hub, then Scan Mouse"
                    else:
                        note = "Mouse not found — connect G502 (USB or Lightspeed receiver)"
                except Exception as hid_exc:  # noqa: BLE001
                    note = f"HID unavailable: {hid_exc}"
            else:
                # G assumes onboard mode is always on — every session enforces it.
                with hidpp_session(
                    key, force_onboard=True, attempts=open_attempts
                ) as session, _quiet_omm():
                    omm = session.omm
                    session.leave_mode = True
                    connected = True
                    label = session.device.label
                    onboard = True
                    hw_active = int(omm.current_profile)
                    is_receiver = bool(getattr(omm.dev, "wireless_receiver", False))
                    if is_receiver and self._logical_active_slot is not None:
                        # Sticky logical slot can lie after Replace All overwrote the
                        # HW-active page — heal overlay before reporting "active".
                        try:
                            self._ensure_wireless_bindings(
                                omm,
                                self._logical_active_slot,
                                hw_active,
                                session.device_key or key or "unknown",
                            )
                        except Exception as heal_exc:  # noqa: BLE001
                            self.last_error = str(heal_exc)
                        active = self._logical_active_slot
                    else:
                        active = hw_active
                        if not is_receiver:
                            self._logical_active_slot = None
                    num_profiles = int(omm.num_profiles)
                    for i in onboard_slot_indices(num_profiles):
                        omm.dest_profile = i
                        enabled = bool(omm.profile_enabled)
                        name = f"Slot {i}"
                        if enabled and read_names:
                            try:
                                data = omm.onboard_profile_to_bin()
                                meta = omm.profile_bin_to_json(data)
                                name = self._resolve_slot_name(
                                    i, str(meta.get("profile_name") or "")
                                )
                            except Exception:
                                name = self._resolve_slot_name(i, "")
                        else:
                            name = self._resolve_slot_name(i, name)
                        slots.append(
                            SlotInfo(
                                index=i,
                                enabled=enabled,
                                active=(i == active),
                                name=name,
                            )
                        )
                    self._save_active_state()

        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            note = str(exc)
            if key and not connected:
                msg = str(exc)
                if "not permitted" in msg.lower() or "Input Monitoring" in msg:
                    from .tcc import listen_event_granted

                    if listen_event_granted():
                        hidfilter_note = ""
                        try:
                            from ghub_presets.live_status import mac_hidfilter_state

                            state = mac_hidfilter_state() or ""
                            if "hidfilter" in state.lower() or "logi" in state.lower():
                                if "disabled" in state.lower():
                                    hidfilter_note = (
                                        "\nLogitech HID Driver Extension is disabled but may "
                                        "still be loaded — reboot once, then Scan Mouse."
                                    )
                                else:
                                    hidfilter_note = (
                                        "\nTurn OFF Logitech G HUB HID Driver under "
                                        "Login Items → Driver Extensions, reboot, then Scan."
                                    )
                        except Exception:
                            pass
                        note = (
                            "Mouse found, but macOS blocked HID++ writes.\n"
                            "Input Monitoring is enabled."
                            f"{hidfilter_note}\n"
                            "If you just removed G Hub, reboot once (disabled driver "
                            "extensions often stay loaded until restart), then Scan Mouse."
                        )
                    else:
                        note = (
                            "Mouse found, but macOS blocked HID writes (SetReport).\n"
                            "Enable G under System Settings → Privacy & Security → "
                            "Input Monitoring, then Scan Mouse."
                        )
                else:
                    note = (
                        "Mouse found but HID++ open failed — wake it / unplug-replug, "
                        "then Scan Mouse.\n"
                        f"Detail: {exc}"
                    )

        return EngineStatus(
            device_key=key,
            device_label=label,
            connected=connected,
            onboard_mode=onboard,
            active_slot=active,
            num_profiles=num_profiles if connected else 0,
            slots=slots,
            ghub_running=mutex.running,
            ghub_blocking=mutex.blocking,
            library_count=len(lib),
            library_path=lib_path,
            last_error=self.last_error,
            note=note,
        )

    def set_active_slot(self, slot: int) -> None:
        if slot not in (1, 2, 3, 4, 5):
            raise EngineError(f"Invalid slot {slot}")
        try:
            from ghub_presets.omm.FeatureOnboardProfile import OnboardWriteError

            with hidpp_session(force_onboard=True) as session, _quiet_omm():
                omm = session.omm
                session.leave_mode = True
                if slot > omm.num_profiles:
                    raise EngineError(f"Device only has {omm.num_profiles} slots")
                omm.dest_profile = slot
                if not omm.profile_enabled:
                    omm.profile_enabled = True

                is_receiver = bool(getattr(omm.dev, "wireless_receiver", False))
                hw_before = int(omm.current_profile)

                # Wired / cable: real profile-index switch only (never overlay).
                if not is_receiver:
                    if hw_before != slot:
                        try:
                            omm.current_profile = slot
                        except OnboardWriteError as exc:
                            raise EngineError(str(exc)) from exc
                    after = int(omm.current_profile)
                    if after != slot:
                        raise EngineError(
                            f"Profile switch had no effect (still on slot {after}, wanted {slot}). "
                            "Try unplugging the mouse, then Scan Mouse."
                        )
                    self._logical_active_slot = None
                    self._save_active_state()
                    self.last_error = ""
                    return

                # Dongle: try func 3, then ensure live bindings match the chosen slot.
                switched = False
                if hw_before != slot:
                    try:
                        omm.current_profile = slot
                        if int(omm.current_profile) == slot:
                            switched = True
                    except OnboardWriteError:
                        switched = False

                if switched:
                    self._logical_active_slot = slot
                    self._save_active_state()
                    self.last_error = ""
                    return

                hw_slot = int(omm.current_profile)
                self._ensure_wireless_bindings(omm, slot, hw_slot, session.device_key)
                omm._overlay_switch_target = slot
                self._logical_active_slot = slot
                self._save_active_state()
            self.last_error = ""
        except OnboardWriteError as exc:
            self.last_error = str(exc)
            raise EngineError(str(exc)) from exc
        except EngineError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            raise EngineError(str(exc)) from exc

    def _target_fingerprint_and_preset(
        self, omm: Any, slot: int
    ) -> tuple[tuple[tuple, tuple], dict | None, dict | None, Path | None]:
        """Return (expected_fp, preset, omm_json, library_path) for a bank slot."""
        library = self.list_library()
        device_slots = onboard_slot_indices(omm.num_profiles)
        plan = build_slot_assignments(library, device_slots)
        path = plan.assignments.get(slot)
        preset = None
        omm_json = None
        library_fp = None
        if path is not None and path.is_file():
            preset = load_preset(path)
            omm_json = ghub_preset_to_omm(preset)
            library_fp = profile_content_fingerprint(omm_json)
            name = str(preset.get("name") or omm_json.get("profile_name") or "")
            self._remember_slot_name(slot, name)

        bank_fp = None
        if slot != int(omm.current_profile):
            try:
                bank_fp = omm._slot_content_fingerprint(slot)
            except Exception:  # noqa: BLE001
                bank_fp = None

        expected = library_fp or bank_fp
        if expected is None:
            raise EngineError(
                f"No preset/bindings found for slot {slot}. "
                "Run Replace All from a clean presets folder first."
            )
        return expected, preset, omm_json, path

    def _ensure_wireless_bindings(
        self, omm: Any, slot: int, hw_slot: int, device_key: str
    ) -> None:
        """Make HW-active flash match the chosen slot (Lightspeed overlay)."""
        expected, preset, omm_json, path = self._target_fingerprint_and_preset(omm, slot)
        active_fp = omm._slot_content_fingerprint(hw_slot)
        if active_fp == expected:
            return

        self._archive_slot(omm, hw_slot, device_key=device_key or "unknown")

        wrote = False
        # Prefer bank page copy when target is a different slot (bank still intact).
        if slot != hw_slot:
            try:
                bank_fp = omm._slot_content_fingerprint(slot)
                if bank_fp == expected:
                    pages = omm._read_slot_pages(slot)
                    omm._write_slot_pages(hw_slot, pages)
                    wrote = True
            except Exception:  # noqa: BLE001
                wrote = False

        if not wrote:
            if preset is None or omm_json is None or path is None:
                raise EngineError(
                    f"Wireless overlay needs library preset for slot {slot} "
                    f"(hardware stays on slot {hw_slot})."
                )
            self._write_slot_from_preset(omm, hw_slot, preset, omm_json)

        after_fp = omm._slot_content_fingerprint(hw_slot)
        if after_fp != expected:
            raise EngineError(
                f"Wireless overlay failed: active slot {hw_slot} bindings "
                f"do not match slot {slot} after write"
            )
        # Keep sticky name for the logical slot (bank may still hold the label).
        if preset is not None:
            self._remember_slot_name(
                slot, str(preset.get("name") or omm_json.get("profile_name") or "")
            )

    def assign_preset(self, slot: int, preset_path: Path) -> None:
        if slot not in (1, 2, 3, 4, 5):
            raise EngineError(f"Invalid slot {slot}")
        path = Path(preset_path)
        if not path.is_file():
            raise EngineError(f"Preset not found: {path}")

        try:
            preset = load_preset(path)
            omm_json = ghub_preset_to_omm(preset)

            with hidpp_session(force_onboard=True) as session, _quiet_omm():
                omm = session.omm
                session.leave_mode = True
                if slot > omm.num_profiles:
                    raise EngineError(f"Device only has {omm.num_profiles} slots")
                omm.dest_profile = slot
                if not omm.profile_enabled:
                    omm.profile_enabled = True

                # Archive current slot before overwrite
                self._archive_slot(omm, slot, device_key=session.device_key)

                self._write_slot_from_preset(omm, slot, preset, omm_json)
                self._remember_slot_name(
                    slot, str(preset.get("name") or omm_json.get("profile_name") or "")
                )
                self._save_active_state()
            self.last_error = ""
        except EngineError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            raise EngineError(str(exc)) from exc

    def extract_slot(self, slot: int, *, profile_name: str | None = None) -> Path:
        """Pull a live onboard slot into Extracted/ (keeps Replace All clean)."""
        try:
            dest = extracted_presets_dir(self.library_dir)
            path = extract_slot_to_library(
                slot,
                dest,
                profile_name=profile_name,
            )
            self.last_error = ""
            return path
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            raise EngineError(str(exc)) from exc

    def extract_all_slots(self, slots: tuple[int, ...] | None = None) -> list[Path]:
        """Extract onboard slots into Extracted/ in one HID++ session."""
        from .extract import extract_slots_to_library

        try:
            if slots is None:
                with hidpp_session(force_onboard=True) as session:
                    slots = onboard_slot_indices(session.omm.num_profiles)
            dest = extracted_presets_dir(self.library_dir)
            paths = extract_slots_to_library(slots, dest)
            self.last_error = ""
            return paths
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            raise EngineError(str(exc)) from exc

    def assign_all_from_library(
        self, slots: tuple[int, ...] | None = None
    ) -> tuple[list[tuple[int, str]], list[str], list[str]]:
        """Write library presets onto onboard slots (uses onboardSlot metadata when present).

        Returns (written, skipped_presets, failed_slots).
        """
        library = self.list_library()
        if not library:
            raise EngineError("Library is empty")

        results: list[tuple[int, str]] = []
        failed: list[str] = []
        try:
            from .device import probe_onboard_write

            ok, probe_detail = probe_onboard_write()
            if not ok:
                raise EngineError(
                    f"Cannot write to mouse onboard memory.\n{probe_detail}\n\n{write_failure_hint([probe_detail])}"
                )
            with hidpp_session(force_onboard=True) as session, _quiet_omm():
                omm = session.omm
                session.leave_mode = True
                device_slots = slots or onboard_slot_indices(omm.num_profiles)
                plan = build_slot_assignments(library, device_slots)
                if not plan.assignments:
                    raise EngineError("No presets in library")
                if max(device_slots) > omm.num_profiles:
                    raise EngineError(f"Device only has {omm.num_profiles} slots")
                untouched = [
                    s for s in device_slots if s not in plan.assignments
                ]
                for slot in device_slots:
                    path = plan.assignments.get(slot)
                    if path is None:
                        continue
                    preset = load_preset(path)
                    name = str(preset.get("name") or path.stem)
                    omm_json = ghub_preset_to_omm(preset)
                    omm.dest_profile = slot
                    if not omm.profile_enabled:
                        omm.profile_enabled = True
                    try:
                        self._archive_slot(omm, slot, device_key=session.device_key)
                        self._write_slot_from_preset(omm, slot, preset, omm_json)
                        self._remember_slot_name(slot, name)
                        results.append((slot, name))
                    except EngineError as exc:
                        failed.append(f"Slot {slot} ({name}): {exc}")
                # Dongle HW index stays put; Replace All just rewrote that page
                # (usually Gaming onto slot 1). Re-apply sticky logical preset.
                is_receiver = bool(getattr(omm.dev, "wireless_receiver", False))
                logical = self._logical_active_slot
                if is_receiver and logical is not None and results:
                    try:
                        hw_slot = int(omm.current_profile)
                        self._ensure_wireless_bindings(
                            omm, logical, hw_slot, session.device_key
                        )
                    except EngineError as exc:
                        failed.append(
                            f"Wireless overlay for active slot {logical}: {exc}"
                        )
            if not results:
                detail = "\n".join(failed) if failed else "No presets matched"
                raise EngineError(
                    f"Replace All failed — nothing was written to the mouse.\n{detail}\n\n{write_failure_hint(failed)}"
                )
            self.last_error = ""
            self._save_active_state()
            notes: list[str] = []
            if untouched:
                notes.append(
                    "Unchanged slots (no preset assigned): "
                    + ", ".join(str(s) for s in untouched)
                )
            if failed:
                notes.extend(failed)
            if plan.skipped:
                notes.append(
                    "Skipped library files (not assigned): " + ", ".join(plan.skipped)
                )
            return results, notes, failed
        except EngineError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            raise EngineError(str(exc)) from exc

    def list_archives(self, slot: int | None = None, *, limit: int = 12) -> list[ArchiveEntry]:
        root = archive_dir()
        pattern = f"slot{slot}-*.bin.json" if slot is not None else "slot*-*.bin.json"
        entries: list[ArchiveEntry] = []
        for path in root.glob(pattern):
            try:
                slot_num = int(path.name.split("-", 1)[0].replace("slot", ""))
            except ValueError:
                continue
            label = path.stem
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    name = (
                        data.get("profile_name")
                        or (data.get("omm") or {}).get("profile_name")
                        or data.get("name")
                    )
                    if name:
                        label = f"{name} · {path.stem.split('-', 1)[-1]}"
            except (OSError, json.JSONDecodeError, TypeError):
                pass
            entries.append(
                ArchiveEntry(
                    path=path.resolve(),
                    slot=slot_num,
                    label=label,
                    modified=path.stat().st_mtime,
                )
            )
        entries.sort(key=lambda e: e.modified, reverse=True)
        return entries[:limit]

    def restore_archive(self, archive_path: Path, slot: int) -> None:
        """Restore a slot from an onboard-archive JSON file."""
        if slot not in (1, 2, 3, 4, 5):
            raise EngineError(f"Invalid slot {slot}")
        path = Path(archive_path)
        if not path.is_file():
            raise EngineError(f"Archive not found: {path}")

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise EngineError("Invalid archive file")

            with hidpp_session(force_onboard=True) as session, _quiet_omm():
                omm = session.omm
                session.leave_mode = True
                if slot > omm.num_profiles:
                    raise EngineError(f"Device only has {omm.num_profiles} slots")
                omm.dest_profile = slot
                if not omm.profile_enabled:
                    omm.profile_enabled = True

                self._archive_slot(omm, slot, device_key=session.device_key)

                # Full library preset dumped into archive folder
                if data.get("format") == "lghub-preset-v1" or (
                    "profile" in data and "gOnboardSnapshot" in data
                ):
                    omm_json = ghub_preset_to_omm(data)
                    self._write_slot_from_preset(omm, slot, data, omm_json)
                elif data.get("format") == "g-onboard-archive-v1":
                    snapshot = data.get("gOnboardSnapshot")
                    omm_meta = data.get("omm") if isinstance(data.get("omm"), dict) else {}
                    if isinstance(snapshot, dict):
                        # Force sourceSlot to target so exact restore applies
                        snap = dict(snapshot)
                        snap["sourceSlot"] = slot
                        pseudo = {"gOnboardSnapshot": snap, "ommRaw": omm_meta}
                        self._write_slot_from_preset(
                            omm, slot, pseudo, omm_meta or {"profile_name": ""}
                        )
                    elif omm_meta:
                        pages = omm.profile_bin_from_json(omm_meta)
                        omm.onboard_profile_save(pages)
                    else:
                        raise EngineError("Archive has no restore payload")
                else:
                    # Legacy: raw OMM profile_bin_to_json dump
                    if data.get("disabled"):
                        raise EngineError("Archive is a disabled/empty slot")
                    if "error" in data and "profile_name" not in data:
                        raise EngineError(f"Archive is incomplete: {data.get('error')}")
                    pages = omm.profile_bin_from_json(data)
                    omm.onboard_profile_save(pages)

            self.last_error = ""
        except EngineError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            raise EngineError(str(exc)) from exc

    def quit_ghub(self) -> None:
        quit_ghub()

    def _write_slot_from_preset(
        self,
        omm: Any,
        slot: int,
        preset: dict,
        omm_json: dict,
    ) -> None:
        snapshot = preset.get("gOnboardSnapshot")
        exact_pages: list[bytes] | None = None
        if isinstance(snapshot, dict) and snapshot.get("sourceSlot") == slot:
            raw_pages = snapshot.get("pages")
            expected_page_count = len(omm.page_layout[slot])
            # Snapshots are device-layout-specific (page count differs across
            # G502 wired vs Lightspeed). On mismatch, fall through to ommRaw.
            if isinstance(raw_pages, list) and len(raw_pages) == expected_page_count:
                exact_pages = []
                for page_hex in raw_pages:
                    if not isinstance(page_hex, str):
                        exact_pages = None
                        break
                    page = bytes.fromhex(page_hex)
                    if len(page) != omm.page_size:
                        exact_pages = None
                        break
                    exact_pages.append(page)

        if exact_pages is not None:
            # Raw snapshots are slot-bound because the profile page contains
            # pointers to that slot's macro pages. Restore every page exactly.
            try:
                omm.onboard_profile_save(exact_pages)
            except Exception as exc:  # noqa: BLE001
                from ghub_presets.omm.FeatureOnboardProfile import OnboardWriteError

                if isinstance(exc, OnboardWriteError):
                    raise EngineError(str(exc)) from exc
                raise
            for page_index, (page_number, expected_page) in enumerate(
                zip(omm.page_layout[slot], exact_pages)
            ):
                # Profile pages have CRCs; macro pages intentionally do not.
                actual_page = omm.read_memory_page(
                    page_number, verify=(page_index == 0)
                )
                if bytes(actual_page) != expected_page:
                    raise EngineError(
                        f"Exact write verify failed for memory page {page_number}"
                    )
        else:
            before_meta = omm.profile_bin_to_json(omm.onboard_profile_to_bin())
            before_fp = profile_content_fingerprint(before_meta)
            pages = omm.profile_bin_from_json(omm_json)
            expected_fp = profile_content_fingerprint(omm_json)
            try:
                omm.onboard_profile_save(pages)
            except Exception as exc:  # noqa: BLE001
                from ghub_presets.omm.FeatureOnboardProfile import OnboardWriteError

                if isinstance(exc, OnboardWriteError):
                    raise EngineError(str(exc)) from exc
                raise
            after_meta = omm.profile_bin_to_json(omm.onboard_profile_to_bin())
            after_fp = profile_content_fingerprint(after_meta)
            if after_fp != expected_fp:
                if after_fp == before_fp:
                    raise EngineError(
                        f"Slot {slot}: write had no effect on device memory"
                    )
                raise EngineError(
                    f"Slot {slot}: profile data on mouse does not match preset after write"
                )

        # Best-effort read-back of profile name (some slots/devices return stale/garbage names).
        data = omm.onboard_profile_to_bin()
        written = omm.profile_bin_to_json(data)
        expected = str(omm_json.get("profile_name") or "").strip()
        got = display_profile_name(written.get("profile_name"), slot=slot)
        if (
            expected
            and got
            and expected != got
            and not profile_name_is_unreliable(got)
            and got != f"Slot {slot}"
        ):
            # Non-fatal: onboard button/DPI data was written; name field may lag on device.
            pass

    def _archive_slot(self, omm: Any, slot: int, *, device_key: str = "") -> Path:
        dest_dir = archive_dir()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        out = dest_dir / f"slot{slot}-{stamp}.bin.json"
        try:
            omm.dest_profile = slot
            if omm.profile_enabled:
                data = omm.onboard_profile_to_bin()
                meta = omm.profile_bin_to_json(data)
                snapshot = None
                try:
                    snapshot = capture_slot_snapshot(
                        omm, slot, device_key=device_key or "unknown"
                    )
                except Exception:  # noqa: BLE001
                    snapshot = None
                payload: dict[str, Any] = {
                    "format": "g-onboard-archive-v1",
                    "slot": slot,
                    "archivedAt": datetime.now(timezone.utc).isoformat(),
                    "profile_name": meta.get("profile_name"),
                    "omm": meta,
                }
                if snapshot is not None:
                    payload["gOnboardSnapshot"] = snapshot
            else:
                payload = {
                    "format": "g-onboard-archive-v1",
                    "slot": slot,
                    "archivedAt": datetime.now(timezone.utc).isoformat(),
                    "profile_name": "",
                    "omm": {"profile_name": "", "disabled": True},
                    "disabled": True,
                }
        except Exception as exc:  # noqa: BLE001
            payload = {
                "format": "g-onboard-archive-v1",
                "slot": slot,
                "archivedAt": datetime.now(timezone.utc).isoformat(),
                "error": str(exc),
            }
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        # tiny pause so timestamps don't collide on rapid writes
        time.sleep(0.01)
        return out
