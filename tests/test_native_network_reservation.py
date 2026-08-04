"""The subnet/veth reservation must be released, and never strand an instance.

`reserve_network` writes the reservation while the plan is built, before any
provisioning step runs, so a rollback's `completed` list never mentions it.
`release_network` existed but had no caller anywhere in the product, so every
rolled-back provisioning left the reservation behind. Because the rollback does
remove the policy record, destroy then had nothing to plan from and answered
`cleanup_plan_unavailable` forever, while the allocator treated the leaked
subnet as permanently in use. Observed live on Ubuntu 24.04, 2026-08-04.
"""

from pathlib import Path
import tempfile
import unittest


class Step:
    """Minimal component double: every verb records and succeeds."""

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
    def initialize(self, value): self.events.append(self.name); return {"ok": True, "mutated": True}
    def activate(self, value): self.events.append("services"); return {"ok": True, "mutated": True}


class Credentials(Step):
    def install(self, **value):
        self.events.append("credentials")
        return ({"name": "db-credential"},)


class Verifier:
    def __init__(self, events, ok): self.events = events; self.ok = ok
    def verify(self, policy): self.events.append("verify"); return {"ok": self.ok}


OWNER = {"project_root": "/tmp/project", "label": "default"}


class TestReservationReleasedOnRollback(unittest.TestCase):
    def setUp(self):
        from sandbox.runtimes.managed.repository import NativeRepository

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repository = NativeRepository(Path(self.temporary.name) / "state.json")
        self.machine_id = "sb-0123456789ab"
        self.events = []

    def provisioner(self, *, verify):
        from sandbox.runtimes.managed.adapter import ManagedProvisioner

        events = self.events
        return ManagedProvisioner(
            policy=Step(events, "policy"), apparmor=Step(events, "apparmor"),
            image=Step(events, "image"), rootfs=Step(events, "rootfs"),
            machine=Step(events, "machine"), network=Step(events, "network"),
            verifier=Verifier(events, verify), credentials=Credentials(events, "credentials"),
            database=Step(events, "database"), services=Step(events, "services"),
            wordpress=Step(events, "wordpress"),
            health=lambda plan: {"ok": True},
            repository=self.repository,
        )

    def plan(self):
        policy = type("Policy", (), {"digest": "a" * 64})()
        return {"machine_id": self.machine_id, "policy": policy, "image": {},
                "apparmor": {}, "network": {}, "wordpress": {},
                "database": {"credential_refs": ()},
                "services": {"backend": {"port": 8080}},
                "record": {"owner": OWNER}}

    def test_a_rolled_back_provisioning_returns_the_subnet_to_the_allocator(self):
        reserved = self.repository.reserve_network(self.machine_id, owner=OWNER)
        self.assertIn(self.machine_id, self.repository.snapshot()["networks"])

        result = self.provisioner(verify=False).ensure(self.plan())

        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "rollback_complete")
        self.assertEqual(self.repository.snapshot()["networks"], {})
        # The released subnet is free again rather than counted as in use.
        again = self.repository.reserve_network(self.machine_id, owner=OWNER)
        self.assertEqual(again["subnet"], reserved["subnet"])

    def test_a_reservation_another_actor_changed_is_retained_not_discarded(self):
        self.repository.reserve_network(self.machine_id, owner=OWNER)
        with self.repository.transaction() as state:
            state["networks"][self.machine_id]["veth"] = "ve-changed"

        result = self.provisioner(verify=False).ensure(self.plan())

        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "rollback_incomplete")
        self.assertIn(self.machine_id, self.repository.snapshot()["networks"])

    def test_the_policy_record_is_only_removed_after_the_reservation(self):
        self.repository.reserve_network(self.machine_id, owner=OWNER)
        self.provisioner(verify=False).ensure(self.plan())
        self.assertEqual(self.events[-1], "policy-remove")


class TestDestroyConvergesOnAReservationRemnant(unittest.TestCase):
    def setUp(self):
        from sandbox.runtimes.managed.repository import NativeRepository

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repository = NativeRepository(Path(self.temporary.name) / "state.json")
        self.machine_id = "sb-0123456789ab"

    def adapter(self):
        from types import SimpleNamespace

        from sandbox.runtimes.managed.adapter import ManagedNativeAdapter

        instance = object.__new__(ManagedNativeAdapter)
        instance.repository = self.repository
        instance.dependencies = SimpleNamespace(cleanup=lambda request, plan: None)
        return instance

    def request(self):
        from sandbox.runtimes.base import OperationRequest

        return OperationRequest("/tmp/project", "destroy")

    def test_a_reservation_with_no_policy_record_is_released_and_converges(self):
        self.repository.reserve_network(self.machine_id, owner=OWNER)

        result = self.adapter()._cleanup(self.request())

        self.assertTrue(result["ok"])
        self.assertTrue(result["cleanup"]["complete"])
        self.assertEqual(result["reason"]["code"], "cleanup_complete")
        self.assertEqual(self.repository.snapshot()["networks"], {})

    def test_repeating_it_converges_without_mutating(self):
        self.repository.reserve_network(self.machine_id, owner=OWNER)
        self.adapter()._cleanup(self.request())

        repeated = self.adapter()._cleanup(self.request())

        self.assertTrue(repeated["ok"])
        self.assertFalse(repeated["mutated"])

    def test_a_foreign_reservation_is_never_released(self):
        self.repository.reserve_network(
            self.machine_id, owner={"project_root": "/foreign", "label": "default"})

        result = self.adapter()._cleanup(self.request())

        self.assertTrue(result["ok"])
        self.assertEqual(result["cleanup"]["removed"], ())
        self.assertIn(self.machine_id, self.repository.snapshot()["networks"])

    def test_a_drifted_reservation_is_retained_for_a_human(self):
        self.repository.reserve_network(self.machine_id, owner=OWNER)
        with self.repository.transaction() as state:
            state["networks"][self.machine_id]["subnet"] = "10.0.0.0/30"

        result = self.adapter()._cleanup(self.request())

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"]["code"], "owned_state_drifted")
        self.assertIn(self.machine_id, self.repository.snapshot()["networks"])


if __name__ == "__main__":
    unittest.main()
