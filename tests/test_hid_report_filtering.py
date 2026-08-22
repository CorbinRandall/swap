"""HID++ discovery must ignore unsolicited reports from the mouse."""

from __future__ import annotations

import unittest

from swap.hidpp.LogiHPP20 import LogiHPP20


class _FakePort:
    def __init__(self, response: bytes) -> None:
        notification = bytes.fromhex("11ff023f00000000000000000000000000000000")
        unrelated_error = bytes.fromhex("11ffff023f0a0000000000000000000000000000")
        self.before_write = []
        self.after_write = [notification, notification, unrelated_error, response]
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> int:
        self.writes.append(bytes(data))
        return len(data)

    def read(self, size: int = 255, timeout: int = 100) -> bytes:
        source = self.after_write if self.writes else self.before_write
        return source.pop(0) if source else b""


class HidReportFilteringTests(unittest.TestCase):
    def test_ping_skips_stale_notifications_and_unrelated_errors(self) -> None:
        request = [0x11, 0xFF, 0x00, 0x0F, 0x00, 0x05]
        response = bytes.fromhex("11ff000f04000000000000000000000000000000")
        dev = LogiHPP20.__new__(LogiHPP20)
        dev.port_short = None
        dev.port_long = _FakePort(response)
        dev.read_timeout_ms = 400
        dev.read_attempts = 1
        dev.debug = False

        self.assertEqual(dev.ping_device(request, read_back=True), response)
        self.assertEqual(len(dev.port_long.writes), 1)


if __name__ == "__main__":
    unittest.main()
