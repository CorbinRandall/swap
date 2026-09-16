"""Short-lived HID++ device sessions with retries and mode restore."""

from __future__ import annotations

import contextlib
import io
import time
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from .mutex import require_ghub_idle
from .paths import ensure_toolkit_on_path

ensure_toolkit_on_path()

from ghub_presets.devices import DEVICES, DeviceConfig, get_device  # noqa: E402
from ghub_presets.pull import connected_pull_device_keys  # noqa: E402

RETRY_ATTEMPTS = 3
RETRY_BACKOFF_S = (0.15, 0.4, 0.9)


@dataclass
class OpenSession:
    device_key: str
    device: DeviceConfig
    omm: Any
    prior_mode: bool
    # If set, finally leaves the device in this mode (for intentional toggles).
    # If None, restores prior_mode.
    leave_mode: Optional[bool] = field(default=None)


def detect_device_key() -> str | None:
    keys = connected_pull_device_keys()
    return keys[0] if keys else None


def probe_hidpp_write() -> tuple[bool, str]:
    """Try one HID++ SetReport. Detects stale Input Monitoring (UI on, writes denied)."""
    try:
        from ghub_presets.omm import hid_compat
        from ghub_presets.omm.hid_compat import HidWriteDenied
    except Exception as exc:  # noqa: BLE001
        return False, f"hidapi unavailable: {exc}"

    key = detect_device_key()
    if not key:
        return False, "no G502 enumerated"
    device = get_device(key)
    try:
        for dev in hid_compat.enumerate_devices(0x046D, device.pid):
            if int(dev.get("usage_page") or 0) < 0xFF00:
                continue
            h = hid_compat.open_device(dev["path"])
            try:
                # Root feature lookup for device name — same path LogiHPP20 uses.
                payload = bytes([0x11, device.hid_index if device.hid_index != 0x01 else 0x01, 0, 0x0F, 0x00, 0x05] + [0] * 14)
                h.write(payload[:20])
                return True, "SetReport ok"
            finally:
                h.close()
        return False, "no HID++ interface"
    except HidWriteDenied as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def probe_onboard_write() -> tuple[bool, str]:
    """Try a round-trip write of onboard page 0 (profile directory)."""
    import contextlib
    import io

    try:
        with contextlib.redirect_stdout(io.StringIO()):
            with hidpp_session(force_onboard=True) as session:
                omm = session.omm
                session.leave_mode = True
                if not omm.onboard_mode:
                    return False, "onboard mode is off"
                page0 = bytearray(omm.read_memory_page(0, verify=False))
                omm.write_memory_page(0, page0, verify=False)
        return True, "onboard memory write ok"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _open_omm(device_key: str):
    from ghub_presets.omm.FeatureOnboardProfile import FeatureOnboardProfile
    from ghub_presets.omm.LogiHPP20 import LogiHPP20

    device = get_device(device_key)
    indices = [device.hid_index]
    if device.hid_index == 0x01:
        indices = [1, 2, 3, 4, 5, 6]
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink):
        dev = LogiHPP20(device.pid, "", "", indices)
        omm = FeatureOnboardProfile(dev)
    return device, omm


@contextlib.contextmanager
def hidpp_session(
    device_key: str | None = None,
    *,
    force_onboard: bool = True,
    attempts: int | None = None,
) -> Iterator[OpenSession]:
    """
    Open HID++, optionally enter onboard mode for the call, then restore mode and close.

    Never holds the device beyond the with-block. Refuses if G Hub is running.
    Set session.leave_mode to override restore (e.g. after set_onboard_mode / slot switch).
    """
    require_ghub_idle()
    key = device_key or detect_device_key()
    if not key:
        raise RuntimeError("No G502 found (wired or Lightspeed receiver).")

    last_err: Exception | None = None
    device = None
    omm = None
    prior_mode = False
    tries = RETRY_ATTEMPTS if attempts is None else max(1, int(attempts))

    for attempt in range(tries):
        try:
            device, omm = _open_omm(key)
            prior_mode = bool(omm.onboard_mode)
            if force_onboard and not prior_mode:
                omm.onboard_mode = True
            break
        except Exception as exc:  # noqa: BLE001 — KVM/HID flakiness
            last_err = exc
            if omm is not None:
                try:
                    omm.close()
                except Exception:
                    pass
            omm = None
            device = None
            if attempt + 1 < tries:
                time.sleep(RETRY_BACKOFF_S[min(attempt, len(RETRY_BACKOFF_S) - 1)])
    else:
        raise RuntimeError(f"HID++ open failed after retries: {last_err}")

    assert omm is not None and device is not None
    session = OpenSession(
        device_key=key,
        device=device,
        omm=omm,
        prior_mode=prior_mode,
    )
    try:
        yield session
    finally:
        try:
            if session.leave_mode is not None:
                if bool(omm.onboard_mode) != bool(session.leave_mode):
                    omm.onboard_mode = bool(session.leave_mode)
            elif prior_mode is False and bool(omm.onboard_mode):
                omm.onboard_mode = False
        except Exception:
            pass
        try:
            omm.close()
        except Exception:
            pass


def known_device_labels() -> list[str]:
    return [f"{k}: {cfg.label}" for k, cfg in DEVICES.items()]
