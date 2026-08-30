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


INSTALLED_INGRESS_HELPER = "/usr/local/libexec/sandbox-ingress-helper"


def parse_helper_listeners(text: str) -> dict:
    """{(address, port): evidence} from the helper's fixed-shape output."""
    found = {}
    for line in text.splitlines():
        columns = line.split()
        if len(columns) not in (5, 6, 7):
            continue
        address, port, pid, command, executable = columns[:5]
        start = columns[5] if len(columns) == 6 else "-"
        if len(columns) == 7:
            start = columns[5]
        executable_digest = columns[6] if len(columns) == 7 else "-"
        if not port.isdigit():
            continue
        address = address.strip("[]")
        if address in {"*", ""}:
            # `ss` prints a dual-stack wildcard as `*`. /proc reports the same
            # socket as `::`, and an IPv4-only wildcard as `0.0.0.0`, so record
            # both keys or the attribution never matches the observation.
            for family in ("::", "0.0.0.0"):
                found[(family, int(port))] = {
                    "pid": int(pid) if pid.isdigit() else None,
                    "command": None if command == "-" else command,
                    "executable": None if executable == "-" else executable,
                    "start": None if start == "-" else start,
                    "executable_digest": (None if executable_digest == "-"
                                          else executable_digest),
                }
            continue
        try:
            ipaddress.ip_address(address)
        except ValueError:
            continue
        found[(address, int(port))] = {
            "pid": int(pid) if pid.isdigit() else None,
            "command": None if command == "-" else command,
            "executable": None if executable == "-" else executable,
            "start": None if start == "-" else start,
            "executable_digest": (None if executable_digest == "-"
                                  else executable_digest),
        }
    return found


class ListenerObserver:
    def __init__(self, *, platform="linux", read_text=None, process=None,
                 ipv6_dual_stack=None, proc_root="/proc",
                 privileged_helper=INSTALLED_INGRESS_HELPER):
        self.platform = platform
        self.read_text = read_text or (lambda path: Path(path).read_text())
        self.process = process
        self.ipv6_dual_stack = ipv6_dual_stack
        self.proc_root = Path(proc_root)
        # An unprivileged caller cannot read /proc/<pid>/fd for a root-owned
        # incumbent, so the documented conformance target (system Caddy under
        # systemd) is invisible without this read-only privileged fallback.
        self.privileged_helper = privileged_helper

    def _attribute_privileged(self, endpoints):
        if not endpoints or self.process is None or not self.privileged_helper:
            return endpoints
        if all(item.process for item in endpoints):
            return endpoints
        result = self.process.run(
            ("sudo", "-n", self.privileged_helper, "listeners"), timeout=10,
        )
        if getattr(result, "returncode", 1) != 0:
            return endpoints
        found = parse_helper_listeners(getattr(result, "stdout", "") or "")
        if not found:
            return endpoints
        from dataclasses import replace
        attributed = []
        for item in endpoints:
            evidence = found.get((item.address, item.port))
            if item.process or not evidence or not evidence.get("pid"):
                attributed.append(item)
                continue
            attributed.append(replace(
                item, process=evidence,
                owner_confidence="proven" if evidence.get("executable") else "probable",
            ))
        return tuple(attributed)

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
            return self._attribute_privileged(
                enrich_linux_processes(tuple(values), self.proc_root))
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
            executable = os.path.realpath(process_dir / "exe")
            if not os.path.isabs(executable) or not os.path.exists(executable):
                executable = None
        except OSError:
            executable = None
        try:
            start = (process_dir / "stat").read_text().split()[21]
        except (OSError, IndexError):
            start = None
        evidence = {"pid": int(process_dir.name), "start": start,
                    "command": command, "executable": executable}
        for inode in matched:
            found[inode] = evidence
    from dataclasses import replace
    return tuple(replace(item, process=found[item.socket_id],
                         owner_confidence=("proven" if found[item.socket_id].get("executable")
                                           else "probable"))
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
