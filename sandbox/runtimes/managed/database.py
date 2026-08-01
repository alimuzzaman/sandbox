"""Deterministic per-instance database identities; credentials remain references."""

from __future__ import annotations

import hashlib


class ManagedDatabase:
    def plan(self, *, owner, machine_id):
        suffix = hashlib.sha256(f"{owner}\0{machine_id}".encode()).hexdigest()[:16]
        return {"owner": owner, "machine_id": machine_id,
                "production": f"sb_{suffix}", "tests": f"sb_{suffix}_tests",
                "user": f"sbu_{suffix[:12]}",
                "credential_refs": (f"native/{machine_id}/db-credential",),
                "socket": "/run/mysqld/mysqld.sock", "network_exposed": False}

    @staticmethod
    def validate_observed(plan, observed):
        keys = ("production", "tests", "user", "socket", "network_exposed")
        return all(observed.get(key) == plan.get(key) for key in keys)
