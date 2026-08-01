"""Fail-closed managed-native runtime adapter; no Compose/host fallback exists."""

from __future__ import annotations

from sandbox.runtimes.base import OperationResult


class ManagedNativeAdapter:
    adapter_id = "ubuntu-nspawn"
    capabilities = frozenset({
        "preflight", "ensure", "status", "health", "open", "wordpress_cli",
        "exec", "test", "apply", "destroy",
    })

    def __init__(self, *, preflight, repository, launcher=None, evidence_id=None):
        self.preflight = preflight
        self.repository = repository
        self.launcher = launcher
        self.evidence_id = evidence_id

    def invoke(self, request):
        if request.operation == "preflight":
            result = self.preflight.inspect()
        elif request.operation in {"status", "health"}:
            owner = f"{request.project_root}::{request.label}"
            state = self.repository.snapshot()
            backend = next((value for value in state["backends"].values()
                            if value.get("owner") == owner), None)
            result = {"ok": backend is not None, "state": "ready" if backend else "absent",
                      "backend": backend, "mutated": False,
                      "reason": {"code": "ready" if backend else "native_backend_absent"}}
        else:
            gate = self.preflight.inspect()
            if not gate.get("ok"):
                result = {**gate, "reason": {
                    "code": "isolation_prerequisite_missing",
                    "missing": gate.get("reason", {}).get("missing", ()),
                }}
            elif not self.evidence_id:
                result = {"ok": False, "state": "blocked", "mutated": False,
                          "reason": {"code": "managed_runtime_unproven",
                                     "message": "Live hostile-matrix evidence is required."}}
            else:
                result = {"ok": False, "state": "blocked", "mutated": False,
                          "reason": {"code": "managed_runtime_not_installed"}}
        return OperationResult(
            bool(result.get("ok")), request.operation, request.project_root, "wordpress",
            {"runtime": {"mode": "managed_native", "adapter": self.adapter_id,
                         "isolation": "managed_container"}, **result},
        )
