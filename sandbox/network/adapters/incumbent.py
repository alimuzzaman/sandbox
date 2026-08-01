"""Documented public CLI plans for Herd and official Valet."""

from sandbox.config.domains import normalize_hostname


class IncumbentResolverAdapter:
    def __init__(self, product: str, executable: str) -> None:
        if product not in {"herd", "valet"}:
            raise ValueError("unsupported incumbent resolver")
        self.product = product
        self.executable = executable

    def plan(self, hostname: str, address: str) -> dict:
        hostname = normalize_hostname(hostname)
        command = "link" if self.product == "valet" else "park"
        return {"kind": "incumbent", "hostname": hostname, "address": address,
                "argv": (self.executable, command), "documented_surface": True}
