"""macOS scoped `/etc/resolver` adapter boundary."""

import hashlib
import os
from pathlib import Path
import tempfile

from sandbox.config.domains import normalize_tld


class MacosResolverAdapter:
    def __init__(self, *, helper: str, process, staging_root: str | Path) -> None:
        self.helper = helper
        self.process = process
        self.staging_root = Path(staging_root).resolve()
        self.authority_root = self.staging_root / "authority"

    def plan(self, suffix: str, address: str, port: int) -> dict:
        suffix = normalize_tld(suffix)
        content = (f"# sandbox-resolver v1 suffix={suffix}\n"
                   f"nameserver {address}\nport {int(port)}\n")
        candidate = self.authority_root / f"macos-{suffix}.resolver"
        return {"kind": "macos-resolver", "suffix": suffix, "address": address,
                "port": int(port), "destination": f"/etc/resolver/{suffix}",
                "candidate": str(candidate), "content": content}

    def apply(self, plan: dict) -> dict:
        self.authority_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        candidate = Path(plan["candidate"])
        descriptor, temporary = tempfile.mkstemp(
            prefix=candidate.name + ".", dir=self.staging_root,
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                stream.write(plan["content"])
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, candidate)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        result = self.process.run((
            "sudo", "-n", self.helper, "macos-apply", str(self.staging_root),
            str(candidate), plan["suffix"], plan["address"], str(plan["port"]),
        ), timeout=30)
        return {"ok": result.returncode == 0,
                "mutated": result.returncode == 0 and
                           (result.stdout or "").strip() != "unchanged",
                "applied": {"destination": plan["destination"],
                            "content_digest": hashlib.sha256(
                                plan["content"].encode(),
                            ).hexdigest()},
                "error": (result.stderr or "")[:1000]}

    def cleanup(self, binding) -> dict:
        desired = dict(binding.desired)
        applied = dict(binding.last_applied or {})
        digest = applied.get("content_digest")
        if not digest:
            return {"ok": False, "mutated": False,
                    "error": "macOS resolver ownership digest is unavailable"}
        result = self.process.run((
            "sudo", "-n", self.helper, "macos-remove",
            desired["suffix"], digest,
        ), timeout=30)
        return {"ok": result.returncode == 0, "mutated": result.returncode == 0,
                "error": (result.stderr or "")[:1000]}

    def rollback(self, plan: dict) -> dict:
        digest = (plan.get("applied") or {}).get("content_digest")
        if not digest:
            return {"ok": False, "mutated": False,
                    "error": "macOS resolver ownership digest is unavailable"}
        result = self.process.run((
            "sudo", "-n", self.helper, "macos-remove", plan["suffix"], digest,
        ), timeout=30)
        return {"ok": result.returncode == 0, "mutated": result.returncode == 0,
                "error": (result.stderr or "")[:1000]}
