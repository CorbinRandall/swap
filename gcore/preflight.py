"""Pre-launch checks before the tray / menu-bar UI starts."""

from __future__ import annotations

import subprocess
import sys
import time


def _ghub_blocking() -> bool:
    from ghub_presets.ghub_running import is_ghub_running

    return bool(is_ghub_running())


def _prompt_quit_ghub() -> bool:
    """Ask whether to quit G Hub. True = quit and continue; False = abort launch."""
    title = "G"
    message = (
        "Logitech G Hub is running.\n\n"
        "G needs exclusive access to the mouse (HID++). "
        "While G Hub is open, G cannot read slots or apply presets reliably.\n\n"
        "Quit G Hub and open G?"
    )
    if sys.platform == "win32":
        try:
            import ctypes

            # MB_YESNO | MB_ICONWARNING | MB_TOPMOST
            result = ctypes.windll.user32.MessageBoxW(
                0, message, title, 0x04 | 0x30 | 0x40000
            )
            return result == 6  # IDYES
        except Exception:
            return True

    if sys.platform == "darwin":
        # Keep the dialog copy single-line — multiline osascript -e breaks easily.
        short = (
            "Logitech G Hub is running. G needs exclusive access to the mouse. "
            "Quit G Hub and open G?"
        )
        script = (
            f'display dialog "{short}" with title "G" with icon caution '
            'buttons {"Cancel", "Quit G Hub & Open G"} '
            'default button "Quit G Hub & Open G" cancel button "Cancel"'
        )
        try:
            proc = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                check=False,
            )
            return proc.returncode == 0
        except FileNotFoundError:
            return True

    # Fallback for other platforms / headless
    print(message, file=sys.stderr)
    try:
        answer = input("Quit G Hub and open G? [Y/n] ").strip().lower()
    except EOFError:
        return False
    return answer in ("", "y", "yes")


def _alert(message: str, title: str = "G") -> None:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, message, title, 0x10 | 0x40000)
            return
        except Exception:
            pass
    elif sys.platform == "darwin":
        try:
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'display alert "{title}" message '
                    f'"{message.replace(chr(34), chr(39))}" as critical',
                ],
                check=False,
            )
            return
        except FileNotFoundError:
            pass
    print(f"{title}: {message}", file=sys.stderr)


def _quit_ghub_and_wait(*, timeout: float = 25.0) -> bool:
    from ghub_presets.ghub_running import is_ghub_running, quit_ghub

    quit_ghub()
    deadline = time.monotonic() + timeout
    last_kill = 0.0
    while time.monotonic() < deadline:
        if not is_ghub_running():
            return True
        time.sleep(0.4)
        if time.monotonic() - last_kill > 2.0:
            quit_ghub()
            last_kill = time.monotonic()
    return not is_ghub_running()


def resolve_ghub_before_launch() -> None:
    """
    If G Hub is holding the mouse, prompt to quit it before starting G.

    - Yes → quit G Hub, wait until clear, then continue
    - No / Cancel → exit without starting the tray UI
    """
    if not _ghub_blocking():
        return
    if not _prompt_quit_ghub():
        raise SystemExit(0)
    if not _quit_ghub_and_wait():
        _alert(
            "Could not quit Logitech G Hub completely.\n\n"
            "Quit it from its tray/menu icon (or Task Manager / Activity Monitor), "
            "then open G again."
        )
        raise SystemExit(1)
