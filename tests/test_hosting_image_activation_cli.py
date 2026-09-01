import tempfile
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from pathlib import Path

from tests.subprocess_support import run_test_process

ROOT = Path(__file__).parent.parent


def run_sb(*args):
    return run_test_process(
        (sys.executable, str(ROOT / "sb"), *args),
        env={"PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin"},
        text=True, capture_output=True)


class ActivationCliTests(unittest.TestCase):
    def test_static_image_recover_parser_is_distinct_from_failed_apply_recover(self):
        for argv in (("host", "image", "recover", "--help"),
                     ("host", "recover", "--help")):
            with self.subTest(argv=argv):
                result = run_sb(*argv)
                self.assertEqual(result.returncode, 0)
        self.assertIn("activation-transaction", run_sb(
            "host", "image", "recover", "--help").stdout)

    def test_missing_selectors_refuse_before_manifest_or_state_open(self):
        result = run_sb("host", "image", "activate", "--json")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("explicit", result.stderr + result.stdout)

    def test_old_opaque_state_is_not_activation_authority(self):
        from sandbox.hosting.images.activation.repository import decode_activation_state
        legacy = {"image_operation": {"schema_version": 2, "receipt": "opaque"}}
        self.assertEqual(decode_activation_state(None)["generation"], 0)
        self.assertNotIn("image_operation", decode_activation_state(None))

    def test_edge_observation_derives_current_manifest_routes_not_caller_echo(self):
        from sandbox.commands.hosting import _HostImageEdgeAdapter
        from sandbox.hosting.images.activation.models import activation_digest
        validated = {"routes": [{"hostname": "example.test", "mode": "serve",
                                  "primary": True}],
                     "healthcheck": {"path": "/health"}, "basic_auth": None}
        expected = [{"hostname": "example.test", "mode": "serve", "target": None,
                     "primary": True, "healthcheck_path": "/health"}]
        with patch("sandbox.commands.hosting._verify_edge") as verify:
            observed = _HostImageEdgeAdapter(validated).observe_plan()
        verify.assert_called_once()
        self.assertEqual(observed["routes"], expected)
        self.assertEqual(observed["route_digest"], activation_digest(
            "sandbox.hosting.images.activation-edge-routes.v1", expected))

    def test_remote_init_private_input_never_enters_ssh_command_or_captured_output(self):
        from sandbox.commands.hosting import _host_image_argv_runner
        captured = {}
        def ssh_run(entry, command, **kwargs):
            captured.update(command=command, input_data=kwargs.get("input_data"))
            return SimpleNamespace(returncode=0, stdout="[redacted]", stderr="")
        with patch("sandbox.commands.hosting.remote.ssh_run", side_effect=ssh_run):
            result = _host_image_argv_runner({"name": "synthetic"})(
                argv=("docker", "create", "--env", "DATABASE_URL", "image"),
                environment={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
                private_environment={"DATABASE_URL": "private-test-value"},
                private_environment_source={}, redact_environment_keys=None,
                timeout_seconds=30, max_output_bytes=1024)
        self.assertNotIn("private-test-value", captured["command"])
        self.assertNotIn("private-test-value", result["stdout"] + result["stderr"])
        self.assertIn("DATABASE_URL", captured["command"])


if __name__ == "__main__": unittest.main()
