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
