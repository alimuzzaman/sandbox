"""Kernel-authoritative host TCP listener observation."""

from __future__ import annotations

import ipaddress
import errno
import os
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


def parse_linux_proc(text: str, *, family: str, dual_stack: bool | None = None):
    endpoints = []
    for line in text.splitlines()[1:]:
        columns = line.split()
        if len(columns) < 10 or columns[3] != "0A":
            continue
        address_hex, port_hex = columns[1].rsplit(":", 1)
        endpoints.append(ListenerEndpoint(
            _linux_address(address_hex, family), int(port_hex, 16),
            dual_stack=dual_stack if family == "ipv6" and int(address_hex, 16) == 0 else None,
            socket_id=columns[9],
        ))
    return tuple(endpoints)


class ListenerObserver:
    def __init__(self, *, platform="linux", read_text=None, process=None,
                 ipv6_dual_stack=None, proc_root="/proc"):
        self.platform = platform
        self.read_text = read_text or (lambda path: Path(path).read_text())
        self.process = process
        self.ipv6_dual_stack = ipv6_dual_stack
        self.proc_root = Path(proc_root)

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
            return enrich_linux_processes(tuple(values), self.proc_root)
        if self.platform in {"darwin", "macos"} and self.process is not None:
            result = self.process.run(("lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-Fpcn"), timeout=2)
            return parse_macos_lsof(result.stdout if result.returncode == 0 else "")
        return ()

    def conflicts(self, requested: ListenerEndpoint):
        return tuple(item for item in self.snapshot() if item.overlaps(requested))


class SocketBindProbe:
    """Momentarily bind without listen; EADDRINUSE is kernel conflict truth."""
    def check(self, endpoint: ListenerEndpoint) -> str:
        family = socket.AF_INET if endpoint.family == "ipv4" else socket.AF_INET6
        probe = socket.socket(family, socket.SOCK_STREAM)
        try:
            probe.bind((endpoint.address, endpoint.port))
            return "free"
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                return "conflict"
            if exc.errno in {errno.EACCES, errno.EPERM, errno.EADDRNOTAVAIL}:
                return "unavailable"
            raise
        finally:
            probe.close()


def enrich_linux_processes(endpoints, proc_root=Path("/proc")):
    wanted = {item.socket_id for item in endpoints if item.socket_id}
    if not wanted:
        return tuple(endpoints)
    found = {}
    try:
        processes = tuple(path for path in Path(proc_root).iterdir() if path.name.isdigit())
    except OSError:
        return tuple(endpoints)
    for process_dir in processes:
        try:
            links = tuple((process_dir / "fd").iterdir())
        except OSError:
            continue
        matched = set()
        for link in links:
            try:
                target = os.readlink(link)
            except OSError:
                continue
            if target.startswith("socket:[") and target.endswith("]"):
                inode = target[8:-1]
                if inode in wanted:
                    matched.add(inode)
        if not matched:
            continue
        try:
            command = (process_dir / "comm").read_text(errors="replace").strip()
        except OSError:
            command = None
        try:
            start = (process_dir / "stat").read_text().split()[21]
        except (OSError, IndexError):
            start = None
        evidence = {"pid": int(process_dir.name), "start": start, "command": command}
        for inode in matched:
            found[inode] = evidence
    from dataclasses import replace
    return tuple(replace(item, process=found[item.socket_id],
                         owner_confidence="probable")
                 if item.socket_id in found else item for item in endpoints)


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
