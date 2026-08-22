"""Short-lived HID++ sessions for supported mice."""

from __future__ import annotations

import contextlib
import io
import time
from dataclasses import dataclass
from typing import Any, Iterator

from .devices import DEVICE_PRIORITY, DeviceConfig, get_device
from .ghub import require_ghub_idle


@dataclass
class OpenSession:
    device_key: str
    device: DeviceConfig
    omm: Any
    leave_onboard: bool = True


def connected_device_keys() -> list[str]:
    import hid

    pids = {
        int(item.get("product_id") or 0)
        for item in hid.enumerate()
        if item.get("vendor_id") == 0x046D
    }
    return [key for key in DEVICE_PRIORITY if get_device(key).pid in pids]


def detect_device_key() -> str | None:
    keys = connected_device_keys()
    return keys[0] if keys else None


def _open_omm(device_key: str):
    from .hidpp.FeatureOnboardProfile import FeatureOnboardProfile
    from .hidpp.LogiHPP20 import LogiHPP20

    device = get_device(device_key)
    indices = [device.hid_index]
    if device.hid_index == 0x01:
        indices = [1, 2, 3, 4, 5, 6]
    with contextlib.redirect_stdout(io.StringIO()):
        raw = LogiHPP20(device.pid, "", "", indices)
        omm = FeatureOnboardProfile(raw)
    return device, omm


@contextlib.contextmanager
def hidpp_session(*, attempts: int = 3) -> Iterator[OpenSession]:
    require_ghub_idle()
    key = detect_device_key()
    if not key:
        raise RuntimeError("No supported G502 mouse was detected.")

    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        omm = None
        try:
            device, omm = _open_omm(key)
            if not bool(omm.onboard_mode):
                omm.onboard_mode = True
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if omm is not None:
                omm.close()
            if attempt + 1 < attempts:
                time.sleep((0.15, 0.4, 0.9)[min(attempt, 2)])
    else:
        raise RuntimeError(f"HID++ open failed: {last_error}")

    session = OpenSession(key, device, omm)
    try:
        yield session
    finally:
        try:
            if session.leave_onboard and not bool(omm.onboard_mode):
                omm.onboard_mode = True
        except Exception:
            pass
        omm.close()
