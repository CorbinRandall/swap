"""Supported Logitech G502 HID identities."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceConfig:
    key: str
    pid: int
    hid_index: int
    label: str


DEVICES = {
    "g502-lightspeed-receiver": DeviceConfig(
        "g502-lightspeed-receiver", 0xC539, 0x01, "G502 Lightspeed (receiver)"
    ),
    "g502-lightspeed-usb": DeviceConfig(
        "g502-lightspeed-usb", 0xC08D, 0xFF, "G502 Lightspeed (USB)"
    ),
    "g502-hero": DeviceConfig("g502-hero", 0xC08B, 0xFF, "G502 Hero"),
    "g502": DeviceConfig(
        "g502", 0xC332, 0xFF, "G502 Proteus Spectrum / Gaming Mouse G502 (wired)"
    ),
}

# Prefer a direct USB path when both the cable and receiver enumerate. Direct
# devices support a true profile-index switch; some receiver firmware does not.
DEVICE_PRIORITY = (
    "g502-lightspeed-usb",
    "g502-hero",
    "g502",
    "g502-lightspeed-receiver",
)


def get_device(key: str) -> DeviceConfig:
    return DEVICES[key]
