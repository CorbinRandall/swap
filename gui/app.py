#!/usr/bin/env python3
"""G — window (macOS) / tray (Windows) UI for G502 onboard memory."""

from __future__ import annotations

import sys


def _run_hid_probe() -> int:
    """Run from the real G.app process identity; write results and exit."""
    from pathlib import Path

    from gcore.device import detect_device_key, probe_hidpp_write, _open_omm
    from gcore.paths import application_support_root

    lines: list[str] = []
    if sys.platform == "darwin":
        import ctypes

        iokit = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/IOKit.framework/IOKit"
        )
        lines.append(f"IOHIDCheckAccess={iokit.IOHIDCheckAccess(1)}")
        lines.append(f"executable={sys.executable}")
        lines.append(f"argv0={sys.argv[0] if sys.argv else ''}")
        lines.append(f"frozen={getattr(sys, 'frozen', False)}")

    key = detect_device_key()
    lines.append(f"detect_device_key={key!r}")
    ok, detail = probe_hidpp_write()
    lines.append(f"probe_ok={ok}")
    lines.append(f"probe_detail={detail}")
    if key and ok:
        try:
            device, omm = _open_omm(key)
            try:
                lines.append(f"open_ok=True label={device.label}")
            finally:
                omm.close()
        except Exception as exc:  # noqa: BLE001
            lines.append(f"open_ok=False error={type(exc).__name__}: {exc}")
    elif key:
        lines.append("open_ok=skipped (probe failed)")

    out = application_support_root() / "hid-probe.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines) + "\n"
    out.write_text(text, encoding="utf-8")
    print(text, end="")
    print(f"wrote {out}", file=sys.stderr)
    return 0 if ok else 2


def main() -> None:
    # Absolute imports so py2app's top-level Resources/app.py works
    # (relative imports fail there: "no known parent package").
    if "--hid-probe" in sys.argv:
        raise SystemExit(_run_hid_probe())

    from gcore.preflight import resolve_ghub_before_launch

    resolve_ghub_before_launch()

    if sys.platform == "darwin":
        from gui.app_mac import main as _main
    elif sys.platform == "win32":
        from gui.app_win import main as _main
    else:
        raise SystemExit(
            f"G's UI is only implemented on macOS and Windows (got {sys.platform})."
        )
    _main()


if __name__ == "__main__":
    main()
