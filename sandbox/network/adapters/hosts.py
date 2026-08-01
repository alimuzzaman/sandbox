"""Exact-name-only hosts fallback; mutations require the fixed helper."""

from pathlib import Path

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
        plan = _plan
        result = self.process.run((
            "sudo", "-n", self.helper, "hosts-apply",
            plan["hostname"], plan["address"],
        ), timeout=30)
        return {"ok": result.returncode == 0,
                "mutated": result.returncode == 0 and
                           (result.stdout or "").strip() != "unchanged",
                "applied": {"hostname": plan["hostname"], "address": plan["address"]},
                "error": (result.stderr or "")[:1000]}

    def cleanup(self, binding):
        desired = dict(binding.desired)
        result = self.process.run((
            "sudo", "-n", self.helper, "hosts-remove",
            desired["hostname"], desired["address"],
        ), timeout=30)
        return {"ok": result.returncode == 0, "mutated": result.returncode == 0,
                "error": (result.stderr or "")[:1000]}

    def rollback(self, plan: dict) -> dict:
        desired = plan.get("applied") or plan
        result = self.process.run((
            "sudo", "-n", self.helper, "hosts-remove",
            desired["hostname"], desired["address"],
        ), timeout=30)
        return {"ok": result.returncode == 0, "mutated": result.returncode == 0,
                "error": (result.stderr or "")[:1000]}

    def observe(self, binding) -> dict | None:
        desired = dict(binding.desired)
        hostname, address = desired["hostname"], desired["address"]
        begin = f"# sandbox-resolver-v1 begin {hostname}"
        end = f"# sandbox-resolver-v1 end {hostname}"
        try:
            lines = Path("/etc/hosts").read_text(errors="replace").splitlines()
        except OSError:
            return None
        expected = [begin, f"{address} {hostname}", end]
        for index in range(max(0, len(lines) - 2)):
            if lines[index:index + 3] == expected:
                return {"hostname": hostname, "address": address}
        return None
