"""systemd-resolved scoped route-only adapter."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile


class ResolvedAdapter:
    def __init__(self, *, process, helper: str, network_root: str | Path,
                 readlink=os.readlink) -> None:
        self.process = process
        self.helper = helper
        self.network_root = Path(network_root).expanduser().resolve()
        self.authority_root = self.network_root / "authority"
        self.authority_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.readlink = readlink

    def plan(self, suffix: str, address: str, port: int) -> dict:
        return {"kind": "resolved-route", "suffix": suffix, "address": address,
                "port": port, "global_takeover": False}

    def _candidate(self, suffix: str, address: str, port: int) -> Path:
        text = (
            f"# sandbox-resolver v1 suffix={suffix}\n[Resolve]\n"
            f"DNS={address}:{port}\nDomains=~{suffix}\n"
        )
        path = self.authority_root / f"resolved-{suffix}.conf"
        descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return path

    def apply(self, suffix, address=None, port=None) -> dict:
        if isinstance(suffix, dict):
            plan = suffix
            suffix, address, port = plan["suffix"], plan["address"], plan["port"]
        before = self.readlink("/etc/resolv.conf")
        candidate = self._candidate(suffix, address, int(port))
        result = self.process.run((
            "sudo", "-n", self.helper, "resolved-apply", str(self.network_root),
            str(candidate), suffix, address, str(port),
        ), timeout=30)
        if result.returncode != 0:
            return {"ok": False, "mutated": False,
                    "error": (result.stderr or "resolver helper failed")[:1000]}
        after = self.readlink("/etc/resolv.conf")
        if after != before:
            return {"ok": False, "mutated": True,
                    "error": "resolver-managed resolv.conf relationship changed"}
        return {"ok": True, "mutated": (result.stdout or "").strip() != "unchanged",
                "applied": {
            "suffix": suffix, "address": address, "port": int(port),
            "fragment_digest": hashlib.sha256(candidate.read_bytes()).hexdigest(),
            "resolv_conf_link": before,
        }}

    def rollback(self, plan: dict) -> dict:
        digest = (plan.get("applied") or plan).get("fragment_digest")
        if not digest:
            return {"ok": False, "mutated": False}
        result = self.process.run((
            "sudo", "-n", self.helper, "resolved-remove", plan["suffix"], digest,
        ), timeout=30)
        return {"ok": result.returncode == 0, "mutated": result.returncode == 0}
