"""G502 wired and Lightspeed device metadata."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceConfig:
    key: str
    model_id: str
    slot_prefix: str
    pid: int
    hid_index: int
    label: str


DEVICES: dict[str, DeviceConfig] = {
    "g502": DeviceConfig(
        key="g502",
        model_id="g502_spectrum",
        slot_prefix="g502spectrum_",
        pid=0xC332,
        hid_index=0xFF,
        label="G502 Proteus Spectrum / Gaming Mouse G502 (wired)",
    ),
    "g502-hero": DeviceConfig(
        key="g502-hero",
        model_id="g502_spectrum",
        slot_prefix="g502spectrum_",
        pid=0xC08B,
        hid_index=0xFF,
        label="G502 Hero (wired, alternate PID)",
    ),
    "g502wireless": DeviceConfig(
        key="g502wireless",
        model_id="g502wireless",
        slot_prefix="g502wireless_",
        pid=0xC08D,
        hid_index=0xFF,
        label="G502 Lightspeed (USB cable)",
    ),
    "g502wireless-dongle": DeviceConfig(
        key="g502wireless-dongle",
        model_id="g502wireless",
        slot_prefix="g502wireless_",
        pid=0xC539,
        hid_index=0x01,
        label="G502 Lightspeed (wireless receiver)",
    ),
}

SLOT_PREFIX_BY_MODEL = {
    cfg.model_id: cfg.slot_prefix for cfg in DEVICES.values()
}

# G Hub settings.db uses alternate modelId spellings.
MODEL_ID_ALIASES: dict[str, str] = {
    "g502_wireless": "g502wireless",
    "g502_hero": "g502_spectrum",
}

# G502 button slots in G Hub assignment naming (g1..g11 + shifted layers).
# OMM onboard profile stores buttons in this same order (index 0 = g1, … index 10 = g11).
G502_BUTTONS = [f"g{i}" for i in range(1, 12)]

G502_SLOT_LABELS: dict[str, str] = {
    "g1": "Left click",
    "g2": "Right click",
    "g3": "Middle click",
    "g4": "Back thumb",
    "g5": "Forward thumb",
    "g6": "DPI Shift button",
    "g7": "DPI Down",
    "g8": "DPI Up",
    "g9": "Sniper / profile button",
    "g10": "Scroll tilt left (G Hub only; often not in onboard)",
    "g11": "Scroll tilt right (G Hub only; often not in onboard)",
}


def omm_index_to_ghub_slot(index: int) -> str:
    if 0 <= index < len(G502_BUTTONS):
        return G502_BUTTONS[index]
    raise IndexError(f"OMM button index {index} out of range for G502 (0-10)")


def get_device(key: str) -> DeviceConfig:
    if key not in DEVICES:
        known = ", ".join(sorted(DEVICES))
        raise ValueError(f"Unknown device {key!r}. Known: {known}")
    return DEVICES[key]


def slot_prefix_for_model(model_id: str) -> str | None:
    normalized = MODEL_ID_ALIASES.get(model_id, model_id)
    return SLOT_PREFIX_BY_MODEL.get(normalized)


def remap_slot_id(slot_id: str, from_prefix: str, to_prefix: str) -> str:
    if slot_id.startswith(from_prefix):
        return to_prefix + slot_id[len(from_prefix) :]
    return slot_id
