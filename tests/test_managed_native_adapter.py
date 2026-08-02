from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace


class Preflight:
    def __init__(self, ok): self.ok = ok
    def inspect(self):
        return {"ok": self.ok, "state": "ready" if self.ok else "blocked",
                "mutated": False, "reason": {"code": "ready" if self.ok else
                                             "isolation_prerequisite_missing",
                                             "missing": [] if self.ok else ["nftables"]}}


class TestManagedNativeAdapter(unittest.TestCase):
    def adapter(self, *, preflight=True, evidence=None):
        from sandbox.runtimes.managed.adapter import ManagedNativeAdapter
        from sandbox.runtimes.managed.repository import NativeRepository
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        return ManagedNativeAdapter(
            preflight=Preflight(preflight),
            repository=NativeRepository(Path(temporary.name) / "state.json"),
            evidence_id=evidence,
        )

    def test_missing_effective_gate_blocks_without_mutation(self):
        from sandbox.runtimes.base import OperationRequest
        result = self.adapter(preflight=False).invoke(
            OperationRequest("/tmp/project", "ensure"))
        self.assertFalse(result.ok); self.assertFalse(result.data["mutated"])
        self.assertEqual(result.data["reason"]["code"], "isolation_prerequisite_missing")

    def test_code_complete_but_unproven_runtime_remains_blocked(self):
        from sandbox.runtimes.base import OperationRequest
        result = self.adapter(preflight=True).invoke(
            OperationRequest("/tmp/project", "ensure"))
        self.assertFalse(result.ok)
        self.assertEqual(result.data["reason"]["code"], "managed_runtime_unproven")

    def test_only_opaque_candidate_authority_opens_the_unproven_proof_path(self):
        from sandbox.runtimes.base import OperationRequest
        from sandbox.runtimes.managed.adapter import (
            ManagedNativeAdapter, _proof_candidate_authority,
        )
        from sandbox.runtimes.managed.repository import NativeRepository

        for forged in (None, "ubuntu-24.04-systemd-255",
                       {"candidate": "ubuntu-24.04-systemd-255"}, object()):
            with self.subTest(forged=type(forged).__name__), \
                    tempfile.TemporaryDirectory() as directory:
                adapter = ManagedNativeAdapter(
                    preflight=Preflight(True),
                    repository=NativeRepository(Path(directory) / "state.json"),
                    proof_candidate_authority=forged,
                )
                result = adapter.invoke(OperationRequest("/tmp/project", "ensure"))
                self.assertEqual(result.data["reason"]["code"],
                                 "managed_runtime_unproven")
                self.assertNotIn("proof_candidate", result.data["runtime"])

        with tempfile.TemporaryDirectory() as directory:
            adapter = ManagedNativeAdapter(
                preflight=Preflight(True),
                repository=NativeRepository(Path(directory) / "state.json"),
                proof_candidate_authority=_proof_candidate_authority(
                    "ubuntu-24.04-systemd-255",
                ),
            )
            result = adapter.invoke(OperationRequest("/tmp/project", "ensure"))
        self.assertEqual(result.data["reason"]["code"],
                         "managed_runtime_not_installed")
        self.assertTrue(result.data["runtime"]["proof_candidate"])
        self.assertFalse(result.data["runtime"]["adoptable"])
        self.assertTrue(result.data["proof_candidate"])
        self.assertFalse(result.data["adoptable"])

    def test_candidate_authority_is_not_serializable(self):
        import copy
        import pickle
        from sandbox.runtimes.managed.adapter import _proof_candidate_authority

        authority = _proof_candidate_authority("ubuntu-24.04-systemd-255")
        with self.assertRaises(TypeError):
            pickle.dumps(authority)
        with self.assertRaises(TypeError):
            copy.copy(authority)
        with self.assertRaises(TypeError):
            copy.deepcopy(authority)

    def test_exact_candidate_authority_allows_bounded_ensure_without_adoption(self):
        from sandbox.runtimes.base import OperationRequest
        from sandbox.runtimes.managed.adapter import (
            ManagedNativeAdapter, _proof_candidate_authority,
        )
        from sandbox.runtimes.managed.repository import NativeRepository
        from tests.test_isolation_verification import policy

        desired = policy()
        owner = {"project_root": "/tmp/project", "label": "default"}
        with tempfile.TemporaryDirectory() as directory:
            repository = NativeRepository(Path(directory) / "state.json")

            class Provisioner:
                def ensure(self, _plan):
                    repository.put_owned("backends", desired.machine_id, {
                        "owner": owner, "policy_digest": desired.digest,
                        "backend": {"address": "10.203.0.2", "port": 8080},
                    })
                    return {"ok": True, "state": "ready", "mutated": True}

            dependencies = SimpleNamespace(
                plan_builder=lambda _request: {
                    "machine_id": desired.machine_id,
                    "policy_digest": desired.digest,
                    "policy": desired,
                },
                provisioner=Provisioner(), verifier=None, launcher=None,
                cleanup=None, grants=None,
            )
            adapter = ManagedNativeAdapter(
                preflight=Preflight(True), repository=repository,
                dependencies=dependencies,
                proof_candidate_authority=_proof_candidate_authority(
                    "ubuntu-24.04-systemd-255",
                ),
            )
            result = adapter.invoke(OperationRequest("/tmp/project", "ensure"))

        self.assertTrue(result.ok)
        self.assertTrue(result.data["mutated"])
        self.assertTrue(result.data["proof_candidate"])
        self.assertFalse(result.data["adoptable"])

    def test_candidate_failed_preflight_has_zero_mutation_and_no_persisted_authority(self):
        import json
        from sandbox.runtimes.base import OperationRequest
        from sandbox.runtimes.managed.adapter import (
            ManagedNativeAdapter, _proof_candidate_authority,
        )
        from sandbox.runtimes.managed.repository import NativeRepository

        with tempfile.TemporaryDirectory() as directory:
            repository = NativeRepository(Path(directory) / "state.json")
            before = repository.snapshot()
            adapter = ManagedNativeAdapter(
                preflight=Preflight(False), repository=repository,
                proof_candidate_authority=_proof_candidate_authority(
                    "ubuntu-24.04-systemd-255",
                ),
            )
            result = adapter.invoke(OperationRequest("/tmp/project", "ensure"))
            after = repository.snapshot()
        self.assertFalse(result.ok)
        self.assertFalse(result.data["mutated"])
        self.assertEqual(before, after)
        self.assertTrue(result.data["runtime"]["proof_candidate"])
        self.assertFalse(result.data["runtime"]["adoptable"])
        self.assertTrue(result.data["proof_candidate"])
        self.assertFalse(result.data["adoptable"])
        persisted = json.dumps(after, sort_keys=True)
        self.assertNotIn("SANDBOX_NATIVE_PROOF_CANDIDATE", persisted)
        self.assertNotIn("ubuntu-24.04-systemd-255", persisted)

    def test_proven_ensure_persists_digest_bound_policy_and_status_reverifies_it(self):
        from sandbox.runtimes.base import OperationRequest
        from sandbox.runtimes.managed.adapter import ManagedNativeAdapter
        from sandbox.runtimes.managed.repository import NativeRepository
        from tests.test_isolation_verification import policy

        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        repository = NativeRepository(Path(temporary.name) / "state.json")
        desired = policy(); calls = []

        class Provisioner:
            def ensure(self, plan):
                calls.append(("ensure", plan["policy"].digest))
                repository.put_owned("backends", desired.machine_id, {
                    "owner": {"project_root": "/tmp/project", "label": "default"},
                    "backend": {"address": "10.203.0.2", "port": 8080},
                    "policy_digest": desired.digest,
                })
                return {"ok": True, "state": "ready", "mutated": True,
                        "backend": {"address": "10.203.0.2", "port": 8080}}

        class Verifier:
            def verify(self, value):
                calls.append(("verify", value.digest))
                return {"ok": value.digest == desired.digest,
                        "reason": {"code": "ready"}}

        dependencies = SimpleNamespace(
            plan_builder=lambda _request: {"machine_id": desired.machine_id,
                "policy_digest": desired.digest, "policy": desired},
            provisioner=Provisioner(), verifier=Verifier(), launcher=None, cleanup=None,
        )
        adapter = ManagedNativeAdapter(preflight=Preflight(True), repository=repository,
                                       dependencies=dependencies, evidence_id="ubuntu-proof")
        ensured = adapter.invoke(OperationRequest("/tmp/project", "ensure"))
        status = adapter.invoke(OperationRequest("/tmp/project", "status"))
        self.assertTrue(ensured.ok); self.assertTrue(status.ok)
        self.assertIn(desired.machine_id, repository.snapshot()["policies"])
        self.assertEqual(calls, [("ensure", desired.digest), ("verify", desired.digest)])

    def test_exec_and_test_can_only_use_the_isolation_launcher_and_stored_policy(self):
        from sandbox.runtimes.base import OperationRequest
        from sandbox.runtimes.managed.adapter import ManagedNativeAdapter
        from sandbox.runtimes.managed.repository import NativeRepository
        from tests.test_isolation_verification import policy

        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        repository = NativeRepository(Path(temporary.name) / "state.json")
        desired = policy(); owner = {"project_root": "/tmp/project", "label": "default"}
        repository.put_owned("backends", desired.machine_id,
                             {"owner": owner, "policy_digest": desired.digest})
        record = {"owner": owner, "policy": desired.to_dict()}
        from sandbox.isolation.models import canonical_digest
        record["last_applied"] = canonical_digest(record)
        repository.put_owned("policies", desired.machine_id, record)
        calls = []

        class Launcher:
            def launch(self, target, **kwargs):
                calls.append((target.digest, kwargs))
                return {"ok": True, "state": "ready", "mutated": False}

        dependencies = SimpleNamespace(plan_builder=None, provisioner=None, verifier=None,
                                       launcher=Launcher(), cleanup=None)
        adapter = ManagedNativeAdapter(preflight=Preflight(True), repository=repository,
                                       dependencies=dependencies, evidence_id="ubuntu-proof")
        for operation, entry, command in (
                ("exec", "exec", ("php", "-v")),
                ("test", "phpunit", ("php", "-v")),
                ("wordpress_cli", "wordpress_cli", ("wp", "core", "version"))):
            result = adapter.invoke(OperationRequest(
                "/tmp/project", operation, arguments={"command": command}))
            self.assertTrue(result.ok)
            self.assertEqual(calls[-1][1]["entry_path"], entry)
            self.assertEqual(calls[-1][0], desired.digest)
        self.assertEqual(calls[-1][1]["command"], (
            "/usr/local/bin/wp", "core", "version", "--path=/var/www/html"))

        from sandbox.runtimes.base import ExecutionRequest
        execution = ExecutionRequest("/tmp/project", "default", "composer",
                                     ("composer", "install"), 60)
        result = adapter.invoke(OperationRequest(
            "/tmp/project", "exec", arguments={"execution": execution}))
        self.assertTrue(result.ok)
        self.assertEqual(calls[-1][1]["entry_path"], "composer")
        self.assertEqual(calls[-1][1]["timeout"], 60)

    def test_policy_digest_mismatch_stops_before_provisioner(self):
        from sandbox.runtimes.base import OperationRequest
        from sandbox.runtimes.managed.adapter import ManagedNativeAdapter
        from sandbox.runtimes.managed.repository import NativeRepository
        from tests.test_isolation_verification import policy
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        repository = NativeRepository(Path(temporary.name) / "state.json")
        desired = policy(); calls = []
        dependencies = SimpleNamespace(
            plan_builder=lambda _request: {"machine_id": desired.machine_id,
                "policy_digest": "0" * 64, "policy": desired},
            provisioner=SimpleNamespace(ensure=lambda _plan: calls.append("ensure")),
            verifier=None, launcher=None, cleanup=None,
        )
        result = ManagedNativeAdapter(
            preflight=Preflight(True), repository=repository,
            dependencies=dependencies, evidence_id="ubuntu-proof",
        ).invoke(OperationRequest("/tmp/project", "ensure"))
        self.assertFalse(result.ok); self.assertEqual(calls, [])
        self.assertEqual(result.data["reason"]["code"], "isolation_policy_drift")

    def test_apply_reconciles_scoped_grants_without_replacing_stable_policy(self):
        from sandbox.isolation.models import EgressGrant, EgressGrantSet, canonical_digest
        from sandbox.runtimes.base import OperationRequest
        from sandbox.runtimes.managed.adapter import ManagedNativeAdapter
        from sandbox.runtimes.managed.repository import NativeRepository
        from tests.test_isolation_verification import policy

        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        repository = NativeRepository(Path(temporary.name) / "state.json")
        stable = policy(); owner = {"project_root": "/tmp/project", "label": "default"}
        repository.put_owned("backends", stable.machine_id, {
            "owner": owner, "backend": {"address": "10.203.0.2", "port": 8080},
            "policy_digest": stable.digest,
        })
        record = {"owner": owner, "policy": stable.to_dict()}
        record["last_applied"] = canonical_digest(record)
        repository.put_owned("policies", stable.machine_id, record)
        empty = EgressGrantSet(stable.machine_id, stable.digest)
        self.assertEqual(repository.put_grants_if_expected(
            stable.machine_id, owner=owner, policy_digest=stable.digest,
            expected_digest="0" * 64, grant_set=empty,
        ), "stored")
        grant = EgressGrant("api", stable.machine_id, "public_cidr_tcp",
                            ("8.8.8.8/32",), (443,), "2999-01-01T00:00:00Z")
        desired = EgressGrantSet(stable.machine_id, stable.digest, (grant,))
        calls = []

        class Reconciler:
            def reconcile(self, target, grant_set, *, expected_digest):
                calls.append((target.digest, expected_digest, grant_set.digest))
                return {"ok": True, "mutated": True}

        class Verifier:
            def verify(self, target, *, grants=None):
                return {"ok": target.digest == stable.digest,
                        "reason": {"code": "ready"}}

        dependencies = SimpleNamespace(
            plan_builder=lambda request: {"machine_id": stable.machine_id,
                "policy_digest": stable.digest, "policy": stable,
                "grant_set": desired if request.arguments.get("egress") else empty},
            provisioner=None, verifier=Verifier(), launcher=None, cleanup=None,
            grants=Reconciler(),
        )
        adapter = ManagedNativeAdapter(preflight=Preflight(True), repository=repository,
                                       dependencies=dependencies, evidence_id="ubuntu-proof")
        result = adapter.invoke(OperationRequest(
            "/tmp/project", "apply", arguments={"egress": [desired.to_dict()["grants"][0]]},
        ))
        self.assertTrue(result.ok)
        self.assertEqual(calls, [(stable.digest, empty.digest, desired.digest)])
        state = repository.snapshot()
        self.assertEqual(state["policies"][stable.machine_id]["policy"]["digest"], stable.digest)
        self.assertEqual(state["grants"][stable.machine_id]["grant_digest"], desired.digest)
        revoked = adapter.invoke(OperationRequest(
            "/tmp/project", "apply", arguments={"egress": []},
        ))
        self.assertTrue(revoked.ok)
        self.assertEqual(calls[-1], (stable.digest, desired.digest, empty.digest))
        self.assertEqual(repository.snapshot()["grants"][stable.machine_id]["grant_digest"],
                         empty.digest)


if __name__ == "__main__": unittest.main()
