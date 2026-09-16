#!/usr/bin/env python3
"""G — macOS Dock window UI for G502 onboard memory."""

from __future__ import annotations

import sys
import threading
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

# Fresh pool each submit is intentional — a stuck HID call must not block the next.

_STATUS_TIMEOUT_S = 8.0

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import ctypes  # noqa: E402

import objc  # noqa: E402
from AppKit import (  # noqa: E402
    NSApp,
    NSApplication,
    NSApplicationActivationPolicyRegular,
    NSBackingStoreBuffered,
    NSBezelStyleRounded,
    NSBox,
    NSBoxSeparator,
    NSButton,
    NSEdgeInsetsMake,
    NSFont,
    NSMakeRect,
    NSMenu,
    NSMenuItem,
    NSPopUpButton,
    NSStackView,
    NSStackViewGravityTop,
    NSTextField,
    NSUserInterfaceLayoutOrientationHorizontal,
    NSUserInterfaceLayoutOrientationVertical,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSObject, NSTimer  # noqa: E402
from PyObjCTools import AppHelper  # noqa: E402

from gcore.engine import Engine, EngineError, EngineStatus  # noqa: E402
from gcore.library import LibraryWatcher  # noqa: E402
from gcore.paths import open_in_file_manager, presets_dir  # noqa: E402

_IOHID_REQUEST_LISTEN = 1
_BUNDLE_ID = "io.bytecode.g-onboard"
_CONTROLLER = None


from gcore.tcc import listen_event_auth as _tcc_listen_event_auth


def input_monitoring_status() -> int:
    """Return 0=granted, 1=denied, 2=not determined (IOHIDCheckAccess convention)."""
    tcc = _tcc_listen_event_auth()
    if tcc == 2:
        return 0
    if tcc == 0:
        return 1
    if tcc is None:
        # No row yet → not determined
        pass
    elif tcc not in (0, 2):
        return 2

    iokit = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/IOKit.framework/IOKit")
    return int(iokit.IOHIDCheckAccess(_IOHID_REQUEST_LISTEN))


def request_input_monitoring() -> bool:
    iokit = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/IOKit.framework/IOKit")
    return bool(iokit.IOHIDRequestAccess(_IOHID_REQUEST_LISTEN))


def open_input_monitoring_settings() -> None:
    import subprocess

    for url in (
        "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_ListenEvent",
        "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent",
    ):
        try:
            subprocess.run(["open", url], check=False)
            return
        except OSError:
            continue


def permission_help_text() -> str:
    status = input_monitoring_status()
    label = {0: "granted", 1: "denied", 2: "not determined"}.get(status, str(status))
    tcc = _tcc_listen_event_auth()
    tcc_note = f" (TCC auth_value={tcc})" if tcc is not None else ""
    if status == 0:
        return (
            f"Input Monitoring: {label}{tcc_note}\n\n"
            "Permission looks fine. If the mouse still won’t connect: quit G Hub, "
            "wake/replug the mouse or Lightspeed receiver, and Scan Mouse.\n"
            "If another tool owns the mouse (BetterTouchTool), quit it briefly."
        )
    return (
        f"Input Monitoring: {label}{tcc_note}\n\n"
        "Enable G under System Settings → Privacy & Security → Input Monitoring.\n"
        "Do this once — install_macos.sh signs with a stable identity and keeps "
        "the nested python binary on the same bundle id, so rebuilds should not "
        "require toggling again."
    )


def choose_folder_dialog(title: str = "Choose presets folder") -> Path | None:
    from AppKit import NSOpenPanel

    panel = NSOpenPanel.openPanel()
    panel.setCanChooseFiles_(False)
    panel.setCanChooseDirectories_(True)
    panel.setAllowsMultipleSelection_(False)
    panel.setCanCreateDirectories_(True)
    panel.setTitle_(title)
    panel.setPrompt_("Choose")
    NSApp.activateIgnoringOtherApps_(True)
    if panel.runModal() != 1:
        return None
    urls = panel.URLs()
    if not urls:
        return None
    return Path(str(urls[0].path())).resolve()


def _alert(title: str, message: str, ok: str = "OK", cancel: str | None = None) -> int:
    from AppKit import NSAlert, NSAlertFirstButtonReturn

    alert = NSAlert.alloc().init()
    alert.setMessageText_(title)
    alert.setInformativeText_(message)
    alert.addButtonWithTitle_(ok)
    if cancel:
        alert.addButtonWithTitle_(cancel)
    NSApp.activateIgnoringOtherApps_(True)
    return 1 if alert.runModal() == NSAlertFirstButtonReturn else 0


def _label(text: str, *, bold: bool = False, size: float = 13) -> NSTextField:
    field = NSTextField.labelWithString_(text)
    field.setFont_(
        NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size)
    )
    field.setSelectable_(True)
    field.setMaximumNumberOfLines_(0)
    return field


