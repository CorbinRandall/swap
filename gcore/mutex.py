"""G Hub coexistence — never talk to the mouse while lghub_agent is running."""

from __future__ import annotations

from dataclasses import dataclass

from .paths import ensure_toolkit_on_path

ensure_toolkit_on_path()

from ghub_presets.ghub_running import (  # noqa: E402
    ensure_ghub_stopped,
    list_ghub_processes,
)


@dataclass(frozen=True)
class GHubMutexStatus:
    running: bool
    blocking: bool
    process_names: tuple[str, ...]


def ghub_status() -> GHubMutexStatus:
    procs = list_ghub_processes()
    names = tuple(sorted({p.command.split("/")[-1][:40] for p in procs}))
    blocking = any(
        "lghub_agent" in p.command
        or "lghub_system_tray" in p.command
        or "lghub_ui" in p.command
        for p in procs
    )
    return GHubMutexStatus(running=bool(procs), blocking=blocking, process_names=names)


def require_ghub_idle() -> None:
    """Raise RuntimeError if G Hub is holding the device."""
    status = ghub_status()
    if status.blocking:
        raise RuntimeError(
            "G Hub is running — quit it before using G. "
            f"Seen: {', '.join(status.process_names) or 'lghub'}"
        )


def quit_ghub() -> None:
    ensure_ghub_stopped(quit_first=True)
