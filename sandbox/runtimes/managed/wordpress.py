"""WordPress bootstrap inside an already-verified managed guest."""

from __future__ import annotations

import re
from collections.abc import Mapping


class ManagedWordPressBootstrap:
    _MACHINE = re.compile(r"^sb-[a-f0-9]{12,32}$")
    _DIGEST = re.compile(r"^[a-f0-9]{64}$")

    def __init__(self, *, process, helper):
        self.process = process
        self.helper = helper

    @classmethod
    def _validate(cls, plan):
        if not isinstance(plan, Mapping):
            raise ValueError("managed WordPress plan is invalid")
        if (not isinstance(plan.get("machine_id"), str)
                or not cls._MACHINE.fullmatch(plan["machine_id"])
                or not isinstance(plan.get("policy_digest"), str)
                or not cls._DIGEST.fullmatch(plan["policy_digest"])):
            raise ValueError("managed WordPress identity is invalid")

    def initialize(self, plan):
        self._validate(plan)
        result = self.process.run(("sudo", "-n", self.helper, "wordpress-bootstrap",
                                   plan["machine_id"], plan["policy_digest"]), timeout=300)
        return {"ok": result.returncode == 0, "mutated": result.returncode == 0}

    def status(self, plan):
        self._validate(plan)
        result = self.process.run(("sudo", "-n", self.helper, "wordpress-status",
                                   plan["machine_id"], plan["policy_digest"]), timeout=30)
        return {"ok": result.returncode == 0, "mutated": False}