def _button(title: str, target, action: str) -> NSButton:
    btn = NSButton.buttonWithTitle_target_action_(title, target, action)
    btn.setBezelStyle_(NSBezelStyleRounded)
    return btn


def _separator() -> NSBox:
    line = NSBox.alloc().initWithFrame_(NSMakeRect(0, 0, 200, 1))
    line.setBoxType_(NSBoxSeparator)
    return line


def _active_slot_name(st: EngineStatus) -> str:
    if st.active_slot is None:
        return "?"
    for slot in st.slots:
        if slot.index == st.active_slot:
            return slot.name or f"Slot {slot.index}"
    return f"Slot {st.active_slot}"


def _pull_down_menu(title: str) -> NSPopUpButton:
    btn = NSPopUpButton.alloc().initWithFrame_pullsDown_(
        NSMakeRect(0, 0, 460, 28), True
    )
    btn.setAutoenablesItems_(False)
    btn.setTitle_(title)
    btn.setEnabled_(True)
    menu = NSMenu.alloc().initWithTitle_(title)
    menu.setAutoenablesItems_(False)
    btn.setMenu_(menu)
    return btn


def _select_popup(title: str, target, action: str) -> NSPopUpButton:
    btn = NSPopUpButton.alloc().initWithFrame_pullsDown_(
        NSMakeRect(0, 0, 460, 28), False
    )
    btn.setTarget_(target)
    btn.setAction_(action)
    btn.addItemWithTitle_(title)
    return btn


