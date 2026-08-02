"""Fresh resolver answer and ingress-backend verification."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from .detection import _answers


class DomainVerifier:
    def __init__(self, *, process, http, platform: str) -> None:
        self.process = process
        self.http = http
        self.platform = platform

    def verify(self, hostname: str, accepted_addresses: tuple[str, ...],
               fallback_url: str) -> bool:
        command = (
            ("resolvectl", "query", "--cache=no", hostname)
            if self.platform == "linux" else
            ("dscacheutil", "-q", "host", "-a", "name", hostname)
        )
        result = self.process.run(command, timeout=5)
        if result.returncode != 0:
            return False
        actual = set(_answers(result.stdout))
        if len(actual) != 1 or not actual.issubset(accepted_addresses):
            return False
        try:
            parsed = urlsplit(fallback_url)
            if parsed.scheme != "http" or parsed.username or parsed.password:
                return False
            fallback_host = parsed.hostname
            if fallback_host == "localhost":
                fallback_address = "127.0.0.1"
            else:
                candidate = ipaddress.ip_address(fallback_host or "")
                if not candidate.is_loopback:
                    return False
                fallback_address = str(candidate)
            fallback_port = parsed.port or 80
        except (ValueError, TypeError):
            return False
        probe_route = getattr(self.http, "probe_route", None)
        if probe_route is None:
            return False
        return bool(probe_route(
            fallback_address, fallback_port, hostname, timeout=5,
        ))
