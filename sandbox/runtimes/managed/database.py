"""Deterministic per-instance database identities; credentials remain references."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping


class ManagedDatabase:
    _MACHINE = re.compile(r"^sb-[a-f0-9]{12,32}$")
    _DIGEST = re.compile(r"^[a-f0-9]{64}$")
    _NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

    def __init__(self, *, process=None, helper=None):
        self.process = process
        self.helper = helper

    def plan(self, *, owner, machine_id):
        suffix = hashlib.sha256(machine_id.encode()).hexdigest()[:16]
        return {"owner": owner, "machine_id": machine_id,
                "production": f"sb_{suffix}", "tests": f"sb_{suffix}_tests",
                "user": f"sbu_{suffix[:12]}",
                "credential_refs": (f"native/{machine_id}/db-credential",),
                "socket": "/run/mysqld/mysqld.sock", "network_exposed": False}

    @staticmethod
    def validate_observed(plan, observed):
        keys = ("production", "tests", "user", "socket", "network_exposed")
        return isinstance(observed, Mapping) and all(observed.get(key) == plan.get(key) for key in keys)

    @classmethod
    def _validate(cls, plan):
        if not isinstance(plan, Mapping):
            raise ValueError("managed database plan is invalid")
        if not (isinstance(plan.get("machine_id"), str) and cls._MACHINE.fullmatch(plan["machine_id"])
                and isinstance(plan.get("policy_digest"), str)
                and cls._DIGEST.fullmatch(plan["policy_digest"])):
            raise ValueError("managed database identity is invalid")
        if not isinstance(plan.get("owner"), str) or not plan["owner"]:
            raise ValueError("managed database owner is invalid")
        for field, prefix in (("production", "sb_"), ("tests", "sb_"), ("user", "sbu_")):
            value = plan.get(field)
            if not isinstance(value, str) or not value.startswith(prefix) or not cls._NAME.fullmatch(value):
                raise ValueError("managed database name is invalid")
        expected_ref = f"native/{plan['machine_id']}/db-credential"
        if tuple(plan.get("credential_refs", ())) != (expected_ref,):
            raise ValueError("managed database credential reference is invalid")
        if plan.get("socket") != "/run/mysqld/mysqld.sock" or plan.get("network_exposed") is not False:
            raise ValueError("managed database must remain socket-only")

    def _run(self, verb, plan):
        self._validate(plan)
        if self.process is None or not isinstance(self.helper, str) or not self.helper.startswith("/"):
            raise RuntimeError("managed database helper is not composed")
        result = self.process.run(
            ("sudo", "-n", self.helper, verb, plan["machine_id"], plan["policy_digest"]),
            timeout=120,
        )
        return result.returncode == 0

    def initialize(self, plan):
        ok = self._run("database-bootstrap", plan)
        return {"ok": ok, "state": "ready" if ok else "bootstrap_failed", "mutated": ok}

    def status(self, plan):
        ok = self._run("database-status", plan)
        return {"ok": ok, "state": "ready" if ok else "unhealthy", "mutated": False,
                "socket": plan["socket"], "network_exposed": False}

    def remove(self, plan):
        ok = self._run("database-remove", plan)
        return {"ok": ok, "state": "removed" if ok else "cleanup_failed", "mutated": ok}
