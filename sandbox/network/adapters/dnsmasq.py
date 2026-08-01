"""Transactional adapter for a declared incumbent dnsmasq include directory."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile

from sandbox.config.domains import normalize_hostname


class DnsmasqAdapter:
    def __init__(self, *, config_directory: str, owned_directory: str | None,
                 process, validate_argv=("dnsmasq", "--test"),
                 reload_argv=("systemctl", "reload", "dnsmasq")) -> None:
        if not owned_directory:
            raise ValueError("dnsmasq adapter requires an owned include directory")
        configured = Path(config_directory).resolve()
        owned = Path(owned_directory).resolve()
        if owned == configured or configured not in owned.parents:
            raise ValueError("owned dnsmasq directory must be below its configured include root")
        self.config_directory = configured
        self.owned_directory = owned
        self.process = process
        self.validate_argv = tuple(validate_argv)
        self.reload_argv = tuple(reload_argv)

    def plan(self, hostname: str, address: str, *, wildcard: bool = False) -> dict:
        hostname = normalize_hostname(hostname)
        name = hostname if not wildcard else hostname.removeprefix("*.")
        identity = hashlib.sha256(name.encode()).hexdigest()[:20]
        text = (
            f"# sandbox-resolver v1 name={name}\n"
            f"address=/{name}/{address}\n"
            f"local=/{name}/\n"
        )
        return {"kind": "zone" if wildcard else "exact", "hostname": name,
                "address": address, "path": str(self.owned_directory / f"{identity}.conf"),
                "content": text, "content_digest": hashlib.sha256(text.encode()).hexdigest()}

    def _validate(self) -> None:
        result = self.process.run(self.validate_argv, timeout=10)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or "dnsmasq validation failed")[:1000])

    @staticmethod
    def _write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
        try:
            os.fchmod(descriptor, 0o644)
            with os.fdopen(descriptor, "w") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def apply(self, plan: dict) -> dict:
        path = Path(plan["path"]).resolve()
        if self.owned_directory not in path.parents or path.suffix != ".conf":
            raise ValueError("dnsmasq plan path is outside the owned directory")
        prior = path.read_text() if path.exists() and not path.is_symlink() else None
        if path.is_symlink() or (prior is not None and not prior.startswith("# sandbox-resolver v1 ")):
            return {"ok": False, "mutated": False, "error": "foreign dnsmasq fragment collision"}
        self._validate()
        self._write(path, plan["content"])
        try:
            self._validate()
            result = self.process.run(self.reload_argv, timeout=20)
            if result.returncode != 0:
                raise RuntimeError((result.stderr or "dnsmasq reload failed")[:1000])
        except Exception as exc:
            if prior is None:
                path.unlink(missing_ok=True)
            else:
                self._write(path, prior)
            self.process.run(self.reload_argv, timeout=20)
            return {"ok": False, "mutated": False, "error": str(exc)}
        return {"ok": True, "mutated": prior != plan["content"], "applied": {
            "path": str(path), "content_digest": plan["content_digest"]}}

    def cleanup(self, binding) -> dict:
        desired = dict(binding.desired)
        path = Path(desired["path"])
        if not path.exists():
            return {"ok": True, "mutated": False}
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != desired["content_digest"]:
            return {"ok": False, "mutated": False, "error": "dnsmasq fragment drifted"}
        prior = path.read_text()
        path.unlink()
        result = self.process.run(self.reload_argv, timeout=20)
        if result.returncode != 0:
            self._write(path, prior)
            return {"ok": False, "mutated": False, "error": "dnsmasq reload failed"}
        return {"ok": True, "mutated": True}

    def rollback(self, plan: dict) -> dict:
        path = Path(plan["path"])
        digest = (plan.get("applied") or {}).get("content_digest") \
            or plan.get("content_digest")
        if not path.exists():
            return {"ok": True, "mutated": False}
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            return {"ok": False, "mutated": False,
                    "error": "dnsmasq fragment drifted"}
        prior = path.read_text()
        path.unlink()
        result = self.process.run(self.reload_argv, timeout=20)
        if result.returncode != 0:
            self._write(path, prior)
            return {"ok": False, "mutated": False, "error": "dnsmasq reload failed"}
        return {"ok": True, "mutated": True}

    def observe(self, binding) -> dict | None:
        desired = dict(binding.desired)
        path = Path(desired["path"])
        if not path.exists() or path.is_symlink():
            return None
        return {"path": str(path),
                "content_digest": hashlib.sha256(path.read_bytes()).hexdigest()}
