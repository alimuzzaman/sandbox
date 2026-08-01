"""Digest-bound systemd-nspawn machine lifecycle through the fixed root helper."""

from __future__ import annotations


class ManagedMachine:
    def __init__(self, *, process, helper):
        self.process = process
        self.helper = helper

    def plan(self, policy):
        return {"machine_id": policy.machine_id, "policy_digest": policy.digest,
                "unit": f"sandbox-native-{policy.machine_id}.service"}

    def _run(self, verb, plan):
        return self.process.run(("sudo", "-n", self.helper, verb,
                                 plan["machine_id"], plan["policy_digest"]), timeout=120)

    def start_minimal(self, plan):
        result = self._run("machine-start-minimal", plan)
        if result.returncode != 0:
            raise RuntimeError("managed machine failed before isolation verification")
        return {"ok": True, "mutated": True}

    def status(self, plan):
        result = self._run("machine-status", plan)
        return {"ok": result.returncode == 0, "mutated": False,
                "stdout": result.stdout or ""}

    def stop(self, plan):
        result = self._run("machine-stop", plan)
        return {"ok": result.returncode == 0, "mutated": result.returncode == 0}
