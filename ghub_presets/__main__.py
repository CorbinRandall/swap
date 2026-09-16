"""G Hub Personal Preset Control — CLI entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .db import backup_settings_to_archive, ghub_settings_db, list_profiles, read_settings
from .devices import DEVICES
from .export import export_all_profiles, export_profile_by_name, load_preset_file
from .ghub_running import (
    ensure_ghub_stopped,
    is_ghub_running,
    list_all_ghub_processes,
    list_ghub_processes,
    quit_ghub,
    require_ghub_stopped,
)
from .import_ import collect_preset_paths, import_presets, replace_library_with_presets
from .library import (
    duplicate_preset,
    organize_library,
    remove_preset,
    scan_preset_files,
    sync_manifest,
)
from .manifest import list_manifest_presets, upsert_manifest_entry
from .paths import PRESETS_DIR_NAME, TOOLKIT_DATA_DIR_NAME, default_presets_dir, onboard_dir, presets_dir
from .update_blocker import (
    apply_update_block,
    get_update_block_status,
    remove_update_block,
)


def _library_root(folder: str | Path) -> Path:
    return Path(folder).expanduser().resolve()


def _manifest_file_key(library: Path, path: Path) -> str:
    try:
        return str(path.relative_to(library))
    except ValueError:
        return path.name


def _print_import_result(result) -> None:
    if result.removed:
        print("Removed from G Hub:", ", ".join(result.removed))
    if result.cards_removed:
        print(f"Pruned orphan macro cards: {result.cards_removed}")
    if result.imported:
        print("Imported:", ", ".join(result.imported))
    if result.replaced:
        print("Replaced:", ", ".join(result.replaced))
    if result.skipped:
        print("Skipped:", ", ".join(result.skipped))
    if result.imported or result.replaced or result.removed:
        from .system_profile import SYSTEM_PROFILE_LABEL

        print(
            f"(Factory default kept in {TOOLKIT_DATA_DIR_NAME}/system/{SYSTEM_PROFILE_LABEL}.lghub-preset.json — "
            "not shown in G Hub.)"
        )


def cmd_list(args: argparse.Namespace) -> int:
    if args.source == "folder":
        library = _library_root(args.folder)
        if not library.exists():
            print(f"Folder not found: {library}")
            return 1
        paths = scan_preset_files(library)
        if not paths:
            print(f"No presets in {library}")
            return 0
        print(f"(DONT_TOUCH_SYSTEM is kept in {TOOLKIT_DATA_DIR_NAME}/system/ — not shown here.)")
        print()
        for path in paths:
            try:
                preset = load_preset_file(path)
                rel = path.relative_to(library)
                print(f"{rel}\t{preset.get('name')}\t{preset.get('sourceDevice', '?')}")
            except Exception as exc:
                print(f"{path.name}\t(error: {exc})")
        manifest = list_manifest_presets(library)
        if manifest:
            print(f"\nManifest: {len(manifest)} entries (run 'sync' to refresh)")
        return 0

    try:
        settings = read_settings(args.db_path)
    except FileNotFoundError as exc:
        print(exc)
        return 1
    for profile in list_profiles(settings):
        print(profile.get("name", "?"), profile.get("id", ""))
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    library = _library_root(args.folder)
    try:
        path = backup_settings_to_archive(library)
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Backed up G Hub database to: {path}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    try:
        require_ghub_stopped()
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    library = _library_root(args.folder)
    folder = presets_dir(library)
    folder.mkdir(parents=True, exist_ok=True)

    if args.all:
        paths = export_all_profiles(folder, db_path=args.db_path)
        for path in paths:
            preset = load_preset_file(path)
            upsert_manifest_entry(
                library,
                _manifest_file_key(library, path),
                preset.get("name", "?"),
                "ghub-export",
            )
            print(f"Exported: {path}")
        sync_manifest(library)
        print(f"Done. {len(paths)} profile(s) saved to:")
        print(f"  {folder.resolve()}")
        exported_names = {load_preset_file(p).get("name") for p in paths}
        from .library import scan_user_preset_files

        stale = [
            p.name
            for p in scan_user_preset_files(library)
            if load_preset_file(p).get("name") not in exported_names
        ]
        if stale:
            print()
            print("Note: these Presets files were not updated (profile gone from G Hub?):")
            for name in stale:
                print(f"  {name}")
        return 0

    if not args.name:
        print("Provide --name or use --all")
        return 1

    path = export_profile_by_name(args.name, folder, db_path=args.db_path)
    preset = load_preset_file(path)
    upsert_manifest_entry(
        library,
        _manifest_file_key(library, path),
        preset.get("name", "?"),
        "ghub-export",
    )
    print(f"Exported: {path}")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    target = Path(args.target)
    paths = collect_preset_paths(target)
    if not paths:
        print(f"No preset files found at {target}")
        return 1

    conflict = "replace" if args.replace else ("rename" if args.rename else "skip")
    try:
        result = import_presets(
        paths,
        conflict_mode=conflict,
        rename_to=args.rename,
        target_device=args.target_device,
        target_platform="mac" if args.for_mac else None,
        db_path=args.db_path,
        dry_run=args.dry_run,
        library=_library_root(args.folder),
        )
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    _print_import_result(result)
    if args.dry_run:
        print("(dry run — settings.db not modified)")
    else:
        library = _library_root(args.folder)
        for path in paths:
            try:
                preset = load_preset_file(path)
                upsert_manifest_entry(
                    library,
                    _manifest_file_key(library, path),
                    preset.get("name", "?"),
                    "ghub-import",
                )
            except Exception:
                pass
        print("Reopen Logitech G Hub to see imported profiles.")
    return 0


def cmd_replace(args: argparse.Namespace) -> int:
    presets = Path(args.presets_dir)
    if not presets.is_dir():
        print(f"Presets folder not found: {presets}")
        return 1

    try:
        result = replace_library_with_presets(
            presets,
            target_device=args.target_device,
            target_platform="mac" if args.for_mac else None,
            db_path=args.db_path,
            dry_run=args.dry_run,
            desktop_only=not args.all_profiles,
        )
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    _print_import_result(result)
    if args.dry_run:
        print("(dry run — settings.db not modified)")
        print(f"\nPresets folder keeps {len(collect_preset_paths(presets))} profile(s).")
    else:
        print("\nG Hub now matches your Presets folder. Open Logitech G Hub.")
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    from .pull import AUTO_PULL_DEVICE, pull_to_file, pull_to_file_resilient

    library = _library_root(args.folder)
    slots = list(range(1, 6)) if args.all_slots else [args.slot]
    last = 0
    for slot in slots:
        if args.output and not args.all_slots:
            output = Path(args.output)
        elif args.raw:
            output = onboard_dir(library) / f"slot{slot}.json"
        else:
            output = presets_dir(library) / f"onboard_slot{slot}.lghub-preset.json"

        try:
            if args.device == AUTO_PULL_DEVICE:
                path, used_device = pull_to_file_resilient(
                    args.device,
                    slot,
                    output,
                    profile_name=args.name,
                    target_platform="mac" if args.for_mac else None,
                    raw=args.raw,
                )
            else:
                path = pull_to_file(
                    args.device,
                    slot,
                    output,
                    profile_name=args.name,
                    target_platform="mac" if args.for_mac else None,
                    raw=args.raw,
                )
                used_device = args.device
        except (RuntimeError, OSError, AssertionError, TypeError, ValueError) as exc:
            print(f"Slot {slot}: {exc}", file=sys.stderr)
            last = 1
            continue

        if not args.raw:
            preset = load_preset_file(path)
            upsert_manifest_entry(
                library,
                _manifest_file_key(library, path),
                preset.get("name", "?"),
                "mouse-pull",
            )
        print(f"Pulled onboard slot {slot} ({used_device}) -> {path}")
    return last


def cmd_duplicate(args: argparse.Namespace) -> int:
    source = Path(args.preset)
    if not source.exists():
        print(f"Not found: {source}")
        return 1
    library = _library_root(args.folder)
    dest = Path(args.output) if args.output else None
    try:
        path = duplicate_preset(source, new_name=args.name, output=dest)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    preset = load_preset_file(path)
    upsert_manifest_entry(
        library,
        _manifest_file_key(library, path),
        preset.get("name", "?"),
        "duplicate",
    )
    print(f"Created: {path}")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    target = Path(args.preset)
    if not target.exists():
        print(f"Not found: {target}")
        return 1
    library = _library_root(args.folder)
    try:
        remove_preset(target, library)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Removed: {target}")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    library = _library_root(args.folder)
    if args.organize:
        moves = organize_library(library)
        for line in moves:
            print(line)
        if not moves:
            print("Library already organized.")
    count = sync_manifest(library)
    print(f"Manifest updated: {count} preset(s) in {library / 'manifest.json'}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    from .compare import compare_onboard_to_ghub, print_compare_report

    report = compare_onboard_to_ghub(args.onboard, args.ghub)
    print_compare_report(report)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"\nWrote {args.output}")
    return 0


def cmd_quit_ghub(args: argparse.Namespace) -> int:
    if not is_ghub_running():
        print("No G Hub processes detected.")
        return 0
    print("Stopping G Hub (menu bar + background agents)...")
    for action in quit_ghub():
        print(f"  {action}")
    try:
        ensure_ghub_stopped(timeout=15, quit_first=True)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    print("G Hub is fully stopped. Safe to export/import/replace.")
    return 0


def cmd_block_updates(args: argparse.Namespace) -> int:
    library = _library_root(args.folder)
    try:
        actions = apply_update_block(library=library)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print("G Hub automatic updates blocked.")
    for action in actions:
        print(f"  {action}")
    print()
    if sys.platform == "win32":
        print(
            "LGHUBUpdaterService stays running so G Hub can start; "
            "updates are blocked via registry + staging hosts "
            "(device depot CDN is intentionally left open)."
        )
    elif sys.platform == "darwin":
        print(
            "com.logi.ghub.updater stays loaded so G Hub can start; "
            "updates are blocked via periodic_check.json + staging hosts "
            "(device depot CDN is intentionally left open)."
        )
    print(
        "Note: blocking updates.ghub.logitechg.com / pipeline.logitech.io "
        "makes devices show Inactive — this toolkit no longer does that."
    )
    print("Also disable 'Enable automatic updates' in G Hub Settings if it is still checked.")
    print("To undo: ghub-presets unblock-updates")
    return 0


def cmd_unblock_updates(args: argparse.Namespace) -> int:
    library = _library_root(args.folder)
    try:
        actions = remove_update_block(library=library)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print("G Hub update block removed.")
    for action in actions:
        print(f"  {action}")
    return 0


def cmd_recover(args: argparse.Namespace) -> int:
    from .recover import recover_inactive

    result = recover_inactive(
        max_hub_level=args.max_level,
        dry_run=args.dry_run,
    )
    for line in result.summary_lines():
        print(line)
    return 0 if result.ok else 1


def cmd_status(args: argparse.Namespace) -> int:
    library = _library_root(args.folder)
    db = ghub_settings_db()
    blocking = is_ghub_running()
    all_procs = list_all_ghub_processes()
    print(f"G Hub blocking (must quit for import/replace): {blocking}")
    if all_procs:
        blocking_ids = {p.pid for p in list_ghub_processes(blocking_only=True)}
        for proc in all_procs:
            tag = "BLOCKS" if proc.pid in blocking_ids else "background"
            print(f"  [{tag}] pid {proc.pid}: {proc.command[:90]}")
    elif not blocking:
        print("  (no Logitech processes detected)")
    print(f"settings.db: {db} ({'exists' if db.exists() else 'missing'})")
    print(f"Presets folder: {library}")
    print(f"  Put .lghub-preset.json files here (or use Export)")
    print(f"  {TOOLKIT_DATA_DIR_NAME}/onboard/ — raw mouse backup (Pull from Mouse)")
    n = len(scan_preset_files(library))
    print(f"  {n} importable preset file(s) on disk")
    print()
    from .pull import pull_device_status_lines

    for line in pull_device_status_lines():
        print(f"  {line}")
    print()
    print("G Hub live device status (agent API — detects UI Inactive):")
    from .live_status import summarize_live_status

    for line in summarize_live_status().summary_lines():
        print(f"  {line}")
    print()
    print("G Hub update block:")
    for line in get_update_block_status(library=library).summary_lines():
        print(f"  {line}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ghub-presets",
        description=(
            "Export, import, and pull Logitech G Hub mouse profiles. "
            "Independent tool — not affiliated with Logitech. "
            "Modifies local settings.db; see DISCLAIMER.md and SECURITY.md."
        ),
    )
    parser.add_argument(
        "--folder",
        default=str(default_presets_dir()),
        help=f"Presets folder (default: ./{PRESETS_DIR_NAME} in toolkit, else ~/LogitechPresets)",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Override path to settings.db",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List profiles in G Hub or preset folder")
    p_list.add_argument(
        "--source",
        choices=("ghub", "folder"),
        default="ghub",
        help="List from G Hub database or preset folder",
    )

    p_export = sub.add_parser("export", help="Export profile(s) from G Hub to preset files")
    p_export.add_argument("--name", help="Profile name to export")
    p_export.add_argument("--all", action="store_true", help="Export all profiles")

    p_import = sub.add_parser("import", help="Import preset file(s) into G Hub")
    p_import.add_argument("target", help="Preset file or folder")
    p_import.add_argument("--replace", action="store_true", help="Replace existing profile with same name")
    p_import.add_argument("--rename", help="Import under a new profile name (single file)")
    p_import.add_argument(
        "--target-device",
        choices=("g502_spectrum", "g502wireless"),
        help="Remap slot IDs for target mouse model",
    )
    p_import.add_argument("--dry-run", action="store_true", help="Preview without writing settings.db")
    p_import.add_argument(
        "--for-mac",
        action="store_true",
        help="Re-convert onboard presets for macOS (Ctrl→Cmd editing shortcuts)",
    )

    p_replace = sub.add_parser(
        "replace",
        help=f"Remove G Hub profiles not in {PRESETS_DIR_NAME}/, prune bloat, import presets",
    )
    p_replace.add_argument(
        "presets_dir",
        nargs="?",
        default=None,
        help=f"Presets folder (default: toolkit {PRESETS_DIR_NAME}/)",
    )
    p_replace.add_argument("--dry-run", action="store_true", help="Show what would be removed")
    p_replace.add_argument(
        "--all-profiles",
        action="store_true",
        help=f"Also remove non-desktop (per-game) profiles not in {PRESETS_DIR_NAME}/",
    )
    p_replace.add_argument("--target-device", choices=("g502_spectrum", "g502wireless"))
    p_replace.add_argument("--for-mac", action="store_true")

    p_pull = sub.add_parser("pull", help="Pull onboard profile from mouse via HID++")
    p_pull.add_argument("--slot", type=int, default=1, help="Onboard profile slot (1-5)")
    p_pull.add_argument(
        "--all-slots",
        action="store_true",
        help="Pull slots 1–5 (skips empty/disabled slots with a message)",
    )
    p_pull.add_argument(
        "--device",
        choices=(*DEVICES.keys(), "auto"),
        default="auto",
        help=(
            "Device connection (auto tries dongle, USB wireless, then wired). "
            "Override: g502, g502-hero, g502wireless, g502wireless-dongle"
        ),
    )
    p_pull.add_argument("--output", help="Output preset file path")
    p_pull.add_argument("--name", help="Override imported profile name")
    p_pull.add_argument(
        "--raw",
        action="store_true",
        help="Save raw onboard omm JSON to onboard/ (not G Hub import format)",
    )
    p_pull.add_argument(
        "--for-mac",
        action="store_true",
        help="Map Windows Ctrl editing shortcuts to Mac Cmd when converting",
    )

    p_dup = sub.add_parser("duplicate", help="Copy a preset file (optionally rename)")
    p_dup.add_argument("preset", help="Source .lghub-preset.json")
    p_dup.add_argument("--name", help="New profile name inside the file")
    p_dup.add_argument("--output", help="Destination path (default: same folder)")

    p_rm = sub.add_parser("remove", help="Delete a preset file and update manifest")
    p_rm.add_argument("preset", help="Preset file to delete")

    p_sync = sub.add_parser("sync", help="Organize folders and rebuild manifest.json")
    p_sync.add_argument(
        "--organize",
        action="store_true",
        help=f"Move loose files into {PRESETS_DIR_NAME}/, {TOOLKIT_DATA_DIR_NAME}/onboard/, or archive/",
    )

    sub.add_parser("status", help="Show G Hub and preset paths")

    p_recover = sub.add_parser(
        "recover",
        help=(
            "Fix G502 stuck Inactive without rebooting: restart agent, then "
            "re-enumerate mouse and its USB hub chain (admin prompt; macOS)"
        ),
    )
    p_recover.add_argument(
        "--max-level",
        type=int,
        default=None,
        help="Highest hub level to re-enumerate (0=mouse only; default: whole chain)",
    )
    p_recover.add_argument(
        "--dry-run",
        action="store_true",
        help="Diagnose only; take no action",
    )

    sub.add_parser(
        "backup",
        help=f"Copy settings.db to {TOOLKIT_DATA_DIR_NAME}/archive/ (safe snapshot before changes)",
    )
    sub.add_parser(
        "quit-ghub",
        help="Quit G Hub and kill background agents (menu bar)",
    )
    sub.add_parser(
        "block-updates",
        help="Stop G Hub from auto-updating (admin/sudo required)",
    )
    sub.add_parser(
        "unblock-updates",
        help="Undo block-updates (admin/sudo required)",
    )

    p_compare = sub.add_parser(
        "compare",
        help="Compare onboard pull JSON to G Hub export (Rosetta stone)",
    )
    p_compare.add_argument(
        "--onboard",
        type=Path,
        required=True,
        help="Onboard pull file (onboard_raw_slotN.json or preset with ommRaw)",
    )
    p_compare.add_argument(
        "--ghub",
        type=Path,
        required=True,
        help="G Hub export .lghub-preset.json (e.g. from upload-from-device)",
    )
    p_compare.add_argument(
        "--output",
        type=Path,
        help="Write rosetta report JSON to this path",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "presets_dir", None) is None and hasattr(args, "command"):
        if args.command == "replace":
            args.presets_dir = str(default_presets_dir())

    handlers = {
        "list": cmd_list,
        "export": cmd_export,
        "import": cmd_import,
        "replace": cmd_replace,
        "pull": cmd_pull,
        "duplicate": cmd_duplicate,
        "remove": cmd_remove,
        "sync": cmd_sync,
        "compare": cmd_compare,
        "status": cmd_status,
        "recover": cmd_recover,
        "backup": cmd_backup,
        "quit-ghub": cmd_quit_ghub,
        "block-updates": cmd_block_updates,
        "unblock-updates": cmd_unblock_updates,
    }
    return handlers[args.command](args)


def main_entry() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    main_entry()
