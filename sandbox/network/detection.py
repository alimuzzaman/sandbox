"""Bounded, read-only observation of the effective host resolver."""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import platform as host_platform
import re
from typing import Callable

from .models import ResolverObservation


_TOKEN = re.compile(r"(?<![0-9A-Fa-f:.])([0-9A-Fa-f:.]+)(?![0-9A-Fa-f:.])")


def _answers(text: str) -> tuple[str, ...]:
    values = set()
    for token in _TOKEN.findall(text or ""):
        try:
            values.add(str(ipaddress.ip_address(token)))
        except ValueError:
            continue
    return tuple(sorted(values))


class ResolverDetector:
    def __init__(self, *, process, platform: str | None = None,
                 readlink: Callable[[str], str] | None = None,
                 read_text: Callable[[str], str] | None = None,
                 exists: Callable[[str], bool] | None = None) -> None:
        self.process = process
        self.platform = (platform or host_platform.system()).lower()
        self.readlink = readlink or os.readlink
        self.read_text = read_text or (lambda path: Path(path).read_text(errors="replace"))
        self.exists = exists or os.path.exists

    def _run(self, argv) -> object:
        return self.process.run(argv, timeout=2)

    def observe(self, hostname: str) -> ResolverObservation:
        evidence: list[str] = []
        if self.platform.startswith("linux"):
            try:
                version = self.read_text("/proc/version")[:500]
            except OSError:
                version = ""
            if "microsoft" in version.lower() or "wsl" in version.lower():
                return ResolverObservation.create(
                    owner_id="wsl2:windows-resolver", manager="unknown", mode="wsl2",
                    support_tier="outside_platform", extension={},
                    evidence=("WSL2 resolver is Windows-managed",),
                )
            try:
                link = self.readlink("/etc/resolv.conf")
            except OSError:
                link = ""
            evidence.append(f"resolv.conf-link:{link or 'regular-file'}"[:500])
            resolved = self._run(("resolvectl", "status"))
            if resolved.returncode == 0 and (
                "systemd/resolve" in link or "resolv.conf mode:" in resolved.stdout
            ):
                query = self._run(("resolvectl", "query", hostname))
                evidence.append((resolved.stdout or "")[:1000])
                if query.returncode == 0:
                    evidence.append((query.stdout or "")[:500])
                return ResolverObservation.create(
                    owner_id="systemd-resolved:host", manager="resolved",
                    mode="stub" if "stub" in (link + resolved.stdout).lower() else "routed",
                    support_tier="implemented_unproven",
                    extension={"kind": "route-only-domain", "global_takeover": False},
                    current_answers=_answers(query.stdout if query.returncode == 0 else ""),
                    evidence=tuple(evidence),
                )
            try:
                text = self.read_text("/etc/resolv.conf")[:1000]
            except OSError:
                text = ""
            evidence.append(text)
            return ResolverObservation.create(
                owner_id="unknown:resolv-conf", manager="unknown", mode="unidentified",
                support_tier="detect_only", extension={}, evidence=tuple(evidence),
            )
        if self.platform in {"darwin", "macos"}:
            result = self._run(("scutil", "--dns"))
            return ResolverObservation.create(
                owner_id="macos:scoped-resolver", manager="macos", mode="scoped",
                support_tier="implemented_unproven" if result.returncode == 0 else "unavailable",
                extension={"kind": "etc-resolver"} if result.returncode == 0 else {},
                evidence=((result.stdout or result.stderr or "")[:1500],),
            )
        return ResolverObservation.create(
            owner_id=f"{self.platform}:outside", manager="unknown", mode="outside-platform",
            support_tier="outside_platform", extension={}, evidence=("unsupported platform",),
        )
