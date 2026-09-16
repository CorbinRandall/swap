"""Compatibility shims for hidapi package versions."""

from __future__ import annotations

import sys
import ctypes

import hid

_darwin_shared_open_configured = False


def _configure_darwin_shared_open() -> None:
    """Allow opening HID++ alongside Logitech's driver extension on macOS."""
    global _darwin_shared_open_configured
    if sys.platform != "darwin" or _darwin_shared_open_configured:
        return
    try:
        lib = ctypes.CDLL(hid.__file__)
        lib.hid_darwin_set_open_exclusive.argtypes = [ctypes.c_int]
        lib.hid_darwin_set_open_exclusive.restype = None
        lib.hid_darwin_set_open_exclusive(0)
    except Exception:
        # Missing symbol / load failure must not block open attempts.
        pass
    _darwin_shared_open_configured = True


def enumerate_devices(vendor_id: int, product_id: int) -> list[dict]:
    try:
        return hid.enumerate(vendor_id, product_id)
    except TypeError:
        pass

    results = []
    for dev in hid.enumerate():
        if dev.get("vendor_id") == vendor_id and dev.get("product_id") == product_id:
            results.append(dev)
    return results


def open_device(path: str):
    _configure_darwin_shared_open()
    if hasattr(hid, "Device"):
        return _HidDevice(hid.Device(path=path))
    raw = hid.device()
    raw.open_path(path.encode() if isinstance(path, str) else path)
    return _LegacyHidDevice(raw)


class HidWriteDenied(RuntimeError):
    """macOS rejected HID SetReport (usually Input Monitoring / TCC)."""


def _raise_if_write_denied(handle, n, data) -> None:
    if isinstance(n, int) and n >= 0:
        return
    err = ""
    for attr in ("error", "get_error"):
        fn = getattr(handle, attr, None)
        if callable(fn):
            try:
                err = str(fn() or "")
            except Exception:
                err = ""
            break
    detail = err or f"write returned {n}"
    if "not permitted" in detail.lower() or "0xe00002e2" in detail.lower():
        raise HidWriteDenied(
            "HID write not permitted (macOS blocked SetReport). "
            "Enable G under System Settings → Privacy & Security → Input Monitoring."
        )
    if "0xe0005000" in detail.lower():
        raise HidWriteDenied(
            "HID write blocked (macOS / Logitech driver). "
            "Turn OFF Logitech G HUB HID Driver under Login Items → Driver Extensions, then reboot."
        )
    raise RuntimeError(f"HID write failed: {detail} (payload len={len(data)})")


class _HidDevice:
    """Wrap hid.Device so write failures surface a clear macOS TCC error."""

    def __init__(self, handle):
        self._handle = handle
        self.product = getattr(handle, "product", None) or ""
        self.serial = getattr(handle, "serial", None) or ""

    def write(self, data):
        n = self._handle.write(data)
        _raise_if_write_denied(self._handle, n, data)
        return n

    def read(self, size=255, timeout=5000):
        data = self._handle.read(size, timeout)
        if isinstance(data, list):
            return bytes(data)
        return data

    def close(self):
        self._handle.close()

    def get_report_descriptor(self):
        return self._handle.get_report_descriptor()


class _LegacyHidDevice:
    """Wrap legacy hid.device() for omm.py attribute and read() conventions."""

    def __init__(self, handle):
        self._handle = handle
        self.product = handle.get_product_string() or ""
        self.serial = handle.get_serial_number_string() or ""

    def write(self, data):
        n = self._handle.write(data)
        _raise_if_write_denied(self._handle, n, data)
        return n

    def read(self, size=255, timeout=5000):
        data = self._handle.read(size, timeout)
        if isinstance(data, list):
            return bytes(data)
        return data

    def close(self):
        self._handle.close()

    def get_report_descriptor(self):
        return self._handle.get_report_descriptor()


def get_report_descriptor(handle) -> bytes:
    if hasattr(handle, "get_report_descriptor"):
        data = handle.get_report_descriptor()
    elif hasattr(handle, "get_descriptor"):
        data = handle.get_descriptor()
    else:
        return b""
    if isinstance(data, (list, tuple)):
        return bytes(data)
    if isinstance(data, bytearray):
        return bytes(data)
    return data
