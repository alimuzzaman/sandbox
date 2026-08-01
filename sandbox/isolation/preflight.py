"""Read-only effective prerequisite gate for managed-native execution."""

from __future__ import annotations

from sandbox.isolation.manifest import MANAGED_ISOLATION_MATRICES


class IsolationPreflight:
    def __init__(self, *, facts, command_probe, effective_probe):
        self.facts = facts
        self.command_probe = command_probe
        self.effective_probe = effective_probe

    def inspect(self):
        facts = dict(self.facts())
        matrix = MANAGED_ISOLATION_MATRICES[0]
        checks = []
        checks.append({"gate": "platform", "ok": facts.get("os_id") == "ubuntu"
                       and facts.get("version_id") == "24.04",
                       "observed": {"id": facts.get("os_id"),
                                    "version": facts.get("version_id")}})
        checks.append({"gate": "systemd_version",
                       "ok": isinstance(facts.get("systemd_version"), int)
                       and facts["systemd_version"] >= matrix["systemd_min"],
                       "observed": facts.get("systemd_version")})
        for command in matrix["required_commands"]:
            checks.append({"gate": f"command:{command}",
                           "ok": bool(self.command_probe(command)), "observed": None})
        for gate in matrix["required_effective_gates"]:
            observation = self.effective_probe(gate)
            checks.append({"gate": gate,
                           "ok": observation is True or
                                 (isinstance(observation, dict) and observation.get("ok") is True),
                           "observed": observation})
        failures = [item["gate"] for item in checks if not item["ok"]]
        return {"ok": not failures, "operation": "native_preflight",
                "state": "ready" if not failures else "blocked",
                "matrix_id": matrix["matrix_id"], "checks": checks,
                "reason": {"code": "ready" if not failures else
                           "isolation_prerequisite_missing",
                           "missing": failures},
                "mutated": False, "adoptable": bool(not failures and matrix["adoptable"])}
