"""Fail-closed managed-native runtime adapter; no Compose/host fallback exists."""

from __future__ import annotations

from sandbox.runtimes.base import OperationResult


class ManagedProvisioner:
    """Provision infrastructure first; activate project services only after proof."""
    def __init__(self, *, policy, apparmor, image, rootfs, machine, network, verifier,
                 database, services, health, repository):
        self.policy = policy; self.apparmor = apparmor
        self.image = image; self.rootfs = rootfs
        self.machine = machine; self.network = network; self.verifier = verifier
        self.database = database; self.services = services; self.health = health
        self.repository = repository

    def ensure(self, plan):
        completed = []
        try:
            self.policy.install(plan["policy"]); completed.append("policy")
            self.apparmor.install(plan["apparmor"]); completed.append("apparmor")
            self.image.create(plan["image"]); completed.append("image")
            self.image.mount(plan["image"]); completed.append("mount")
            self.rootfs.configure(plan); completed.append("rootfs")
            unmounted = self.image.unmount(plan["image"])
            if isinstance(unmounted, dict) and not unmounted.get("ok"):
                raise RuntimeError("managed image could not be closed after provisioning")
            completed.remove("mount")
            # Start only init/network. Web, PHP, database and cron remain masked,
            # so project files cannot execute before effective isolation proof.
            self.machine.start_minimal(plan); completed.append("machine")
            self.network.apply(plan["network"]); completed.append("network")
            verified = self.verifier.verify(plan["policy"])
            if not verified.get("ok"): raise RuntimeError("effective isolation verification failed")
            self.database.initialize(plan["database"]); completed.append("database")
            self.services.activate(plan["services"]); completed.append("services")
            health = self.health(plan)
            if not health.get("ok"): raise RuntimeError("managed backend health failed")
            self.repository.put_owned("backends", plan["machine_id"], plan["record"])
            return {"ok": True, "state": "ready", "mutated": True,
                    "backend": plan["services"]["backend"], "health": health}
        except Exception as exc:
            rollback = []
            if "services" in completed: rollback.append(self.services.stop(plan["services"]))
            if "network" in completed: rollback.append(self.network.remove(plan["network"]))
            if "machine" in completed: rollback.append(self.machine.stop(plan))
            if "mount" in completed: rollback.append(self.image.unmount(plan["image"]))
            if "apparmor" in completed: rollback.append(self.apparmor.remove(plan["apparmor"]))
            return {"ok": False, "state": "rollback_complete"
                    if all(item.get("ok", False) for item in rollback) else "rollback_incomplete",
                    "mutated": bool(completed), "error": str(exc), "completed": completed,
                    "rollback": rollback}


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
