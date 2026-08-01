"""Exact-name-only hosts fallback; mutations require the fixed helper."""

from sandbox.config.domains import normalize_hostname


class HostsAdapter:
    def __init__(self, *, helper: str, process) -> None:
        self.helper = helper
        self.process = process

    def plan(self, hostname: str, address: str) -> dict:
        if hostname.startswith("*."):
            raise ValueError("hosts adapter does not support wildcard names")
        return {"kind": "exact", "hostname": normalize_hostname(hostname),
                "address": address, "marker": "sandbox-resolver-v1"}

    def apply(self, _plan):
        raise RuntimeError("hosts mutation remains proof-gated")
