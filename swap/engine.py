"""Minimal public engine: list onboard slots and activate one."""

from __future__ import annotations

import contextlib
import io
from dataclasses import dataclass, field

from .device import detect_device_key, hidpp_session
from .devices import get_device
from .ghub import ghub_status


class EngineError(RuntimeError):
    pass


@dataclass(frozen=True)
class SlotInfo:
    index: int
    enabled: bool
    active: bool
    name: str


@dataclass
class EngineStatus:
    device_key: str | None
    device_label: str
    connected: bool
    active_slot: int | None
    slots: list[SlotInfo] = field(default_factory=list)
    ghub_running: bool = False
    ghub_blocking: bool = False
    last_error: str = ""
    note: str = ""


def _slot_name(raw: str | None, slot: int) -> str:
    name = str(raw or "").strip()
    if not name or name.count("\ufffd") >= max(1, len(name) // 2):
        return f"Slot {slot}"
    return name


@contextlib.contextmanager
def _quiet_hidpp():
    with contextlib.redirect_stdout(io.StringIO()):
        yield


class Engine:
    def __init__(self) -> None:
        self.last_error = ""

    def status(self, *, read_names: bool = True, open_attempts: int = 1) -> EngineStatus:
        hub = ghub_status()
        if hub.blocking:
            return EngineStatus(
                None,
                "No device",
                False,
                None,
                ghub_running=hub.running,
                ghub_blocking=True,
                note="Quit Logitech G HUB to use Swap.",
            )

        key = detect_device_key()
        if not key:
            return EngineStatus(
                None,
                "No device",
                False,
                None,
                ghub_running=hub.running,
                note="Connect a supported Logitech G502 mouse.",
            )

        try:
            with hidpp_session(attempts=open_attempts) as session, _quiet_hidpp():
                omm = session.omm
                active = int(omm.current_profile)
                slots = []
                for index in range(1, min(int(omm.num_profiles), 5) + 1):
                    omm.dest_profile = index
                    enabled = bool(omm.profile_enabled)
                    name = f"Slot {index}"
                    if enabled and read_names:
                        meta = omm.profile_bin_to_json(omm.onboard_profile_to_bin())
                        name = _slot_name(meta.get("profile_name"), index)
                    slots.append(SlotInfo(index, enabled, index == active, name))
            self.last_error = ""
            return EngineStatus(
                key,
                get_device(key).label,
                True,
                active,
                slots,
                ghub_running=hub.running,
            )
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            return EngineStatus(
                key,
                get_device(key).label,
                False,
                None,
                ghub_running=hub.running,
                last_error=self.last_error,
                note="Mouse did not answer HID++ discovery.",
            )

    def set_active_slot(self, slot: int) -> None:
        if slot not in range(1, 6):
            raise EngineError(f"Invalid onboard slot: {slot}")
        try:
            with hidpp_session() as session, _quiet_hidpp():
                omm = session.omm
                if slot > int(omm.num_profiles):
                    raise EngineError(f"Mouse only reports {omm.num_profiles} slots")
                omm.dest_profile = slot
                if not bool(omm.profile_enabled):
                    raise EngineError(f"Onboard slot {slot} is disabled")
                if int(omm.current_profile) != slot:
                    omm.current_profile = slot
                if int(omm.current_profile) != slot:
                    if bool(getattr(omm.dev, "wireless_receiver", False)):
                        raise EngineError(
                            "This receiver firmware cannot perform a true onboard slot switch. "
                            "Connect the mouse by USB instead."
                        )
                    raise EngineError(f"Mouse remained on slot {omm.current_profile}")
            self.last_error = ""
        except EngineError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            raise EngineError(str(exc)) from exc
