"""macOS scoped `/etc/resolver` adapter plan boundary."""

from pathlib import Path

from sandbox.config.domains import normalize_tld


class MacosResolverAdapter:
    def __init__(self, *, helper: str, process, staging_root: str | Path) -> None:
        self.helper = helper
        self.process = process
        self.staging_root = Path(staging_root).resolve()

    def plan(self, suffix: str, address: str, port: int) -> dict:
        suffix = normalize_tld(suffix)
        return {"kind": "macos-resolver", "suffix": suffix, "address": address,
                "port": int(port), "destination": f"/etc/resolver/{suffix}"}
