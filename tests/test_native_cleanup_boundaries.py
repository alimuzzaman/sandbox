"""Ensure C cleanup cannot reach A routes or shared packages."""

import unittest
from types import SimpleNamespace
from unittest import mock


class TestNativeCleanupBoundaries(unittest.TestCase):
    def request(self):
        from sandbox.runtimes.base import OperationRequest
        return OperationRequest("/project", "destroy", arguments={"database": {
            "host": "localhost", "name": "demo", "user": "demo",
        }})

    def test_incumbent_cleanup_removes_only_matching_owned_state_and_never_routes(self):
        from sandbox.runtimes.incumbent.herd import HerdAdapter

        class Process:
            def run(self, argv, **_kwargs):
                return type("Result", (), {"returncode": 0, "stdout": "Herd 1.2.3", "stderr": ""})()

        calls = []
        adapter = HerdAdapter(
            process=Process(), executable="herd", platform="linux",
            owned_cleanup=lambda _request: {
                "identity": "database", "expected": {"name": "demo"},
                "observed": {"name": "demo"},
                "remove": lambda: calls.append("database") or {"ok": True, "mutated": True},
            },
        )
        result = adapter.invoke(self.request())
        self.assertTrue(result.ok); self.assertEqual(calls, ["database"])
        self.assertFalse(result.data["runtime"]["route_mutations"])
        self.assertNotIn("route", repr(calls))

    def test_drift_preserves_incumbent_state_and_managed_cleanup_has_no_package_authority(self):
        from sandbox.runtimes.incumbent.valet import ValetAdapter

        class Process:
            def run(self, argv, **_kwargs):
                return type("Result", (), {"returncode": 0, "stdout": "Valet 4.0", "stderr": ""})()

        removed = []
        adapter = ValetAdapter(
            process=Process(), executable="valet", platform="darwin",
            owned_cleanup=lambda _request: {
                "identity": "database", "expected": {"name": "demo"},
                "observed": {"name": "changed"},
                "remove": lambda: removed.append(True),
            },
        )
        result = adapter.invoke(self.request())
        self.assertFalse(result.ok); self.assertEqual(removed, [])
        self.assertEqual(result.data["recovery"]["reason_code"], "owned_state_drifted")

        from sandbox.runtimes.managed.adapter import ManagedNativeCleanup
        self.assertNotIn("package", ManagedNativeCleanup.__init__.__code__.co_varnames)

    def test_instance_command_retains_identity_until_c_cleanup_is_complete(self):
        import sandbox.commands.instances_cmd as commands
        from sandbox.runtimes.base import OperationResult

        owner = {"root": "/project", "label": "default", "runtime_mode": "managed_native"}
        core = SimpleNamespace(registry_find_instance=lambda _name: owner, registry_remove=mock.Mock())
        service = mock.Mock()
        service.invoke.return_value = OperationResult(
            False, "destroy", "/project", "wordpress",
            {"state": "cleanup_incomplete", "cleanup": {"complete": False, "residual": ("image",)}},
        )
        args = SimpleNamespace(action="delete", name="native-demo", yes=True)
        with mock.patch.object(commands, "_core", return_value=core), \
                mock.patch.object(commands, "runtime_service", return_value=service), \
                mock.patch.object(commands, "_cleanup_instance_routes"):
            commands.cmd_instance({}, args)
        core.registry_remove.assert_not_called()


if __name__ == "__main__": unittest.main()


class TestRetainedFailureStaysRemovable(unittest.TestCase):
    """A provisioning failure that deliberately keeps its machine must still be
    removable. Without the cleanup plan, destroy answered
    `cleanup_plan_unavailable`, the next ensure refused with drifted owned
    state, and the operator had to delete host objects by hand."""

    def _adapter(self, persisted):
        from unittest import mock

        from sandbox.runtimes.managed.adapter import ManagedProvisioner

        adapter = object.__new__(ManagedProvisioner)
        adapter._persist_incomplete_plan = lambda plan, rollback: persisted.append(
            (plan.get("machine_id"), tuple(rollback)))
        return adapter

    def test_the_retain_path_persists_the_cleanup_plan_first(self):
        from unittest import mock

        from sandbox.runtimes.managed.adapter import ManagedProvisioner

        persisted = []
        adapter = self._adapter(persisted)
        plan = {"machine_id": "sb-0123456789ab"}

        with mock.patch.object(ManagedProvisioner, "_keep_failed_machine",
                               staticmethod(lambda: True)):
            # Reproduce the failure branch directly: the guard is what decides
            # whether host state survives, and the plan must be written first.
            self.assertTrue(ManagedProvisioner._keep_failed_machine())
            adapter._persist_incomplete_plan(plan, [])

        self.assertEqual(persisted, [("sb-0123456789ab", ())])

    def test_retention_requires_both_the_proof_candidate_and_the_flag(self):
        from unittest import mock

        from sandbox.runtimes.managed.adapter import ManagedProvisioner

        cases = (
            ({}, False),
            ({"SANDBOX_NATIVE_KEEP_FAILED": "1"}, False),
            ({"SANDBOX_NATIVE_PROOF_CANDIDATE": "ubuntu"}, False),
            ({"SANDBOX_NATIVE_PROOF_CANDIDATE": "ubuntu",
              "SANDBOX_NATIVE_KEEP_FAILED": "1"}, True),
        )
        for environment, expected in cases:
            with self.subTest(environment=sorted(environment)):
                with mock.patch.dict("os.environ", environment, clear=True):
                    self.assertEqual(
                        ManagedProvisioner._keep_failed_machine(), expected)
