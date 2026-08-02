import unittest


class Step:
    def __init__(self, events, name, *, ok=True):
        self.events = events; self.name = name; self.ok = ok
    def install(self, value): self.events.append(self.name)
    def create(self, value): self.events.append("image-create")
    def mount(self, value): self.events.append("image-mount")
    def unmount(self, value): self.events.append("image-unmount"); return {"ok": True}
    def configure(self, value): self.events.append(self.name)
    def start_minimal(self, value): self.events.append("machine-minimal")
    def stop(self, value): self.events.append(self.name + "-stop"); return {"ok": True}
    def apply(self, value): self.events.append("network")
    def remove(self, value):
        self.events.append(self.name + "-remove")
        return {"ok": self.ok, "mutated": self.ok}
    def deactivate(self, value): self.events.append(self.name + "-deactivate"); return {"ok": True}
    def initialize(self, value):
        self.events.append(self.name)
        return {"ok": self.ok, "mutated": self.ok}
    def activate(self, value):
        self.events.append("services")
        return {"ok": self.ok, "mutated": self.ok}
    def put_owned(self, *args): self.events.append("persist")


class CredentialStep(Step):
    def install(self, **value):
        self.events.append("credentials")
        return ({"name": "db-credential"},) if self.ok else ()


class PolicyStep(Step):
    def remove(self, value):
        if (not isinstance(value, dict)
                or set(value) != {"machine_id", "policy_digest"}
                or not isinstance(value["machine_id"], str)
                or not isinstance(value["policy_digest"], str)
                or len(value["policy_digest"]) != 64):
            raise AssertionError("policy cleanup requires the store plan contract")
        self.events.append("policy-remove")
        return {"ok": self.ok, "mutated": self.ok}


class Verifier:
    def __init__(self, events, ok=True): self.events = events; self.ok = ok
    def verify(self, policy): self.events.append("verify"); return {"ok": self.ok}


