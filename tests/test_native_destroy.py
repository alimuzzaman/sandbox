"""Contract tests for conservative managed-native destruction."""

from pathlib import Path
import tempfile
import unittest


class Component:
    def __init__(self, name, calls): self.name = name; self.calls = calls
    def stop(self, plan): self.calls.append((self.name, "stop", plan["name"])); return {"ok": True, "mutated": True}
    def remove(self, plan): self.calls.append((self.name, "remove", plan["name"])); return {"ok": True, "mutated": True}
    def unmount(self, plan): self.calls.append((self.name, "unmount", plan["name"])); return {"ok": True, "mutated": True}


class TestNativeDestroy(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        from sandbox.runtimes.managed.repository import NativeRepository
        self.repository = NativeRepository(Path(self.temporary.name) / "state.json")
        from tests.test_isolation_verification import policy
        self.policy = policy(); self.owner = {"project_root": "/tmp/project", "label": "default"}
        self.calls = []
        self.expected = {name: {"owned": name, "generation": 1} for name in (
            "services", "database", "network", "machine", "mount", "image", "policy",
        )}

    def cleaner(self):
        from sandbox.runtimes.managed.adapter import ManagedNativeCleanup
        return ManagedNativeCleanup(
            repository=self.repository, services=Component("services", self.calls),
            database=Component("database", self.calls), network=Component("network", self.calls),
            machine=Component("machine", self.calls), image=Component("image", self.calls),
            policy=Component("policy", self.calls),
            observe=lambda name, _plan: self.expected[name],
        )

    def plan(self):
        return {"policy": self.policy, "cleanup": {
            name: {"expected": value, "plan": {"name": name}}
            for name, value in self.expected.items()
        }}

    def request(self):
        from sandbox.runtimes.base import OperationRequest
        return OperationRequest("/tmp/project", "destroy")

    def put_owned(self, section, *, owner=None):
        from sandbox.isolation.models import canonical_digest
        record = {"owner": self.owner if owner is None else owner, "machine": self.policy.machine_id,
                  "resource": section}
        record["last_applied"] = canonical_digest(record)
        self.repository.put_owned(section, self.policy.machine_id, record)

    def test_owned_resources_are_compared_and_removed_in_safe_order_before_state(self):
        for section in ("backends", "policies", "networks"): self.put_owned(section)
        result = self.cleaner().cleanup(self.request(), self.plan())
        self.assertTrue(result["ok"]); self.assertTrue(result["cleanup"]["complete"])
        self.assertEqual([(name, action) for name, action, _ in self.calls], [
            ("services", "stop"), ("database", "remove"), ("machine", "stop"),
            ("network", "remove"), ("image", "unmount"), ("image", "remove"),
            ("policy", "remove"),
        ])
        state = self.repository.snapshot()
        self.assertFalse(state["backends"]); self.assertFalse(state["policies"]); self.assertFalse(state["networks"])
        repeated = self.cleaner().cleanup(self.request(), self.plan())
        self.assertTrue(repeated["ok"]); self.assertFalse(repeated["mutated"])

    def test_foreign_path_image_machine_database_unit_or_network_identity_stops_before_mutation(self):
        self.put_owned("backends", owner={"project_root": "/foreign", "label": "default"})
        result = self.cleaner().cleanup(self.request(), self.plan())
        self.assertFalse(result["ok"]); self.assertEqual(result["cleanup"]["residual"], ("state",))
        self.assertEqual(self.calls, [])
        self.assertIn(self.policy.machine_id, self.repository.snapshot()["backends"])

    def test_missing_ordinary_cleanup_entry_is_not_silently_skipped(self):
        self.put_owned("backends")
        plan = self.plan(); del plan["cleanup"]["services"]
        result = self.cleaner().cleanup(self.request(), plan)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"]["code"], "cleanup_plan_unavailable")
        self.assertEqual(self.calls, [])

    def test_broker_top_level_and_cleanup_entry_must_appear_together(self):
        self.put_owned("backends")
        top_only = self.plan(); top_only["credential_broker"] = {"digest": "a" * 64}
        result = self.cleaner().cleanup(self.request(), top_only)
        self.assertEqual(result["reason"]["code"], "cleanup_plan_unavailable")
        cleanup_only = self.plan()
        cleanup_only["cleanup"]["credential_broker"] = {
            "expected": {"owned": "credential_broker", "generation": 1},
            "plan": {"name": "credential_broker"},
        }
        result = self.cleaner().cleanup(self.request(), cleanup_only)
        self.assertEqual(result["reason"]["code"], "cleanup_plan_unavailable")
        self.assertEqual(self.calls, [])


if __name__ == "__main__": unittest.main()
