"""Fresh resolver answer and ingress-backend verification."""

from __future__ import annotations

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
        if not actual.intersection(accepted_addresses):
            return False
        return bool(self.http.probe(fallback_url, timeout=5))