class TestManagedProvisioner(unittest.TestCase):
    def provisioner(self, events, verify=True, *, credentials_ok=True,
                    database_ok=True, services_ok=True, wordpress_ok=True):
        from sandbox.runtimes.managed.adapter import ManagedProvisioner
        return ManagedProvisioner(
            policy=PolicyStep(events, "policy"), apparmor=Step(events, "apparmor"),
            image=Step(events, "image"),
            rootfs=Step(events, "rootfs"), machine=Step(events, "machine"),
            network=Step(events, "network"), verifier=Verifier(events, verify),
            credentials=CredentialStep(events, "credentials", ok=credentials_ok),
            database=Step(events, "database", ok=database_ok),
            services=Step(events, "services", ok=services_ok),
            wordpress=Step(events, "wordpress", ok=wordpress_ok),
            health=lambda plan: events.append("health") or {"ok": True},
            repository=Step(events, "repository"),
        )

    def plan(self):
        policy = type("Policy", (), {"digest": "a" * 64})()
        return {"machine_id": "sb-0123456789ab", "policy": policy, "image": {},
                "apparmor": {},
                "network": {},
                "database": {"credential_refs": ("native/sb-0123456789ab/db-credential",)},
                "wordpress": {},
                "services": {"backend": {"port": 8080}},
                "record": {"owner": "owner"}}

    def test_no_project_service_can_start_before_effective_verification(self):
        events = []; result = self.provisioner(events).ensure(self.plan())
        self.assertTrue(result["ok"])
        self.assertLess(events.index("verify"), events.index("database"))
        self.assertLess(events.index("verify"), events.index("credentials"))
        self.assertLess(events.index("credentials"), events.index("database"))
        self.assertLess(events.index("verify"), events.index("services"))
        self.assertLess(events.index("database"), events.index("wordpress"))
        self.assertLess(events.index("wordpress"), events.index("services"))
        self.assertLess(events.index("image-unmount"), events.index("machine-minimal"))
        self.assertLess(events.index("apparmor"), events.index("machine-minimal"))
        self.assertEqual(events[-1], "persist")

    def test_verification_failure_rolls_back_without_database_or_services(self):
        events = []; result = self.provisioner(events, verify=False).ensure(self.plan())
        self.assertFalse(result["ok"])
        self.assertNotIn("credentials", events); self.assertNotIn("database", events)
        self.assertNotIn("services", events)
        self.assertIn("network-remove", events); self.assertIn("machine-stop", events)
        self.assertLess(events.index("network-deactivate"), events.index("machine-stop"))
        self.assertLess(events.index("machine-stop"), events.index("network-remove"))
        self.assertIn("image-remove", events)
        self.assertIn("apparmor-remove", events)
        self.assertIn("image-unmount", events)
        self.assertEqual(events[-1], "policy-remove")

    def test_lost_network_apply_response_still_removes_network_and_policy(self):
        events = []

        class LostNetwork(Step):
            def apply(self, value):
                self.events.append("network")
                raise RuntimeError("lost apply response")

        provisioner = self.provisioner(events)
        provisioner.network = LostNetwork(events, "network")
        result = provisioner.ensure(self.plan())
        self.assertEqual(result["state"], "rollback_complete")
        self.assertIn("network-remove", events)
        self.assertEqual(events[-1], "policy-remove")

    def test_lost_database_bootstrap_response_still_removes_database_and_policy(self):
        events = []

        class LostDatabase(Step):
            def initialize(self, value):
                self.events.append("database")
                raise RuntimeError("lost database response")

        provisioner = self.provisioner(events)
        provisioner.database = LostDatabase(events, "database")
        result = provisioner.ensure(self.plan())
        self.assertEqual(result["state"], "rollback_complete")
        self.assertIn("database-remove", events)
        self.assertEqual(events[-1], "policy-remove")

    def test_database_or_service_failure_never_persists_a_ready_backend(self):
        for failed, options in (("credentials", {"credentials_ok": False}),
                                ("database", {"database_ok": False}),
                                ("wordpress", {"wordpress_ok": False}),
                                ("services", {"services_ok": False})):
            with self.subTest(failed=failed):
                events = []
                result = self.provisioner(events, **options).ensure(self.plan())
                self.assertFalse(result["ok"])
                self.assertNotIn("persist", events)
                if failed in {"credentials", "database"}:
                    self.assertNotIn("services", events)
                if failed == "credentials":
                    self.assertNotIn("database", events)
                self.assertIn("network-remove", events)
                self.assertIn("machine-stop", events)

    def test_active_grants_are_revoked_with_cas_before_network_and_policy_cleanup(self):
        from sandbox.isolation.models import EgressGrant, EgressGrantSet
        from sandbox.runtimes.managed.adapter import ManagedProvisioner
        from sandbox.runtimes.managed.repository import NativeRepository
        from tests.test_isolation_verification import policy
        from pathlib import Path
        import tempfile

        events = []
        desired_policy = policy()
        grant = EgressGrant(
            "api", desired_policy.machine_id, "public_cidr_tcp",
            ("8.8.8.8/32",), (443,), "2999-01-01T00:00:00Z",
        )
        active = EgressGrantSet(desired_policy.machine_id, desired_policy.digest, (grant,))

        class Grants:
            def reconcile(self, target, desired, *, expected_digest):
                events.append(("grants", expected_digest, desired.digest))
                return {"ok": True, "mutated": True}

        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        repository = NativeRepository(Path(temporary.name) / "state.json")
        provisioner = ManagedProvisioner(
            policy=PolicyStep(events, "policy"), apparmor=Step(events, "apparmor"),
            image=Step(events, "image"), rootfs=Step(events, "rootfs"),
            machine=Step(events, "machine"), network=Step(events, "network"),
            verifier=Verifier(events), credentials=CredentialStep(events, "credentials"),
            database=Step(events, "database"), services=Step(events, "services"),
            wordpress=Step(events, "wordpress"),
            health=lambda _plan: events.append("health") or {"ok": False},
            repository=repository, grants=Grants(),
        )
        plan = self.plan()
        plan.update({
            "machine_id": desired_policy.machine_id, "policy": desired_policy,
            "grant_set": active,
            "record": {"owner": {"project_root": "/tmp/project", "label": "default"}},
        })
        result = provisioner.ensure(plan)
        self.assertEqual(result["state"], "rollback_complete")
        self.assertEqual(events.count(("grants", "0" * 64, active.digest)), 1)
        empty_digest = EgressGrantSet(
            desired_policy.machine_id, desired_policy.digest,
        ).digest
        self.assertIn(("grants", active.digest, empty_digest), events)
        revoke_index = events.index(("grants", active.digest, empty_digest))
        self.assertLess(revoke_index, events.index("network-remove"))
        self.assertEqual(events[-1], "policy-remove")
        self.assertEqual(repository.snapshot()["recovery"], {})

    def test_incomplete_grant_network_and_policy_rollback_retains_attributed_recovery(self):
        from sandbox.isolation.models import EgressGrant, EgressGrantSet
        from sandbox.runtimes.managed.adapter import ManagedProvisioner
        from sandbox.runtimes.managed.repository import NativeRepository
        from tests.test_isolation_verification import policy
        from pathlib import Path
        import tempfile

        events = []
        desired_policy = policy()
        owner = {"project_root": "/tmp/project", "label": "default"}
        grant = EgressGrant(
            "api", desired_policy.machine_id, "public_cidr_tcp",
            ("8.8.8.8/32",), (443,), "2999-01-01T00:00:00Z",
        )
        active = EgressGrantSet(desired_policy.machine_id, desired_policy.digest, (grant,))

        class Grants:
            def __init__(self): self.calls = 0
            def reconcile(self, target, desired, *, expected_digest):
                self.calls += 1
                events.append(("grants", expected_digest, desired.digest))
                return {"ok": self.calls == 1, "mutated": self.calls == 1}

        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        repository = NativeRepository(Path(temporary.name) / "state.json")
        provisioner = ManagedProvisioner(
            policy=PolicyStep(events, "policy", ok=False),
            apparmor=Step(events, "apparmor"), image=Step(events, "image"),
            rootfs=Step(events, "rootfs"), machine=Step(events, "machine"),
            network=Step(events, "network", ok=False), verifier=Verifier(events),
            credentials=CredentialStep(events, "credentials"),
            database=Step(events, "database"), services=Step(events, "services"),
            wordpress=Step(events, "wordpress"),
            health=lambda _plan: events.append("health") or {"ok": False},
            repository=repository, grants=Grants(),
        )
        plan = self.plan()
        plan.update({
            "machine_id": desired_policy.machine_id, "policy": desired_policy,
            "grant_set": active, "record": {"owner": owner},
        })
        result = provisioner.ensure(plan)
        self.assertEqual(result["state"], "rollback_incomplete")
        self.assertLess(
            events.index(("grants", active.digest, EgressGrantSet(
                desired_policy.machine_id, desired_policy.digest,
            ).digest)),
            events.index("network-remove"),
        )
        self.assertEqual(events[-1], "policy-remove")
        state = repository.snapshot()
        self.assertEqual(
            state["grants"][desired_policy.machine_id]["grant_digest"], active.digest,
        )
        expected = {
            f"provision:{desired_policy.machine_id}:grants",
            f"provision:{desired_policy.machine_id}:network",
            f"provision:{desired_policy.machine_id}:policy",
        }
        self.assertTrue(expected.issubset(state["recovery"]))
        for key in expected:
            self.assertEqual(state["recovery"][key]["owner"], owner)
            self.assertEqual(state["recovery"][key]["retry_state"], "pending")

    def test_failed_active_grant_apply_is_reconciled_to_empty_before_policy_removal(self):
        from sandbox.isolation.models import EgressGrant, EgressGrantSet
        from sandbox.runtimes.managed.adapter import ManagedProvisioner
        from sandbox.runtimes.managed.repository import NativeRepository
        from tests.test_isolation_verification import policy
        from pathlib import Path
        import tempfile

        events = []
        desired_policy = policy()
        active = EgressGrantSet(desired_policy.machine_id, desired_policy.digest, (
            EgressGrant(
                "api", desired_policy.machine_id, "public_cidr_tcp",
                ("8.8.8.8/32",), (443,), "2999-01-01T00:00:00Z",
            ),
        ))

        class Grants:
            def __init__(self): self.calls = 0
            def reconcile(self, target, desired, *, expected_digest):
                self.calls += 1
                events.append(("grants", expected_digest, desired.digest))
                return {"ok": self.calls == 2, "mutated": True}

        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        provisioner = ManagedProvisioner(
            policy=PolicyStep(events, "policy"), apparmor=Step(events, "apparmor"),
            image=Step(events, "image"), rootfs=Step(events, "rootfs"),
            machine=Step(events, "machine"), network=Step(events, "network"),
            verifier=Verifier(events), credentials=CredentialStep(events, "credentials"),
            database=Step(events, "database"), services=Step(events, "services"),
            wordpress=Step(events, "wordpress"), health=lambda _plan: {"ok": True},
            repository=NativeRepository(Path(temporary.name) / "state.json"),
            grants=Grants(),
        )
        plan = self.plan()
        plan.update({
            "machine_id": desired_policy.machine_id, "policy": desired_policy,
            "grant_set": active,
            "record": {"owner": {"project_root": "/tmp/project", "label": "default"}},
        })
        result = provisioner.ensure(plan)
        empty = EgressGrantSet(desired_policy.machine_id, desired_policy.digest)
        self.assertEqual(result["state"], "rollback_complete")
        self.assertEqual(events.count(("grants", "0" * 64, active.digest)), 1)
        self.assertEqual(events.count(("grants", active.digest, empty.digest)), 1)
        self.assertLess(
            events.index(("grants", active.digest, empty.digest)),
            events.index("network-remove"),
        )
        self.assertEqual(events[-1], "policy-remove")

    def test_adapter_destroy_retries_retained_grants_from_incomplete_provision(self):
        from sandbox.isolation.models import EgressGrant, EgressGrantSet
        from sandbox.runtimes.base import OperationRequest
        from sandbox.runtimes.managed.adapter import (
            ManagedNativeAdapter, ManagedProvisioner,
        )
        from sandbox.runtimes.managed.repository import NativeRepository
        from tests.test_isolation_verification import policy
        from pathlib import Path
        from types import SimpleNamespace
        import tempfile

        events = []
        desired_policy = policy()
        owner = {"project_root": "/tmp/project", "label": "default"}
        active = EgressGrantSet(desired_policy.machine_id, desired_policy.digest, (
            EgressGrant(
                "api", desired_policy.machine_id, "public_cidr_tcp",
                ("8.8.8.8/32",), (443,), "2999-01-01T00:00:00Z",
            ),
        ))

        class Grants:
            def __init__(self): self.calls = 0
            def reconcile(self, target, desired, *, expected_digest):
                self.calls += 1
                events.append(("grants", expected_digest, desired.digest))
                return {"ok": self.calls != 2, "mutated": True}

        class Cleanup:
            def __init__(self, repository): self.repository = repository
            def cleanup(self, request, plan):
                self_case.assertIs(plan["policy"].__class__, desired_policy.__class__)
                self_case.assertEqual(plan["cleanup"], {"proof": "retryable"})
                record = self.repository.snapshot()["policies"][desired_policy.machine_id]
                observed = {key: value for key, value in record.items()
                            if key != "last_applied"}
                self_case.assertEqual(self.repository.remove_if_unchanged(
                    "policies", desired_policy.machine_id, observed,
                ), "removed")
                progress_key = f"cleanup-progress:{desired_policy.machine_id}"
                progress = self.repository.snapshot()["recovery"].get(progress_key)
                if progress is not None:
                    self.repository.remove_recovery_if_unchanged(progress_key, progress)
                return {"ok": True, "state": "ready", "mutated": True,
                        "cleanup": {"complete": True, "residual": ()},
                        "reason": {"code": "cleanup_complete"}}

        self_case = self
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        repository = NativeRepository(Path(temporary.name) / "state.json")
        grants = Grants()
        provisioner = ManagedProvisioner(
            policy=PolicyStep(events, "policy"), apparmor=Step(events, "apparmor"),
            image=Step(events, "image"), rootfs=Step(events, "rootfs"),
            machine=Step(events, "machine"), network=Step(events, "network"),
            verifier=Verifier(events), credentials=CredentialStep(events, "credentials"),
            database=Step(events, "database"), services=Step(events, "services"),
            wordpress=Step(events, "wordpress"),
            health=lambda _plan: events.append("health") or {"ok": False},
            repository=repository, grants=grants,
        )
        plan = self.plan()
        plan.update({
            "machine_id": desired_policy.machine_id, "policy": desired_policy,
            "grant_set": active, "record": {"owner": owner},
            "cleanup": {"proof": "retryable"},
        })
        failed = provisioner.ensure(plan)
        self.assertEqual(failed["state"], "rollback_incomplete")
        state = repository.snapshot()
        self.assertIn(desired_policy.machine_id, state["policies"])
        self.assertIn(desired_policy.machine_id, state["grants"])

        dependencies = SimpleNamespace(
            cleanup=Cleanup(repository), grants=grants,
            plan_builder=None, provisioner=None, verifier=None, launcher=None,
        )
        destroyed = ManagedNativeAdapter(
            preflight=object(), repository=repository, dependencies=dependencies,
            evidence_id="ubuntu-proof",
        ).invoke(OperationRequest("/tmp/project", "destroy"))
        self.assertTrue(destroyed.ok)
        state = repository.snapshot()
        self.assertNotIn(desired_policy.machine_id, state["grants"])
        self.assertNotIn(desired_policy.machine_id, state["policies"])
        self.assertFalse(any(
            key.startswith(f"provision:{desired_policy.machine_id}:")
            for key in state["recovery"]
        ))
        empty = EgressGrantSet(desired_policy.machine_id, desired_policy.digest)
        self.assertEqual(events[-1], ("grants", active.digest, empty.digest))


if __name__ == "__main__": unittest.main()
