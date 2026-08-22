"""Small, platform-neutral view model for Swap's preset selector."""

from __future__ import annotations

from dataclasses import dataclass
import time

from .engine import Engine, EngineStatus


@dataclass(frozen=True)
class PresetChoice:
    slot: int
    label: str


def preset_choices(status: EngineStatus) -> list[PresetChoice]:
    """Return enabled onboard presets in the order reported by the mouse."""
    return [
        PresetChoice(slot=info.index, label=info.name or f"Preset {info.index}")
        for info in status.slots
        if info.enabled
    ]


def active_choice_index(status: EngineStatus, choices: list[PresetChoice]) -> int:
    """Return the selected dropdown index, defaulting to the first preset."""
    for index, choice in enumerate(choices):
        if choice.slot == status.active_slot:
            return index
    return 0


def connection_message(status: EngineStatus) -> str:
    if status.ghub_blocking:
        return "Quit Logitech G HUB, then reopen Swap."
    if not status.connected:
        detail = f"{status.note}\n{status.last_error}".lower()
        if "did not answer discovery" in detail:
            return "Mouse did not answer. Move or reconnect it, then reopen Swap."
        return "Mouse unavailable. Connect it, then reopen Swap."
    if not preset_choices(status):
        return "No enabled onboard presets were found."
    return status.device_label


def acquire_startup_status(engine: Engine) -> EngineStatus:
    """Acquire the mouse through transient USB/HID startup races without user action."""
    status: EngineStatus | None = None
    for delay in (0.0, 0.3, 0.8):
        if delay:
            time.sleep(delay)
        status = engine.status(read_names=True, open_attempts=3)
        if status.connected or status.ghub_blocking:
            return status
    assert status is not None
    return status
