"""Unprivileged transport for fixed managed-native helper contracts."""

from __future__ import annotations

import json
import os
from pathlib import Path

from sandbox.isolation.models import canonical_digest


class ManagedMachineExecutor:
    """Stage project argv out of process listings and invoke the fixed helper."""

    def __init__(self, *, process, helper, staging_root="/var/lib/sandbox/native/staging"):
        self.process = process
        self.helper = helper
        self.staging_root = Path(staging_root)

    def __call__(self, machine_id, argv, *, context, timeout, expected_policy_digest):
        request = {
            "machine_id": machine_id, "policy_digest": expected_policy_digest,
            "argv": list(argv), "environment": dict(context.get("environment", {})),
            "credential_refs": list(context.get("credential_refs", ())),
            "timeout": timeout,
        }
        request_digest = canonical_digest(request)
        self.staging_root.mkdir(parents=True, exist_ok=True)
        path = self.staging_root / f"execute-{os.getuid()}-{request_digest}.json"
        payload = (json.dumps(request, sort_keys=True, separators=(",", ":")) + "\n").encode()
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload); output.flush(); os.fsync(output.fileno())
            return self.process.run(("sudo", "-n", self.helper, "execute", machine_id,
                                     expected_policy_digest, request_digest),
                                    timeout=timeout + 15)
        finally:
            path.unlink(missing_ok=True)


class ManagedIsolationObserver:
    """Read one bounded effective-state document from the installed helper."""

    def __init__(self, *, process, helper):
        self.process = process
        self.helper = helper

    def __call__(self, machine_id):
        result = self.process.run(("sudo", "-n", self.helper, "isolation-observe",
                                   machine_id), timeout=20)
        if result.returncode != 0 or len((result.stdout or "").encode()) > 1024 * 1024:
            detail = ((result.stderr or "").strip() or "no output")
            raise RuntimeError(
                "managed isolation observation failed: "
                + (detail if len(detail) <= 400 else "…" + detail[-399:]))
        try: value = json.loads(result.stdout or "")
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("managed isolation observation is invalid") from exc
        if not isinstance(value, dict) or value.get("machine_id") != machine_id:
            raise RuntimeError("managed isolation observation is invalid")
        return value


class ManagedCleanupObserver:
    """Request one exact, read-only ownership proof before each removal."""

    RESOURCES = frozenset({
        "services", "database", "machine", "network", "mount", "image", "policy",
    })

    def __init__(self, *, process, helper):
        self.process = process
        self.helper = helper

    def __call__(self, resource, plan):
        if resource not in self.RESOURCES or not isinstance(plan, dict):
            raise ValueError("managed cleanup observation is invalid")
        machine_id = plan.get("machine_id")
        policy_digest = plan.get("policy_digest")
        resource_digest = plan.get("digest") if resource == "services" else policy_digest
        result = self.process.run(
            ("sudo", "-n", self.helper, "cleanup-observe", resource,
             machine_id, policy_digest, resource_digest), timeout=30,
        )
        if result.returncode != 0 or len((result.stdout or "").encode()) > 65536:
            raise RuntimeError("managed cleanup observation failed")
        try:
            value = json.loads(result.stdout or "")
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("managed cleanup observation is invalid") from exc
        expected = {"machine_id": machine_id, "policy_digest": policy_digest,
                    "resource": resource, "resource_digest": resource_digest}
        if not isinstance(value, dict):
            raise RuntimeError("managed cleanup observation is invalid")
        # `state` reports whether the host still has the resource. Anything other
        # than the two known verdicts is a malformed observation, not a hint to
        # be interpreted: cleanup must never guess what an unknown state means.
        state = value.get("state", "present")
        if state not in {"present", "absent"}:
            raise RuntimeError("managed cleanup observation is invalid")
        if {key: item for key, item in value.items() if key != "state"} != expected:
            raise RuntimeError("managed cleanup observation is invalid")
        return {**expected, "state": state}
