#!/usr/bin/env python3
"""Swap — compact native Windows UI for selecting an onboard mouse preset."""

from __future__ import annotations

import ctypes
import queue
import threading
from ctypes import wintypes
from typing import Any, Callable

from .engine import Engine, EngineStatus
from .choices import (
    acquire_startup_status,
    active_choice_index,
    connection_message,
    preset_choices,
)

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
gdi32 = ctypes.windll.gdi32
LRESULT = ctypes.c_ssize_t

kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HWND,
    wintypes.HMENU,
    wintypes.HINSTANCE,
    ctypes.c_void_p,
]
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
]
user32.LoadCursorW.restype = wintypes.HANDLE
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.SendMessageW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
]
gdi32.GetStockObject.restype = wintypes.HANDLE

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_SETFONT = 0x0030
WM_COMMAND = 0x0111
WM_APP_RESULT = 0x8001
CBN_SELCHANGE = 1
CB_GETCURSEL = 0x0147
CB_RESETCONTENT = 0x014B
CB_SETCURSEL = 0x014E
CB_ADDSTRING = 0x0143
SW_SHOW = 5

WS_OVERLAPPED = 0x00000000
WS_CAPTION = 0x00C00000
WS_SYSMENU = 0x00080000
WS_MINIMIZEBOX = 0x00020000
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_VSCROLL = 0x00200000
CBS_DROPDOWNLIST = 0x0003
SS_LEFT = 0x00000000
DEFAULT_GUI_FONT = 17

COMBO_ID = 1001

WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class SwapWindow:
    def __init__(self) -> None:
        self.engine = Engine()
        self._choices = []
        self._busy = False
        self._results: queue.Queue[tuple[bool, Any, Callable[[Any], None]]] = queue.Queue()
        self.hinstance = kernel32.GetModuleHandleW(None)
        self.hwnd = None
        self.combo = None
        self.status = None
        self._wndproc = WNDPROC(self._window_proc)
        self._create_window()

    def _create_window(self) -> None:
        class_name = "SwapPresetWindow"
        window_class = WNDCLASSW()
        window_class.lpfnWndProc = self._wndproc
        window_class.hInstance = self.hinstance
        window_class.hCursor = user32.LoadCursorW(None, 32512)  # IDC_ARROW
        window_class.hbrBackground = wintypes.HBRUSH(6)  # COLOR_WINDOW + 1
        window_class.lpszClassName = class_name
        if not user32.RegisterClassW(ctypes.byref(window_class)):
            error = ctypes.get_last_error()
            if error != 1410:  # class already exists
                raise ctypes.WinError(error)

        style = WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX
        self.hwnd = user32.CreateWindowExW(
            0, class_name, "Swap", style, 0x80000000, 0x80000000, 400, 150,
            None, None, self.hinstance, None,
        )
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())

        self.combo = user32.CreateWindowExW(
            0, "COMBOBOX", "", WS_CHILD | WS_VISIBLE | WS_VSCROLL | CBS_DROPDOWNLIST,
            18, 18, 348, 240, self.hwnd, COMBO_ID, self.hinstance, None,
        )
        self.status = user32.CreateWindowExW(
            0, "STATIC", "Reading onboard presets…", WS_CHILD | WS_VISIBLE | SS_LEFT,
            20, 64, 346, 32, self.hwnd, None, self.hinstance, None,
        )
        font = gdi32.GetStockObject(DEFAULT_GUI_FONT)
        user32.SendMessageW(self.combo, WM_SETFONT, font, True)
        user32.SendMessageW(self.status, WM_SETFONT, font, True)
        user32.EnableWindow(self.combo, False)

    def _window_proc(self, hwnd, message, wparam, lparam):  # type: ignore[no-untyped-def]
        if message == WM_COMMAND:
            control_id = int(wparam) & 0xFFFF
            notification = (int(wparam) >> 16) & 0xFFFF
            if control_id == COMBO_ID and notification == CBN_SELCHANGE:
                self._selection_changed()
                return 0
        elif message == WM_APP_RESULT:
            self._consume_result()
            return 0
        elif message == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        elif message == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _set_status(self, text: str) -> None:
        user32.SetWindowTextW(self.status, text)

    def _set_title(self, text: str) -> None:
        user32.SetWindowTextW(self.hwnd, text)

    def _combo_add(self, text: str) -> None:
        value = ctypes.create_unicode_buffer(text)
        address = ctypes.cast(value, ctypes.c_void_p).value
        user32.SendMessageW(self.combo, CB_ADDSTRING, 0, address)

    def _run(self, work: Callable[[], Any], done: Callable[[Any], None]) -> None:
        if self._busy:
            return
        self._busy = True
        user32.EnableWindow(self.combo, False)

        def runner() -> None:
            try:
                result = (True, work(), done)
            except Exception as exc:  # noqa: BLE001
                result = (False, exc, done)
            self._results.put(result)
            user32.PostMessageW(self.hwnd, WM_APP_RESULT, 0, 0)

        threading.Thread(target=runner, name="swap-hid", daemon=True).start()

    def _consume_result(self) -> None:
        try:
            ok, result, done = self._results.get_nowait()
        except queue.Empty:
            return
        self._busy = False
        if ok:
            done(result)
            return
        self._set_status(str(result))
        user32.EnableWindow(self.combo, bool(self._choices))
        user32.MessageBoxW(self.hwnd, str(result), "Swap", 0x10)

    def refresh(self) -> None:
        self._set_status("Reading onboard presets…")
        self._run(lambda: acquire_startup_status(self.engine), self._apply_status)

    def _apply_status(self, status: EngineStatus) -> None:
        self._choices = preset_choices(status)
        user32.SendMessageW(self.combo, CB_RESETCONTENT, 0, 0)
        self._set_status(connection_message(status))
        if not self._choices:
            self._combo_add("No onboard presets found")
            user32.SendMessageW(self.combo, CB_SETCURSEL, 0, 0)
            user32.EnableWindow(self.combo, False)
            return

        for choice in self._choices:
            self._combo_add(choice.label)
        index = active_choice_index(status, self._choices)
        user32.SendMessageW(self.combo, CB_SETCURSEL, index, 0)
        user32.EnableWindow(self.combo, True)
        self._set_title(f"Swap — {self._choices[index].label}")

    def _selection_changed(self) -> None:
        index = int(user32.SendMessageW(self.combo, CB_GETCURSEL, 0, 0))
        if self._busy or index < 0 or index >= len(self._choices):
            return
        choice = self._choices[index]
        self._set_status(f"Switching to {choice.label}…")

        def switched(_result: None) -> None:
            self._set_status("Preset switched")
            self._set_title(f"Swap — {choice.label}")
            user32.EnableWindow(self.combo, True)

        self._run(lambda: self.engine.set_active_slot(choice.slot), switched)

    def run(self) -> None:
        user32.ShowWindow(self.hwnd, SW_SHOW)
        user32.UpdateWindow(self.hwnd)
        self.refresh()
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))


def main() -> None:
    SwapWindow().run()


if __name__ == "__main__":
    main()