class GWindowController(NSObject):
    def init(self):
        self = objc.super(GWindowController, self).init()
        if self is None:
            return None
        self.engine = Engine()
        self._last_status: EngineStatus | None = None
        self._busy = False
        self._refreshing_menus = False
        self._worker_lock = threading.Lock()
        self._library_timer = None
        self._watcher = LibraryWatcher(
            folder=self.engine.library_dir,
            on_change=self._on_library_change,
        )
        self._build_window()
        self._watcher.start()
        # Presets are available without the mouse — show them immediately.
        self._apply_status(
            EngineStatus(
                device_key=None,
                device_label="No device",
                connected=False,
                onboard_mode=None,
                active_slot=None,
                slots=[],
                ghub_running=False,
                ghub_blocking=False,
                library_count=0,
                library_path=str(self.engine.library_dir),
                last_error="",
                note="Connecting to mouse…",
            ),
            self.engine.list_library(),
        )
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.2, self, "startupTick:", None, False
        )
        return self

    def _build_window(self) -> None:
        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskMiniaturizable
            | NSWindowStyleMaskResizable
        )
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(160, 120, 500, 160),
            style,
            NSBackingStoreBuffered,
            False,
        )
        self.window.setTitle_("Swap")
        self.window.setMinSize_((420, 150))
        self.window.setReleasedWhenClosed_(False)
        self.window.setDelegate_(self)

        stack = NSStackView.alloc().initWithFrame_(NSMakeRect(0, 0, 480, 140))
        stack.setOrientation_(NSUserInterfaceLayoutOrientationVertical)
        stack.setSpacing_(10)
        stack.setEdgeInsets_(NSEdgeInsetsMake(16, 16, 16, 16))

        self.active_slot_popup = _select_popup(
            "Active profile", self, "activeSlotChanged:"
        )
        stack.addView_inGravity_(self.active_slot_popup, NSStackViewGravityTop)

        self.preset_mgmt_btn = _pull_down_menu("Preset Management")
        stack.addView_inGravity_(self.preset_mgmt_btn, NSStackViewGravityTop)

        self.quit_ghub_btn = _button("Quit G Hub", self, "quitGHub:")
        self.quit_btn = _button("Quit Swap", self, "quitApp:")
        footer = NSStackView.stackViewWithViews_([self.quit_ghub_btn, self.quit_btn])
        footer.setOrientation_(NSUserInterfaceLayoutOrientationHorizontal)
        footer.setSpacing_(8)
        stack.addView_inGravity_(footer, NSStackViewGravityTop)

        self.window.setContentView_(stack)
        self.window.center()
        self.window.makeKeyAndOrderFront_(None)

    def _populate_active_slot_popup(self, st: EngineStatus, mouse_ok: bool) -> None:
        popup = self.active_slot_popup
        by_index = {s.index: s for s in st.slots}
        titles = []
        select_idx = 0

        for i in (1, 2, 3):
            info = by_index.get(i)
            name = info.name if info else "—"
            titles.append(f"Slot {i}: {name}")
            if st.active_slot == i:
                select_idx = i - 1
            elif info and info.active:
                select_idx = i - 1

        if popup.numberOfItems() == 3:
            changed = False
            for i, title in enumerate(titles):
                if popup.itemTitleAtIndex_(i) != title:
                    changed = True
                    break
            if (
                not changed
                and st.slots
                and popup.indexOfSelectedItem() == select_idx
                and popup.isEnabled() == bool(mouse_ok and st.slots)
            ):
                return

        popup.removeAllItems()
        for title in titles:
            popup.addItemWithTitle_(title)
        if st.slots:
            popup.selectItemAtIndex_(select_idx)
        popup.setEnabled_(bool(mouse_ok and st.slots))

    def _populate_preset_mgmt_menu(
        self, st: EngineStatus, library, mouse_ok: bool
    ) -> None:
        title = "Preset Management"
        menu = NSMenu.alloc().initWithTitle_(title)
        menu.setAutoenablesItems_(False)

        for label, kind in (
            ("Choose Presets Folder…", "choose_folder"),
            ("Open Presets Folder", "open_folder"),
        ):
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                label, "presetMgmtAction:", ""
            )
            item.setTarget_(self)
            item.setRepresentedObject_({"kind": kind})
            menu.addItem_(item)

        menu.addItem_(NSMenuItem.separatorItem())

        replace = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Replace All from Folder", "presetMgmtAction:", ""
        )
        replace.setTarget_(self)
        replace.setRepresentedObject_({"kind": "replace_all"})
        replace.setEnabled_(bool(mouse_ok and library))
        menu.addItem_(replace)

        extract_all = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Extract All from Mouse", "presetMgmtAction:", ""
        )
        extract_all.setTarget_(self)
        extract_all.setRepresentedObject_({"kind": "extract_all"})
        extract_all.setEnabled_(bool(mouse_ok))
        menu.addItem_(extract_all)

        menu.addItem_(NSMenuItem.separatorItem())

        scan = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Scan Mouse", "presetMgmtAction:", ""
        )
        scan.setTarget_(self)
        scan.setRepresentedObject_({"kind": "scan_mouse"})
        menu.addItem_(scan)

        self.preset_mgmt_btn.setMenu_(menu)
        self.preset_mgmt_btn.setTitle_(title)
        self.preset_mgmt_btn.setEnabled_(True)

    def _update_window_title(self, st: EngineStatus) -> None:
        if st.ghub_blocking:
            self.window.setTitle_("Swap — quit G Hub")
        elif st.connected:
            self.window.setTitle_(f"Swap — {_active_slot_name(st)}")
        else:
            self.window.setTitle_("Swap — mouse not connected")

    def _preserve_slot_names(
        self, st: EngineStatus, previous: EngineStatus | None
    ) -> EngineStatus:
        if previous is None or not st.slots:
            return st
        old_by = {s.index: s for s in previous.slots}
        slots = []
        changed = False
        for slot in st.slots:
            name = slot.name
            if name == f"Slot {slot.index}" and slot.index in old_by:
                old_name = old_by[slot.index].name
                if old_name and old_name != name:
                    name = old_name
                    changed = True
            slots.append(slot if name == slot.name else replace(slot, name=name))
        return st if not changed else replace(st, slots=slots)

    def _patch_active_slot(self, slot: int) -> None:
        st = self._last_status
        if st is None or not st.slots:
            self.refreshAsync()
            return
        slots = [
            replace(s, active=(s.index == slot))
            for s in st.slots
        ]
        new_st = replace(st, active_slot=slot, slots=slots)
        self._apply_status(new_st, self.engine.list_library())

    def _menu_payload(self, sender) -> dict | None:
        if hasattr(sender, "selectedItem"):
            item = sender.selectedItem()
        else:
            item = sender
        if item is None:
            return None
        payload = item.representedObject()
        if not isinstance(payload, dict):
            return None
        return payload

    def windowShouldClose_(self, _sender) -> bool:
        self.quitApp_(None)
        return False

    def startupTick_(self, _timer) -> None:
        # Only prompt when this process truly lacks ListenEvent.
        # Do not nag about "stale rebuild" — install_macos.sh keeps a stable
        # signing identity so Input Monitoring should stick across rebuilds.
        if input_monitoring_status() != 0:
            request_input_monitoring()
            open_input_monitoring_settings()
            _alert("Swap needs Input Monitoring", permission_help_text())
        self.refreshAsync(read_names=True)

    def _on_library_change(self) -> None:
        if self._library_timer is not None:
            self._library_timer.invalidate()
        self._library_timer = (
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                0.35, self, "libraryChangedTick:", None, False
            )
        )

    def libraryChangedTick_(self, timer) -> None:
        timer.invalidate()
        self._library_timer = None
        if self._last_status is None:
            return
        library = self.engine.list_library()
        mouse_ok = self._last_status.connected and not self._last_status.ghub_blocking
        self._refreshing_menus = True
        try:
            self._populate_preset_mgmt_menu(self._last_status, library, mouse_ok)
        finally:
            self._refreshing_menus = False

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy

    def _run_bg(
        self,
        work: Callable[[], Any],
        *,
        busy_message: str,
        on_ok: Callable[[Any], None] | None = None,
        on_err: Callable[[Exception], None] | None = None,
        timeout: float = 120.0,
        preserve_on_error: bool = False,
    ) -> None:
        if not self._worker_lock.acquire(blocking=False):
            return
        self._set_busy(True)

        def runner() -> None:
            try:
                from gcore.hid_worker import run_hid

                result = run_hid(work, timeout=timeout)
            except Exception as exc:  # noqa: BLE001
                err = exc

                def fail() -> None:
                    self._worker_lock.release()
                    self._set_busy(False)
                    if not preserve_on_error:
                        try:
                            self._apply_status(
                                EngineStatus(
                                    device_key=None,
                                    device_label="No device",
                                    connected=False,
                                    onboard_mode=None,
                                    active_slot=None,
                                    slots=[],
                                    ghub_running=False,
                                    ghub_blocking=False,
                                    library_count=0,
                                    library_path=str(self.engine.library_dir),
                                    last_error=str(err),
                                    note=str(err).split("\n")[0],
                                ),
                                self.engine.list_library(),
                            )
                        except Exception:
                            traceback.print_exc()
                    if on_err:
                        on_err(err)

                AppHelper.callAfter(fail)
                return

            def ok() -> None:
                self._worker_lock.release()
                self._set_busy(False)
                if on_ok:
                    on_ok(result)

            AppHelper.callAfter(ok)

        threading.Thread(target=runner, name="g-worker", daemon=True).start()

    def _show_error(self, exc: Exception) -> None:
        message = str(exc)
        if "open failed" in message.lower():
            if input_monitoring_status() != 0:
                request_input_monitoring()
                open_input_monitoring_settings()
            message = f"{message}\n\n{permission_help_text()}"
        _alert("Swap", message)

    def _status_with_timeout(self, *, read_names: bool = False) -> EngineStatus:
        return self.engine.status(read_names=read_names, open_attempts=1)

    def refreshAsync(
        self,
        *,
        alert_on_error: bool = False,
        read_names: bool = False,
        preserve_on_error: bool = True,
    ) -> None:
        def work():
            return (
                self._status_with_timeout(read_names=read_names),
                self.engine.list_library(),
            )

        def ok(result) -> None:
            st, library = result
            try:
                self._apply_status(st, library)
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                if alert_on_error:
                    _alert("Swap", f"UI refresh failed:\n{exc}")

        def err(exc: Exception) -> None:
            if isinstance(exc, TimeoutError) and preserve_on_error:
                return
            if isinstance(exc, TimeoutError):
                st = EngineStatus(
                    device_key=None,
                    device_label="No device",
                    connected=False,
                    onboard_mode=None,
                    active_slot=None,
                    slots=[],
                    ghub_running=False,
                    ghub_blocking=False,
                    library_count=0,
                    library_path=str(self.engine.library_dir),
                    last_error="timeout",
                    note=(
                        f"Mouse did not respond within {_STATUS_TIMEOUT_S:.0f}s.\n"
                        "Use Preset Management → Scan Mouse."
                    ),
                )
                try:
                    self._apply_status(st, self.engine.list_library())
                except Exception:
                    traceback.print_exc()
                return
            if alert_on_error:
                self._show_error(exc)

        self._run_bg(
            work,
            busy_message="",
            on_ok=ok,
            on_err=err,
            timeout=_STATUS_TIMEOUT_S,
            preserve_on_error=preserve_on_error,
        )

    def _apply_status(self, st: EngineStatus, library) -> None:
        previous = self._last_status
        st = self._preserve_slot_names(st, previous)
        self._last_status = st
        mouse_ok = st.connected and not st.ghub_blocking
        self._update_window_title(st)

        self.quit_ghub_btn.setTitle_(
            "Quit G Hub (required)" if st.ghub_blocking else "Quit G Hub"
        )
        self.quit_ghub_btn.setEnabled_(bool(st.ghub_blocking))

        self._refreshing_menus = True
        try:
            self._populate_active_slot_popup(st, mouse_ok)
            self._populate_preset_mgmt_menu(st, library, mouse_ok)
        finally:
            self._refreshing_menus = False

    def activeSlotChanged_(self, sender) -> None:
        if self._refreshing_menus or self._busy:
            return
        slot = int(sender.indexOfSelectedItem()) + 1
        st = self._last_status
        if st and st.active_slot == slot:
            return

        def work():
            self.engine.set_active_slot(slot)
            return slot

        def ok(result_slot: int) -> None:
            self._patch_active_slot(result_slot)

        def err(exc: Exception) -> None:
            self._show_error(exc)
            if self._last_status:
                self._apply_status(self._last_status, self.engine.list_library())

        self._run_bg(
            work,
            busy_message=f"Switching to slot {slot}…",
            on_ok=ok,
            on_err=err,
            timeout=15.0,
        )

    def presetMgmtAction_(self, sender) -> None:
        if self._refreshing_menus:
            return
        payload = self._menu_payload(sender)
        if not payload:
            return
        kind = payload.get("kind")
        if kind == "choose_folder":
            self.chooseFolder_(None)
        elif kind == "open_folder":
            self.openFolder_(None)
        elif kind == "replace_all":
            self.replaceAll_(None)
        elif kind == "extract_all":
            self.extractAll_(None)
        elif kind == "scan_mouse":
            self.scanMouse_(None)

    def _scan_after_operation(self, summary: str) -> None:
        def work():
            return self._status_with_timeout(read_names=True)

        def ok(st: EngineStatus) -> None:
            self._apply_status(st, self.engine.list_library())
            _alert("Swap", summary)

        def err(_exc: Exception) -> None:
            _alert(
                "Swap",
                f"{summary}\n\nCould not refresh from mouse — use Scan Mouse.",
            )

        self._run_bg(
            work,
            busy_message="",
            on_ok=ok,
            on_err=err,
            timeout=_STATUS_TIMEOUT_S,
            preserve_on_error=True,
        )

    def chooseFolder_(self, _sender) -> None:
        chosen = choose_folder_dialog()
        if chosen is None:
            return
        try:
            self.engine.set_library_dir(chosen, persist=True)
            self._watcher.set_folder(chosen)
        except EngineError as exc:
            self._show_error(exc)
        self.refreshAsync()

    def openFolder_(self, _sender) -> None:
        open_in_file_manager(self.engine.library_dir)

    def replaceAll_(self, _sender) -> None:
        if not self.engine.list_library():
            _alert("Swap", "Library is empty — choose a presets folder first.")
            return
        if (
            _alert(
                "Replace all onboard slots?",
                "Overwrite slots 1–3 from your presets folder?\n\n"
                "Uses onboardSlot metadata when there are three or fewer files. "
                "If there are more than three, only the first three by filename (A→Z) "
                "are used. Each slot is archived first.",
                "Replace All",
                "Cancel",
            )
            != 1
        ):
            return

        def work():
            return self.engine.assign_all_from_library()

        def ok(result) -> None:
            results, skipped = result
            summary = "\n".join(f"Slot {s}: {name}" for s, name in results)
            if skipped:
                summary += "\n\nSkipped:\n" + "\n".join(f"· {n}" for n in skipped)
            self._scan_after_operation(summary)

        self._run_bg(work, busy_message="Replacing all slots…", on_ok=ok)

    def extractAll_(self, _sender) -> None:
        if (
            _alert(
                "Extract all slots?",
                "Pull slots 1–3 from the mouse into your presets folder?",
                "Extract All",
                "Cancel",
            )
            != 1
        ):
            return

        def work():
            return self.engine.extract_all_slots()

        def ok(paths) -> None:
            summary = "Saved:\n" + "\n".join(p.name for p in paths)
            self._scan_after_operation(summary)

        self._run_bg(work, busy_message="Extracting all slots…", on_ok=ok)

    def scanMouse_(self, _sender) -> None:
        if input_monitoring_status() != 0:
            request_input_monitoring()

        def work():
            return self._status_with_timeout(read_names=True)

        def ok(st: EngineStatus) -> None:
            self._apply_status(st, self.engine.list_library())
            im = {0: "granted", 1: "denied", 2: "not determined"}.get(
                input_monitoring_status(), "?"
            )
            lines = [
                f"Input Monitoring: {im}",
                f"Library: {st.library_path}",
                f"Presets in library: {len(self.engine.list_library())}",
                "",
            ]
            if st.ghub_blocking:
                lines.append(st.note or "G Hub is running.")
            elif not st.connected:
                lines.append(st.note or "Mouse not found.")
                if im == "granted":
                    lines.append("")
                    lines.append(
                        "Input Monitoring is enabled. If HID++ is still blocked, turn OFF "
                        "“Logitech G HUB HID Driver” under Login Items → Driver Extensions, "
                        "then unplug/replug the mouse and Scan again."
                    )
                else:
                    lines.append("")
                    lines.append(permission_help_text())
            else:
                lines.append(f"Active: {_active_slot_name(st)} (slot {st.active_slot})")
                lines.append(f"Device: {st.device_label}")
                for slot in st.slots:
                    mark = "●" if slot.active else "○"
                    lines.append(f"{mark} Slot {slot.index}: {slot.name}")
            _alert("Swap · Mouse Scan", "\n".join(lines))

        self._run_bg(
            work,
            busy_message="Scanning mouse…",
            on_ok=ok,
            timeout=_STATUS_TIMEOUT_S,
            preserve_on_error=False,
        )

    def quitGHub_(self, _sender) -> None:
        self._run_bg(
            lambda: self.engine.quit_ghub(),
            busy_message="Quitting G Hub…",
        )

    def quitApp_(self, _sender) -> None:
        if self._library_timer is not None:
            self._library_timer.invalidate()
            self._library_timer = None
        self._watcher.stop()
        NSApp.terminate_(None)


def main() -> None:
    global _CONTROLLER
    print(f"G presets library: {presets_dir()}", flush=True)
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    _CONTROLLER = GWindowController.alloc().init()
    app.activateIgnoringOtherApps_(True)
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
