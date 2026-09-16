"""Pull onboard profiles from Logitech mice via HID++."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .convert import omm_to_ghub_preset
from .devices import DEVICES, get_device
from .ghub_running import require_ghub_stopped

AUTO_PULL_DEVICE = "auto"

# When --device auto: try dongle first, then USB wireless, then wired PIDs.
PULL_DEVICE_PRIORITY = (
    "g502wireless-dongle",
    "g502wireless",
    "g502-hero",
    "g502",
)

_PULL_ERRORS = (RuntimeError, OSError, AssertionError, TypeError, ValueError)


def _import_omm():
    try:
        from .omm.FeatureOnboardProfile import FeatureOnboardProfile
        from .omm.LogiHPP20 import LogiHPP20
    except ImportError as exc:
        raise RuntimeError(
            "Mouse pull requires hidapi. Install with: pip install hidapi"
        ) from exc
    return LogiHPP20, FeatureOnboardProfile


def _connected_logitech_pids() -> set[int]:
    from .omm import hid_compat

    try:
        import hid

        devs = hid.enumerate()
    except Exception:
        devs = []
    pids = {d["product_id"] for d in devs if d.get("vendor_id") == 0x046D}
    if pids:
        return pids

    for key in PULL_DEVICE_PRIORITY:
        cfg = DEVICES[key]
        if hid_compat.enumerate_devices(0x046D, cfg.pid):
            pids.add(cfg.pid)
    return pids


def connected_pull_device_keys() -> list[str]:
    """Connected G502-family device keys, best match first."""
    pids = _connected_logitech_pids()
    return [key for key in PULL_DEVICE_PRIORITY if DEVICES[key].pid in pids]


def detect_pull_device() -> str | None:
    """Best device key for pull, or None if nothing supported is connected."""
    keys = connected_pull_device_keys()
    return keys[0] if keys else None


def resolve_pull_device_keys(device_key: str) -> list[str]:
    """Device key(s) to attempt for a pull operation."""
    if device_key != AUTO_PULL_DEVICE:
        return [device_key]
    keys = connected_pull_device_keys()
    if not keys:
        raise RuntimeError(
            "No supported Logitech mouse detected (G502 wired, Hero, or Lightspeed).\n"
            "Quit G Hub, connect a USB cable or Lightspeed receiver, then retry."
        )
    return keys


def pull_device_status_lines() -> list[str]:
    """Human-readable summary of connected pull targets."""
    keys = connected_pull_device_keys()
    if not keys:
        return ["Mouse pull: no G502-family device detected"]
    parts = [f"{key} ({DEVICES[key].label})" for key in keys]
    return [
        "Mouse pull: connected — " + ", ".join(parts),
        "Auto mode tries in that order. Override with --device <key>.",
    ]


def _open_logi_device(device_key: str):
    device = get_device(device_key)
    LogiHPP20, _ = _import_omm()
    indices = [device.hid_index]
    if device.hid_index == 0x01:
        indices = [1, 2, 3, 4, 5, 6]
    return LogiHPP20(device.pid, "", "", indices)


def pull_onboard_omm_json(device_key: str, slot: int) -> dict[str, Any]:
    """Read raw onboard profile JSON from mouse (Rosetta / truth layer)."""
    require_ghub_stopped()
    LogiHPP20, FeatureOnboardProfile = _import_omm()

    dev = _open_logi_device(device_key)
    omm = None
    prior_mode: bool | None = None
    try:
        omm = FeatureOnboardProfile(dev)
        # Reading slots requires onboard mode; remember the original mode so
        # the mouse is not left in onboard mode afterwards (leaving it ON
        # confuses G Hub's next agent handshake and can wedge the device's
        # HID++ firmware until a USB power cycle).
        prior_mode = bool(omm.onboard_mode)
        if not prior_mode:
            omm.onboard_mode = True
        omm.dest_profile = slot
        if not omm.profile_enabled:
            raise RuntimeError(
                f"Onboard profile slot {slot} is disabled on the mouse. "
                f"Enable it in G Hub or with omm --enable."
            )
        data = omm.onboard_profile_to_bin()
        return omm.profile_bin_to_json(data)
    finally:
        if omm is not None:
            try:
                if prior_mode is False and omm.onboard_mode:
                    omm.onboard_mode = False
            except Exception:
                pass
            omm.close()
        else:
            dev.close()


def pull_onboard_profile(
    device_key: str,
    slot: int,
    *,
    output: Path | None = None,
    profile_name: str | None = None,
    target_platform: str | None = None,
) -> dict[str, Any]:
    device = get_device(device_key)
    omm_json = pull_onboard_omm_json(device_key, slot)

    return omm_to_ghub_preset(
        omm_json,
        device,
        profile_name=profile_name or omm_json.get("profile_name"),
        slot=slot,
        target_platform=target_platform,
    )


def pull_to_file(
    device_key: str,
    slot: int,
    output: Path,
    *,
    profile_name: str | None = None,
    target_platform: str | None = None,
    raw: bool = False,
) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    if raw:
        omm_json = pull_onboard_omm_json(device_key, slot)
        output.write_text(json.dumps(omm_json, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return output

    preset = pull_onboard_profile(
        device_key,
        slot,
        profile_name=profile_name,
        target_platform=target_platform,
    )
    output.write_text(json.dumps(preset, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output


def pull_to_file_resilient(
    device_key: str,
    slot: int,
    output: Path,
    *,
    profile_name: str | None = None,
    target_platform: str | None = None,
    raw: bool = False,
) -> tuple[Path, str]:
    """Pull using one explicit device key, or try each connected device when auto."""
    keys = resolve_pull_device_keys(device_key)
    errors: list[str] = []
    for key in keys:
        try:
            path = pull_to_file(
                key,
                slot,
                output,
                profile_name=profile_name,
                target_platform=target_platform,
                raw=raw,
            )
            return path, key
        except _PULL_ERRORS as exc:
            errors.append(f"  {key} ({DEVICES[key].label}): {exc}")
    hint = (
        "\n\nTip: pass --device g502, g502wireless, or g502wireless-dongle "
        "to force a specific connection."
    )
    raise RuntimeError(
        f"Could not pull onboard slot {slot} from any connected device.\n"
        + "\n".join(errors)
        + hint
    )


def list_connected_logitech_devices() -> list[dict[str, Any]]:
    LogiHPP20, _ = _import_omm()
    LogiHPP20.list_devices()
    return []


def _safe_name(name: str) -> str:
    import re

    safe = re.sub(r"[^\w\- ]+", "", name).strip().replace(" ", "_")
    return safe or "onboard_preset"
