"""Adapter for an explicitly declared incumbent dnsmasq include directory."""

from pathlib import Path


class DnsmasqAdapter:
    def __init__(self, *, config_directory: str, owned_directory: str | None, process) -> None:
        if not owned_directory:
            raise ValueError("dnsmasq adapter requires an owned include directory")
        configured = Path(config_directory).resolve()
        owned = Path(owned_directory).resolve()
        if owned == configured or configured not in owned.parents:
            raise ValueError("owned dnsmasq directory must be below its configured include root")
        self.config_directory = configured
        self.owned_directory = owned
        self.process = process
