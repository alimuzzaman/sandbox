"""Kernel-authoritative host TCP listener observation."""

from __future__ import annotations

import ipaddress
import socket
from pathlib import Path

from .models import ListenerEndpoint


def _linux_address(hex_value: str, family: str) -> str:
    raw = bytes.fromhex(hex_value)
    if family == "ipv4":
        return socket.inet_ntop(socket.AF_INET, raw[::-1])
    # /proc/net/tcp6 stores each 32-bit word in host byte order.
    normalized = b"".join(raw[index:index + 4][::-1] for index in range(0, 16, 4))
    return socket.inet_ntop(socket.AF_INET6, normalized)


def parse_linux_proc(text: str, *, family: str, dual_stack: bool = False):
    endpoints = []
    for line in text.splitlines()[1:]:
        columns = line.split()
        if len(columns) < 10 or columns[3] != "0A":
            continue
        address_hex, port_hex = columns[1].rsplit(":", 1)
        endpoints.append(ListenerEndpoint(
            _linux_address(address_hex, family), int(port_hex, 16),
            dual_stack=dual_stack and int(address_hex, 16) == 0,
            socket_id=columns[9],
        ))
    return tuple(endpoints)


class ListenerObserver:
    def __init__(self, *, platform="linux", read_text=None, process=None,
                 ipv6_dual_stack=False):
        self.platform = platform
        self.read_text = read_text or (lambda path: Path(path).read_text())
        self.process = process
        self.ipv6_dual_stack = ipv6_dual_stack

    def snapshot(self) -> tuple[ListenerEndpoint, ...]:
        if self.platform == "linux":
            values = []
            for path, family in (("/proc/net/tcp", "ipv4"), ("/proc/net/tcp6", "ipv6")):
                try:
                    values.extend(parse_linux_proc(
                        self.read_text(path), family=family,
                        dual_stack=self.ipv6_dual_stack if family == "ipv6" else False,
                    ))
                except OSError:
                    continue
            return tuple(values)
        if self.platform in {"darwin", "macos"} and self.process is not None:
            result = self.process.run(("lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-Fpcn"), timeout=2)
            return parse_macos_lsof(result.stdout if result.returncode == 0 else "")
        return ()

    def conflicts(self, requested: ListenerEndpoint):
        return tuple(item for item in self.snapshot() if item.overlaps(requested))


def parse_macos_lsof(text: str) -> tuple[ListenerEndpoint, ...]:
    endpoints, pid, command = [], None, None
    for line in text.splitlines():
        if line.startswith("p"):
            pid = line[1:]
            command = None
        elif line.startswith("c"):
            command = line[1:]
        elif line.startswith("n"):
            value = line[1:].split("->", 1)[0]
            address, separator, port = value.rpartition(":")
            address = address.strip("[]")
            if not separator or not port.isdigit():
                continue
            address = "0.0.0.0" if address == "*" else address
            try:
                ipaddress.ip_address(address)
            except ValueError:
                continue
            endpoints.append(ListenerEndpoint(
                address, int(port), process={"pid": pid, "command": command},
                owner_confidence="probable" if pid else "unknown",
            ))
    return tuple(endpoints)
