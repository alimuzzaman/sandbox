"""systemd-resolved scoped route-only adapter."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re


INSTALLED_HELPER = "/usr/local/libexec/sandbox-resolver-helper"
HELPER_VERSION = "sandbox-resolver-helper-v2"
_PREFLIGHT = re.compile(
    r"^sandbox-resolved-service-v1 "
    r"owner=systemd-resolved:host unit=systemd-resolved\.service "
    r"pid=([1-9][0-9]*) start=([1-9][0-9]*) uid=([0-9]+) "
    r"control=(/system\.slice/systemd-resolved\.service)$"
)


class ResolvedAdapter:
    def __init__(self, *, process, helper: str = INSTALLED_HELPER,
                 repository_helper: str | None = None, network_root: str | Path,
                 readlink=os.readlink) -> None:
        self.process = process
        if helper != INSTALLED_HELPER:
            raise ValueError("resolved mutations require the fixed installed helper")
        self.helper = helper
        self.repository_helper = repository_helper
        self.network_root = Path(network_root).expanduser().resolve()
        self.authority_root = self.network_root / "authority"
        self.readlink = readlink

    def plan(self, suffix: str, address: str, port: int) -> dict:
        return {"kind": "resolved-route", "suffix": suffix, "address": address,
                "port": port, "global_takeover": False}

    def qualification_preflight(self, observation) -> dict | None:
        """Read and bind the installed helper to the observed live service."""
        if (observation.owner_id != "systemd-resolved:host"
                or observation.manager != "resolved"):
            return None
        status = self.process.run(
            ("sudo", "-n", self.helper, "resolved-status"), timeout=5,
        )
        if status.returncode != 0:
            return None
        output = (status.stdout or "").strip()
        match = _PREFLIGHT.fullmatch(output)
        if match is None:
            return None
        pid, start_ticks, uid, control_group = match.groups()
        return {
            "schema": "sandbox-resolved-service-v1",
            "owner_id": observation.owner_id,
            "unit": "systemd-resolved.service",
            "pid": int(pid),
            "start_ticks": int(start_ticks),
            "uid": int(uid),
            "control_group": control_group,
        }

    @staticmethod
    def _content(suffix: str, address: str, port: int) -> bytes:
        return (
            f"# sandbox-resolver v1 suffix={suffix}\n[Resolve]\n"
            f"DNS={address}:{port}\nDomains=~{suffix}\n"
        ).encode()

    def ensure_helper(self, *, interactive: bool) -> dict:
        status = self.process.run(
            ("sudo", "-n", self.helper, "installed-status"), timeout=5,
        )
        if status.returncode == 0 and (status.stdout or "").strip() == HELPER_VERSION:
            return {"ok": True, "mutated": False}
        if not interactive or not self.repository_helper:
            return {
                "ok": False, "mutated": False,
                "error": "The scoped resolver helper requires one-time interactive installation.",
            }
        installed = self.process.run(
            ("sudo", self.repository_helper, "install"), timeout=120,
        )
        if installed.returncode != 0:
            return {"ok": False, "mutated": False,
                    "error": (installed.stderr or "resolver helper installation failed")[:1000]}
        verified = self.process.run(
            ("sudo", "-n", self.helper, "installed-status"), timeout=5,
        )
        verified_ok = (verified.returncode == 0
                       and (verified.stdout or "").strip() == HELPER_VERSION)
        return {
            "ok": verified_ok,
            "mutated": True,
            "error": "" if verified_ok else "installed resolver helper could not be verified",
        }

    def ensure_authorized(self, plan: dict, *, interactive: bool) -> dict:
        suffix, address, port = plan["suffix"], plan["address"], int(plan["port"])
        digest = hashlib.sha256(self._content(suffix, address, port)).hexdigest()
        identity = self._identity_args(plan)
        if identity is None:
            return {"ok": False, "mutated": False,
                    "error": "Resolved authorization requires final service identity."}
        args = ("resolved", plan["owner_digest"], suffix, address, str(port), digest,
                *identity)
        status = self.process.run(
            ("sudo", "-n", self.helper, "authorization-status", *args), timeout=5,
        )
        if status.returncode == 0 and (status.stdout or "").strip() == "authorized":
            return {"ok": True, "mutated": False, "digest": digest}
        if not interactive:
            return {"ok": False, "mutated": False,
                    "error": "Exact resolver authorization requires interactive approval."}
        authorized = self.process.run(
            ("sudo", self.helper, "authorize", *args), timeout=120,
        )
        return {"ok": authorized.returncode == 0,
                "mutated": authorized.returncode == 0,
                "digest": digest,
                "error": (authorized.stderr or "resolver authorization failed")[:1000]}

    def apply(self, suffix, address=None, port=None) -> dict:
        if isinstance(suffix, dict):
            plan = suffix
            suffix, address, port = plan["suffix"], plan["address"], plan["port"]
        else:
            return {"ok": False, "mutated": False,
                    "error": "resolved apply requires an owner-bound plan"}
        identity = self._identity_args(plan)
        if identity is None:
            return {"ok": False, "mutated": False,
                    "error": "resolved apply requires final service identity"}
        before = self.readlink("/etc/resolv.conf")
        content = self._content(suffix, address, int(port))
        digest = hashlib.sha256(content).hexdigest()
        result = self.process.run((
            "sudo", "-n", self.helper, "resolved-apply",
            plan["owner_digest"], suffix, address, str(port), digest, *identity,
        ), timeout=30)
        if result.returncode != 0:
            return {"ok": False, "mutated": False,
                    "error": (result.stderr or "resolver helper failed")[:1000]}
        after = self.readlink("/etc/resolv.conf")
        if after != before:
            rollback = self.rollback({
                "suffix": suffix, "address": address, "port": int(port),
                "owner_digest": plan["owner_digest"],
                "service_identity": plan["service_identity"],
                "applied": {"fragment_digest": digest},
            })
            return {
                "ok": False, "mutated": not rollback.get("ok", False),
                "rollback_failed": not rollback.get("ok", False),
                "applied": {
                    "suffix": suffix, "address": address, "port": int(port),
                    "fragment_digest": digest, "resolv_conf_link": before,
                },
                "error": (
                    "resolver-managed resolv.conf relationship changed; rollback failed"
                    if not rollback.get("ok") else
                    "resolver-managed resolv.conf relationship changed; scoped fragment rolled back"
                ),
            }
        return {"ok": True, "mutated": (result.stdout or "").strip() != "unchanged",
                "applied": {
            "suffix": suffix, "address": address, "port": int(port),
            "fragment_digest": digest,
            "resolv_conf_link": before,
        }}

    def rollback(self, plan: dict) -> dict:
        digest = (plan.get("applied") or plan).get("fragment_digest")
        identity = self._identity_args(plan)
        if not digest or identity is None:
            return {"ok": False, "mutated": False}
        result = self.process.run((
            "sudo", "-n", self.helper, "resolved-remove", plan["owner_digest"],
            plan["suffix"], plan["address"], str(plan["port"]), digest, *identity,
        ), timeout=30)
        return {"ok": result.returncode == 0, "mutated": result.returncode == 0}

    def revoke_authorization(self, plan: dict) -> dict:
        digest = hashlib.sha256(self._content(
            plan["suffix"], plan["address"], int(plan["port"]),
        )).hexdigest()
        identity = self._identity_args(plan)
        if identity is None:
            return {"ok": False, "mutated": False}
        result = self.process.run((
            "sudo", "-n", self.helper, "revoke-authorization", "resolved",
            plan["owner_digest"],
            plan["suffix"], plan["address"], str(plan["port"]), digest, *identity,
        ), timeout=30)
        return {"ok": result.returncode == 0, "mutated": result.returncode == 0}

    def cleanup(self, binding) -> dict:
        return self.release_owner(binding, dict(binding.desired)["owner_digest"])

    @staticmethod
    def _identity_args(plan: dict) -> tuple[str, str, str, str] | None:
        identity = plan.get("service_identity")
        if not isinstance(identity, dict) or set(identity) != {
            "schema", "owner_id", "unit", "pid", "start_ticks", "uid",
            "control_group",
        }:
            return None
        if (identity.get("schema") != "sandbox-resolved-service-v1"
                or identity.get("owner_id") != "systemd-resolved:host"
                or identity.get("unit") != "systemd-resolved.service"
                or identity.get("control_group")
                != "/system.slice/systemd-resolved.service"):
            return None
        values = (identity.get("pid"), identity.get("start_ticks"), identity.get("uid"))
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0
               for value in values) or values[0] == 0 or values[1] == 0:
            return None
        return (str(values[0]), str(values[1]), str(values[2]),
                identity["control_group"])

    def release_owner(self, binding, owner_digest: str) -> dict:
        desired = dict(binding.desired)
        digest = (dict(binding.last_applied) if binding.last_applied is not None else {}).get(
            "fragment_digest"
        )
        identity = self._identity_args(desired)
        if not digest or identity is None:
            return {"ok": False, "mutated": False,
                    "error": "resolver binding has no owned fragment receipt"}
        result = self.process.run((
            "sudo", "-n", self.helper, "resolved-remove", owner_digest,
            desired["suffix"], desired["address"], str(desired["port"]), digest,
            *identity,
        ), timeout=30)
        return {"ok": result.returncode == 0, "mutated": result.returncode == 0,
                "error": (result.stderr or "")[:1000]}

    def observe(self, binding) -> dict | None:
        desired = dict(binding.desired)
        destination = Path(
            f"/etc/systemd/resolved.conf.d/80-sandbox-{desired['suffix']}.conf"
        )
        if not destination.exists() or destination.is_symlink():
            return None
        try:
            link = self.readlink("/etc/resolv.conf")
            digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        except OSError:
            return None
        return {
            "suffix": desired["suffix"], "address": desired["address"],
            "port": int(desired["port"]), "fragment_digest": digest,
            "resolv_conf_link": link,
        }
