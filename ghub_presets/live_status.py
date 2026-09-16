"""Live G Hub device status via local agent WebSocket (no UI clicking).

G Hub UI "Inactive" maps to agent device state NOT_CONNECTED (and similar
non-ACTIVE states). Agents should call summarize_live_status() instead of
asking the user whether the mouse looks Inactive.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import socket
import ssl
import struct
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

AGENT_WS_DEFAULT = "ws://127.0.0.1:9010/"
AGENT_SUBPROTOCOL = "json"

# UI-facing labels for agent Device.Info.state values (from lghub_ui).
STATE_UI = {
    "ACTIVE": "Active",
    "NOT_CONNECTED": "Inactive",
    "ABSENT": "Absent",
    "PENDING": "Pending",
    "LOADING_RESOURCES": "Loading",
    "REQUIRES_UPDATE": "Needs update",
    "REQUIRES_ASSISTANCE": "Needs assistance",
    "INITIALIZING": "Initializing",
}

INACTIVE_LIKE = frozenset(
    {
        "NOT_CONNECTED",
        "ABSENT",
        "PENDING",
        "LOADING_RESOURCES",
        "INITIALIZING",
        "REQUIRES_ASSISTANCE",
    }
)

HIDFILTER_BUNDLE = "com.logi.ghub.hidfilter"


@dataclass
class DeviceLiveInfo:
    id: str
    display_name: str
    model: str
    state: str
    ui_label: str
    onboard_mode: bool
    connection_type: str
    pid: int | None = None
    path: str = ""


@dataclass
class LiveStatus:
    agent_reachable: bool
    devices: list[DeviceLiveInfo] = field(default_factory=list)
    hidfilter_state: str | None = None
    hid_iface0_ok: bool | None = None
    hid_iface1_ok: bool | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def any_inactive(self) -> bool:
        return any(d.state in INACTIVE_LIKE for d in self.devices)

    def summary_lines(self) -> list[str]:
        lines: list[str] = []
        if not self.agent_reachable:
            lines.append("G Hub agent API: unreachable (is lghub_agent running on :9010?)")
        else:
            lines.append("G Hub agent API: ok (ws://127.0.0.1:9010 json)")
            if not self.devices:
                lines.append("Devices: none reported")
            for d in self.devices:
                onboard = "onboard" if d.onboard_mode else "software"
                lines.append(
                    f"Device: {d.display_name} ({d.model}) — "
                    f"{d.ui_label} [agent state={d.state}, {onboard}, {d.connection_type}]"
                )
            if self.any_inactive:
                lines.append(
                    "UI Inactive detected from agent state (no screenshot needed)."
                )

        if sys.platform == "darwin":
            if self.hidfilter_state:
                lines.append(f"HID filter dext: {self.hidfilter_state}")
                if "waiting_for_user" in self.hidfilter_state.replace(" ", "_"):
                    lines.append(
                        "ACTION: enable Logitech G HUB HID Driver Extension in "
                        "System Settings → General → Login Items & Extensions → "
                        "Driver Extensions (this blocks HID++ / causes Inactive)."
                    )
            else:
                lines.append("HID filter dext: not listed (install/approve via G Hub)")

            if self.hid_iface0_ok is not None or self.hid_iface1_ok is not None:
                lines.append(
                    f"HID open: iface0={'ok' if self.hid_iface0_ok else 'fail'}, "
                    f"iface1/HID++={'ok' if self.hid_iface1_ok else 'fail'}"
                )

        lines.extend(self.notes)
        return lines


class _WsClient:
    """Minimal WebSocket client (text frames) using stdlib only."""

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self._buf = bytearray()

    @classmethod
    def connect(cls, url: str, *, subprotocol: str, timeout: float = 3.0) -> "_WsClient":
        parsed = urlparse(url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        raw = socket.create_connection((host, port), timeout=timeout)
        raw.settimeout(timeout)
        if parsed.scheme == "wss":
            ctx = ssl.create_default_context()
            raw = ctx.wrap_socket(raw, server_hostname=host)

        key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"Sec-WebSocket-Protocol: {subprotocol}\r\n"
            f"\r\n"
        )
        raw.sendall(req.encode("ascii"))
        header = b""
        while b"\r\n\r\n" not in header:
            chunk = raw.recv(4096)
            if not chunk:
                raise ConnectionError("WebSocket handshake closed")
            header += chunk
        head, _, rest = header.partition(b"\r\n\r\n")
        status = head.split(b"\r\n", 1)[0]
        if b"101" not in status:
            raise ConnectionError(f"WebSocket upgrade failed: {status.decode('latin1')}")
        expected = base64.b64encode(
            hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
            ).digest()
        ).decode("ascii")
        if expected.encode("ascii") not in head:
            raise ConnectionError("WebSocket accept mismatch")
        client = cls(raw)
        client._buf.extend(rest)
        return client

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def send_text(self, text: str) -> None:
        payload = text.encode("utf-8")
        header = bytearray([0x81])
        mask_bit = 0x80
        n = len(payload)
        if n < 126:
            header.append(mask_bit | n)
        elif n < 65536:
            header.append(mask_bit | 126)
            header.extend(struct.pack("!H", n))
        else:
            header.append(mask_bit | 127)
            header.extend(struct.pack("!Q", n))
        mask = os.urandom(4)
        header.extend(mask)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(header + masked)

    def recv_text(self) -> str:
        while True:
            opcode, payload = self._read_frame()
            if opcode == 0x8:  # close
                raise ConnectionError("WebSocket closed")
            if opcode == 0x9:  # ping
                self._send_control(0xA, payload)
                continue
            if opcode == 0xA:  # pong
                continue
            if opcode in (0x1, 0x2):
                return payload.decode("utf-8") if opcode == 0x1 else payload.hex()

    def _send_control(self, opcode: int, payload: bytes) -> None:
        if len(payload) > 125:
            payload = payload[:125]
        mask = os.urandom(4)
        header = bytes([0x80 | opcode, 0x80 | len(payload)]) + mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(header + masked)

    def _read_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(max(4096, n - len(self._buf)))
            if not chunk:
                raise ConnectionError("socket closed")
            self._buf.extend(chunk)
        data = bytes(self._buf[:n])
        del self._buf[:n]
        return data

    def _read_frame(self) -> tuple[int, bytes]:
        b1, b2 = self._read_exact(2)
        opcode = b1 & 0x0F
        masked = bool(b2 & 0x80)
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read_exact(8))[0]
        mask = self._read_exact(4) if masked else b""
        payload = self._read_exact(length)
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return opcode, payload


def agent_request(
    verb: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    url: str = AGENT_WS_DEFAULT,
    timeout: float = 3.0,
) -> dict[str, Any]:
    client = _WsClient.connect(url, subprotocol=AGENT_SUBPROTOCOL, timeout=timeout)
    try:
        # Hello OPTIONS from backend
        try:
            client.recv_text()
        except (socket.timeout, TimeoutError, OSError):
            pass
        msg: dict[str, Any] = {"msgId": "1", "verb": verb, "path": path}
        if payload is not None:
            msg["payload"] = payload
        client.send_text(json.dumps(msg))
        deadline = timeout
        import time

        end = time.monotonic() + deadline
        while time.monotonic() < end:
            raw = client.recv_text()
            data = json.loads(raw)
            if data.get("msgId") == "1" or (
                data.get("path") == path and data.get("verb") == verb
            ):
                return data
        raise TimeoutError(f"no response for {verb} {path}")
    finally:
        client.close()


def fetch_devices_simplified(*, timeout: float = 3.0) -> list[DeviceLiveInfo]:
    data = agent_request("GET", "/devices/list/simplified", timeout=timeout)
    if (data.get("result") or {}).get("code") != "SUCCESS":
        raise RuntimeError(f"agent error: {data.get('result')}")
    infos = (data.get("payload") or {}).get("deviceInfos") or []
    out: list[DeviceLiveInfo] = []
    for info in infos:
        state = str(info.get("state") or "UNKNOWN")
        out.append(
            DeviceLiveInfo(
                id=str(info.get("id") or ""),
                display_name=str(info.get("displayName") or info.get("extendedDisplayName") or "?"),
                model=str(info.get("deviceModel") or ""),
                state=state,
                ui_label=STATE_UI.get(state, state),
                onboard_mode=bool(info.get("onboardMode")),
                connection_type=str(info.get("connectionType") or ""),
                pid=int(info["pid"]) if info.get("pid") is not None else None,
                path=str(info.get("path") or ""),
            )
        )
    return out


def mac_hidfilter_state() -> str | None:
    if sys.platform != "darwin":
        return None
    try:
        out = subprocess.check_output(
            ["systemextensionsctl", "list"],
            text=True,
            stderr=subprocess.STDOUT,
            timeout=5,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None
    for line in out.splitlines():
        if HIDFILTER_BUNDLE not in line:
            continue
        # Trailing [...] state token
        m = re.search(r"\[([^\]]+)\]\s*$", line.strip())
        if m:
            return m.group(1)
        return line.strip()
    return None


def probe_g502_hid_open() -> tuple[bool | None, bool | None]:
    """Return (iface0_ok, iface1_ok) for Logitech G502 wired PID if present."""
    try:
        import hid
    except ImportError:
        return None, None

    iface0: bool | None = None
    iface1: bool | None = None
    try:
        for d in hid.enumerate(0x046D, 0xC332):
            iface = d.get("interface_number")
            try:
                h = hid.device()
                h.open_path(d["path"])
                h.close()
                ok = True
            except Exception:
                ok = False
            if iface == 0:
                iface0 = True if ok else bool(iface0)
            elif iface == 1:
                iface1 = True if ok else bool(iface1)
    except Exception:
        pass
    return iface0, iface1


def _agent_holds_g502_hidpp() -> bool | None:
    """True if lghub_agent has an IOHIDLibUserClient on G502 iface 1."""
    if sys.platform != "darwin":
        return None
    try:
        text = subprocess.check_output(
            ["ioreg", "-r", "-n", "Gaming Mouse G502", "-l", "-w0"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None
    iface: str | None = None
    for line in text.splitlines():
        if "IOUSBHostInterface@" in line:
            m = re.search(r"IOUSBHostInterface@(\d+)", line)
            iface = m.group(1) if m else iface
        if iface == "1" and "IOUserClientCreator" in line and "lghub_agent" in line:
            return True
    return False


def _iface1_foreign_clients() -> list[str]:
    """Non-system processes with HID clients on G502 iface 1 (can break HID++)."""
    if sys.platform != "darwin":
        return []
    try:
        text = subprocess.check_output(
            ["ioreg", "-r", "-n", "Gaming Mouse G502", "-l", "-w0"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return []
    ignore = ("WindowServer", "TouchBarServer", "loginwindow", "lghub_agent")
    iface: str | None = None
    found: list[str] = []
    for line in text.splitlines():
        if "IOUSBHostInterface@" in line:
            m = re.search(r"IOUSBHostInterface@(\d+)", line)
            iface = m.group(1) if m else iface
        if iface != "1" or "IOUserClientCreator" not in line:
            continue
        m = re.search(r'IOUserClientCreator" = "pid \d+, ([^"]+)"', line)
        if not m:
            continue
        name = m.group(1)
        if name not in ignore and name not in found:
            found.append(name)
    return found


def summarize_live_status() -> LiveStatus:
    status = LiveStatus(agent_reachable=False)
    fw_info: list[Any] | None = None
    active_interfaces: list[Any] | None = None
    try:
        status.devices = fetch_devices_simplified()
        status.agent_reachable = True
        try:
            full = agent_request("GET", "/devices/list")
            infos = (full.get("payload") or {}).get("deviceInfos") or []
            if infos:
                fw_info = infos[0].get("fwInfo")
                active_interfaces = infos[0].get("activeInterfaces")
        except Exception:
            pass
    except Exception as exc:
        status.notes.append(f"agent probe failed: {exc}")

    if sys.platform == "darwin":
        status.hidfilter_state = mac_hidfilter_state()
        status.hid_iface0_ok, status.hid_iface1_ok = probe_g502_hid_open()
        if status.hidfilter_state and "waiting for user" in status.hidfilter_state:
            status.notes.append(
                "Root cause candidate: HID Driver Extension not user-approved."
            )

        agent_holds = _agent_holds_g502_hidpp()
        foreigners = _iface1_foreign_clients()
        if foreigners and status.any_inactive:
            status.notes.append(
                "HID++ iface1 also opened by: "
                + ", ".join(foreigners)
                + " (quit these if Inactive persists)."
            )

        if status.any_inactive and agent_holds:
            empty_fw = fw_info is not None and len(fw_info) == 0
            empty_ifaces = active_interfaces is not None and len(active_interfaces) == 0
            if empty_fw or empty_ifaces:
                status.notes.append(
                    "Agent opened HID++ but protocol is stuck "
                    f"(fwInfo={fw_info!r}, activeInterfaces={active_interfaces!r}). "
                    "Known wedge: kernel USB state for the hub chain is stale — "
                    "replugging the mouse does NOT fix it. "
                    "Run 'ghub-presets recover' (re-enumerates mouse + hub chain, "
                    "admin prompt); last resorts: power-cycle the dock/KVM, reboot."
                )
            if status.hid_iface1_ok is False:
                status.notes.append(
                    "Note: userspace HID++ open_fail is expected while lghub_agent holds the device."
                )
        elif status.hid_iface1_ok is False and status.any_inactive:
            status.notes.append(
                "HID++ interface will not open — G Hub cannot activate the mouse."
            )
    return status
