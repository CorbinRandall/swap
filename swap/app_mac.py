#!/usr/bin/env python3
"""Swap — compact macOS window for selecting an onboard mouse preset."""

from __future__ import annotations

import ctypes
import threading
from typing import Any, Callable

import objc
from AppKit import (
    NSApp,
    NSApplication,
    NSApplicationActivationPolicyRegular,
    NSBackingStoreBuffered,
    NSColor,
    NSFont,
    NSMakeRect,
    NSPopUpButton,
    NSTextField,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSObject
from PyObjCTools import AppHelper

from .engine import Engine, EngineStatus
from .hid_worker import run_hid
from .tcc import listen_event_granted
from .choices import (
    acquire_startup_status,
    active_choice_index,
    connection_message,
    preset_choices,
)

_CONTROLLER = None


def _alert(title: str, message: str) -> None:
    from AppKit import NSAlert

    alert = NSAlert.alloc().init()
    alert.setMessageText_(title)
    alert.setInformativeText_(message)
    alert.addButtonWithTitle_("OK")
    NSApp.activateIgnoringOtherApps_(True)
    alert.runModal()


def _request_input_monitoring_if_needed() -> None:
    if listen_event_granted():
        return
    iokit = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/IOKit.framework/IOKit")
    iokit.IOHIDRequestAccess(1)
    _alert(
        "Swap needs Input Monitoring",
        "Enable Swap in System Settings → Privacy & Security → Input Monitoring, "
        "then reopen the app.",
    )


class SwapWindowController(NSObject):
    def init(self):
        self = objc.super(SwapWindowController, self).init()
        if self is None:
            return None
        self.engine = Engine()
        self._choices = []
        self._busy = False
        self._build_window()
        AppHelper.callAfter(self.refresh)
        return self

    def _build_window(self) -> None:
        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskMiniaturizable
        )
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(160, 120, 380, 116), style, NSBackingStoreBuffered, False
        )
        self.window.setTitle_("Swap")
        self.window.setReleasedWhenClosed_(False)
        self.window.setDelegate_(self)

        content = self.window.contentView()
        self.selector = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(18, 62, 344, 28), False
        )
        self.selector.setTarget_(self)
        self.selector.setAction_("presetChanged:")
        self.selector.addItemWithTitle_("Scanning mouse…")
        self.selector.setEnabled_(False)
        content.addSubview_(self.selector)

        self.status = NSTextField.labelWithString_("Reading onboard presets…")
        self.status.setFrame_(NSMakeRect(20, 25, 340, 20))
        self.status.setFont_(NSFont.systemFontOfSize_(12))
        self.status.setTextColor_(NSColor.secondaryLabelColor())
        self.status.setLineBreakMode_(4)
        content.addSubview_(self.status)

        self.window.center()
        self.window.makeKeyAndOrderFront_(None)

    def windowShouldClose_(self, _sender) -> bool:
        NSApp.terminate_(None)
        return False

    def _run(
        self,
        work: Callable[[], Any],
        done: Callable[[Any], None],
        *,
        timeout: float = 15.0,
    ) -> None:
        if self._busy:
            return
        self._busy = True
        self.selector.setEnabled_(False)

        def runner() -> None:
            try:
                result = run_hid(work, timeout=timeout)
            except Exception as exc:  # noqa: BLE001
                AppHelper.callAfter(self._failed, exc)
            else:
                AppHelper.callAfter(self._finished, done, result)

        threading.Thread(target=runner, name="swap-ui", daemon=True).start()

    def _failed(self, error: Exception) -> None:
        self._busy = False
        self.status.setStringValue_(str(error))
        self.selector.setEnabled_(bool(self._choices))
        _alert("Swap", str(error))

    def _finished(self, done: Callable[[Any], None], result: Any) -> None:
        self._busy = False
        done(result)

    def refresh(self) -> None:
        self.status.setStringValue_("Reading onboard presets…")
        self._run(lambda: acquire_startup_status(self.engine), self._apply_status)

    def _apply_status(self, status: EngineStatus) -> None:
        self._choices = preset_choices(status)
        self.selector.removeAllItems()
        self.status.setStringValue_(connection_message(status))
        if not self._choices:
            self.selector.addItemWithTitle_("No onboard presets found")
            self.selector.setEnabled_(False)
            return

        for choice in self._choices:
            self.selector.addItemWithTitle_(choice.label)
        index = active_choice_index(status, self._choices)
        self.selector.selectItemAtIndex_(index)
        self.selector.setEnabled_(True)
        self.window.setTitle_(f"Swap — {self._choices[index].label}")

    def presetChanged_(self, _sender) -> None:
        index = self.selector.indexOfSelectedItem()
        if self._busy or index < 0 or index >= len(self._choices):
            return
        choice = self._choices[index]
        self.status.setStringValue_(f"Switching to {choice.label}…")

        def switched(_result: None) -> None:
            self.status.setStringValue_("Preset switched")
            self.window.setTitle_(f"Swap — {choice.label}")
            self.selector.setEnabled_(True)

        self._run(lambda: self.engine.set_active_slot(choice.slot), switched)


def main() -> None:
    global _CONTROLLER
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    _request_input_monitoring_if_needed()
    _CONTROLLER = SwapWindowController.alloc().init()
    app.activateIgnoringOtherApps_(True)
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
