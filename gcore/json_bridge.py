"""JSON command bridge for native Swift UI (Xcode) and tests."""

from __future__ import annotations

import json
import os
import sys
import traceback
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .engine import Engine, EngineError, EngineStatus, SlotInfo, write_failure_hint
from .library import LibraryPreset
from .paths import open_in_file_manager, presets_dir, set_presets_dir


def _serialize(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _serialize(v) for k, v in asdict(obj).items()}
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, list):
        return [_serialize(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    return obj


def _status_dict(st: EngineStatus) -> dict[str, Any]:
    return _serialize(st)


def _library_dict(items: list[LibraryPreset]) -> list[dict[str, Any]]:
    return [{"name": p.name, "path": str(p.path)} for p in items]


_ENGINE: Engine | None = None


def _engine() -> Engine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = Engine()
    return _ENGINE


def handle(payload: dict[str, Any]) -> dict[str, Any]:
    cmd = payload.get("cmd")
    eng = _engine()

    if cmd == "status":
        st = eng.status(
            read_names=bool(payload.get("read_names", False)),
            open_attempts=int(payload.get("open_attempts", 1)),
        )
        return {"ok": True, "status": _status_dict(st), "library": _library_dict(eng.list_library())}

    if cmd == "list_library":
        return {"ok": True, "library": _library_dict(eng.list_library())}

    if cmd == "library_dir":
        return {"ok": True, "path": str(eng.library_dir)}

    if cmd == "set_active_slot":
        slot = int(payload["slot"])
        eng.set_active_slot(slot)
        st = eng.status(read_names=False, open_attempts=1)
        return {"ok": True, "status": _status_dict(st), "library": _library_dict(eng.list_library())}

    if cmd == "set_library_dir":
        path = Path(str(payload["path"])).expanduser().resolve()
        eng.set_library_dir(path, persist=bool(payload.get("persist", True)))
        return {"ok": True, "path": str(eng.library_dir), "library": _library_dict(eng.list_library())}

    if cmd == "assign_all":
        results, skipped, failed = eng.assign_all_from_library()
        st = eng.status(read_names=True, open_attempts=1)
        lines = [f"✓ Slot {s}: {name}" for s, name in results]
        if failed:
            lines.append("")
            lines.extend(f"✗ {msg}" for msg in failed)
        if skipped:
            lines.append("")
            lines.append("Note: " + "; ".join(skipped))
        if failed:
            lines.append("")
            lines.append(write_failure_hint(failed))
        ok = len(failed) == 0
        summary = "\n".join(lines) if lines else "Replace All finished."
        return {
            "ok": ok,
            "summary": summary,
            "status": _status_dict(st),
            "library": _library_dict(eng.list_library()),
            **({"error": summary} if failed else {}),
        }

    if cmd == "extract_all":
        paths = eng.extract_all_slots()
        st = eng.status(read_names=True, open_attempts=1)
        lines = [str(p) for p in paths]
        return {
            "ok": True,
            "summary": "\n".join(lines) if lines else "Extract All finished.",
            "status": _status_dict(st),
            "library": _library_dict(eng.list_library()),
        }

    if cmd == "quit_ghub":
        eng.quit_ghub()
        st = eng.status(read_names=False, open_attempts=1)
        return {"ok": True, "status": _status_dict(st)}

    if cmd == "open_library":
        open_in_file_manager(eng.library_dir)
        return {"ok": True}

    if cmd == "presets_dir_default":
        return {"ok": True, "path": str(presets_dir())}

    raise EngineError(f"unknown command: {cmd!r}")


def run(json_text: str) -> str:
    """Entry for PythonKit: pass JSON string, get JSON string back."""
    try:
        payload = json.loads(json_text)
        if not isinstance(payload, dict):
            raise EngineError("payload must be a JSON object")
        result = handle(payload)
        return json.dumps(result)
    except Exception as exc:
        return json.dumps(
            {
                "ok": False,
                "error": str(exc),
                "trace": traceback.format_exc(),
            }
        )


def main() -> None:
    raw = sys.stdin.read()
    sys.stdout.write(run(raw))


if __name__ == "__main__":
    main()
