"""Documented public CLI plans for Herd and official Valet."""

from sandbox.config.domains import normalize_hostname


class IncumbentResolverAdapter:
    def __init__(self, product: str, executable: str, process=None) -> None:
        if product not in {"herd", "valet"}:
            raise ValueError("unsupported incumbent resolver")
        self.product = product
        self.executable = executable
        self.process = process

    def plan(self, hostname: str, address: str) -> dict:
        hostname = normalize_hostname(hostname)
        return {"kind": "incumbent", "hostname": hostname, "address": address,
                "argv": (self.executable, "status"), "documented_surface": True,
                "mutation_owner": "ingress"}

    def apply(self, plan: dict) -> dict:
        if self.process is None:
            return {"ok": False, "mutated": False, "error": "incumbent CLI unavailable"}
        result = self.process.run(plan["argv"], timeout=10)
        return {"ok": result.returncode == 0, "mutated": False,
                "applied": {"hostname": plan["hostname"], "verified_existing": True},
                "error": (result.stderr or "")[:1000]}

    def cleanup(self, _binding) -> dict:
        return {"ok": True, "mutated": False}
