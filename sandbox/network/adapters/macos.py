"""macOS scoped `/etc/resolver` adapter boundary."""

import hashlib
from pathlib import Path

from sandbox.config.domains import normalize_tld
from sandbox.network.adapters.resolved import INSTALLED_HELPER


class MacosResolverAdapter:
    def __init__(self, *, helper: str = INSTALLED_HELPER, process,
                 staging_root: str | Path) -> None:
        if helper != INSTALLED_HELPER:
            raise ValueError("macOS resolver mutations require the fixed installed helper")
        self.helper = helper
        self.process = process
        self.staging_root = Path(staging_root).resolve()
        self.authority_root = self.staging_root / "authority"

    def plan(self, suffix: str, address: str, port: int) -> dict:
        suffix = normalize_tld(suffix)
        content = (f"# sandbox-resolver v1 suffix={suffix}\n"
                   f"nameserver {address}\nport {int(port)}\n")
        return {"kind": "macos-resolver", "suffix": suffix, "address": address,
                "port": int(port), "destination": f"/etc/resolver/{suffix}",
                "content": content}

    def apply(self, plan: dict) -> dict:
        digest = hashlib.sha256(plan["content"].encode()).hexdigest()
        result = self.process.run((
            "sudo", "-n", self.helper, "macos-apply",
            plan["owner_digest"], plan["suffix"], plan["address"],
            str(plan["port"]), digest,
        ), timeout=30)
        return {"ok": result.returncode == 0,
                "mutated": result.returncode == 0 and
                           (result.stdout or "").strip() != "unchanged",
                "applied": {"destination": plan["destination"],
                            "content_digest": digest},
                "error": (result.stderr or "")[:1000]}

    def ensure_authorized(self, plan: dict, *, interactive: bool) -> dict:
        digest = hashlib.sha256(plan["content"].encode()).hexdigest()
        args = ("macos", plan["owner_digest"], plan["suffix"],
                plan["address"], str(plan["port"]), digest)
        status = self.process.run(
            ("sudo", "-n", self.helper, "authorization-status", *args), timeout=5,
        )
        if status.returncode == 0 and (status.stdout or "").strip() == "authorized":
            return {"ok": True, "mutated": False, "digest": digest}
        if not interactive:
            return {"ok": False, "mutated": False,
                    "error": "Exact resolver authorization requires interactive approval."}
        result = self.process.run(("sudo", self.helper, "authorize", *args), timeout=120)
        return {"ok": result.returncode == 0, "mutated": result.returncode == 0,
                "digest": digest,
                "error": (result.stderr or "resolver authorization failed")[:1000]}

    def cleanup(self, binding) -> dict:
        return self.release_owner(binding, dict(binding.desired)["owner_digest"])

    def release_owner(self, binding, owner_digest: str) -> dict:
        desired = dict(binding.desired)
        applied = dict(binding.last_applied or {})
        digest = applied.get("content_digest")
        if not digest:
            return {"ok": False, "mutated": False,
                    "error": "macOS resolver ownership digest is unavailable"}
        result = self.process.run((
            "sudo", "-n", self.helper, "macos-remove",
            owner_digest, desired["suffix"], desired["address"],
            str(desired["port"]), digest,
        ), timeout=30)
        return {"ok": result.returncode == 0, "mutated": result.returncode == 0,
                "error": (result.stderr or "")[:1000]}

    def rollback(self, plan: dict) -> dict:
        digest = (plan.get("applied") or {}).get("content_digest")
        if not digest:
            return {"ok": False, "mutated": False,
                    "error": "macOS resolver ownership digest is unavailable"}
        result = self.process.run((
            "sudo", "-n", self.helper, "macos-remove", plan["owner_digest"],
            plan["suffix"], plan["address"], str(plan["port"]), digest,
        ), timeout=30)
        return {"ok": result.returncode == 0, "mutated": result.returncode == 0,
                "error": (result.stderr or "")[:1000]}

    def revoke_authorization(self, plan: dict) -> dict:
        digest = hashlib.sha256(plan["content"].encode()).hexdigest()
        result = self.process.run((
            "sudo", "-n", self.helper, "revoke-authorization", "macos",
            plan["owner_digest"],
            plan["suffix"], plan["address"], str(plan["port"]), digest,
        ), timeout=30)
        return {"ok": result.returncode == 0, "mutated": result.returncode == 0,
                "error": (result.stderr or "")[:1000]}

    def observe(self, binding) -> dict | None:
        desired = dict(binding.desired)
        destination = Path(desired["destination"])
        if not destination.exists() or destination.is_symlink():
            return None
        try:
            digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        except OSError:
            return None
        return {"destination": str(destination), "content_digest": digest}
