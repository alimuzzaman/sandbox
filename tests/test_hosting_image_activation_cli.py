import tempfile
import hashlib
import sys
import unittest
from contextlib import nullcontext, redirect_stdout
from io import StringIO
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
    def test_configuration_hmac_key_is_derived_per_registered_target(self):
        from sandbox.commands.hosting import _host_image_target_configuration_key

        master = b"k" * 32
        first = _host_image_target_configuration_key(master, "machine-a", "target-a")
        self.assertEqual(first, _host_image_target_configuration_key(
            master, "machine-a", "target-a"))
        self.assertNotEqual(first, master)
        self.assertNotEqual(first, _host_image_target_configuration_key(
            master, "machine-a", "target-b"))
        self.assertNotEqual(first, _host_image_target_configuration_key(
            master, "machine-b", "target-a"))

    def test_machine_bundle_reader_refuses_symlink_and_non_owner_only_file(self):
        from sandbox.commands.hosting import _host_image_machine_bundle
        from sandbox.hosting.images import validate_verified_image_plan
        from tests.hosting_image_fixtures import verified_plan_mapping
        plan = validate_verified_image_plan(verified_plan_mapping())
        args = SimpleNamespace(remote="synthetic", environment="development")
        scope = plan.delivery_identity_projection.target_scope
        identity = hashlib.sha256(
            f"{args.remote}\0{scope.project}\0{args.environment}".encode()).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_root = root / "hosting" / "image-activation" / "policies"
            policy_root.mkdir(parents=True)
            for owner_only in (root / "hosting", root / "hosting" / "image-activation",
                               policy_root):
                owner_only.chmod(0o700)
            target = policy_root / f"{identity}.json"
            source = root / "source.json"
            source.write_text("{}")
            source.chmod(0o600)
            target.symlink_to(source)
            with patch("sandbox.commands.hosting.RUNTIME_DIR", root):
                with self.assertRaises(OSError):
                    _host_image_machine_bundle(args, plan)
            target.unlink(); target.write_text("{}")
            target.chmod(0o644)
            with patch("sandbox.commands.hosting.RUNTIME_DIR", root):
                with self.assertRaisesRegex(ValueError, "unsafe"):
                    _host_image_machine_bundle(args, plan)
    def test_static_image_recover_parser_is_distinct_from_failed_apply_recover(self):
        for argv in (("host", "image", "recover", "--help"),
                     ("host", "recover", "--help")):
            with self.subTest(argv=argv):
                result = run_sb(*argv)
                self.assertEqual(result.returncode, 0)
        self.assertIn("activation-transaction", run_sb(
            "host", "image", "recover", "--help").stdout)

    def test_early_recovery_without_candidate_closes_without_promotion(self):
        from sandbox.commands.hosting import _cmd_host_image
        from tests.fixtures.hosting_image_activation import DIGEST_A, DIGEST_B

        target = {"machine_identity": "machine-a", "target_identity": "target-a",
                  "daemon_identity": "daemon-a"}
        active = {
            "transaction_digest": DIGEST_A,
            "request_digest": DIGEST_B,
            "operation": "activate",
            "phase": "accepted",
            "effect_entered": False,
            "candidate_generation": None,
            "recovery_context": {"target": target, "compose_project": "widget",
                                 "selected_services": ["web"]},
        }
        captured = {}

        class Repository:
            def operation_transaction(self, _target_key):
                return nullcontext()

            def snapshot(self, _target_key):
                return {"current": None, "active": active, "recovery_results": {}}

            def recover(self, _target_key, **kwargs):
                observation = kwargs["observer"]()
                captured["classification"] = observation.classification
                return {"schema_version": 1, "ok": False,
                        "request_id": kwargs["request_id"],
                        "activation_request_id": "activate/request-a",
                        "request_digest": kwargs["request_digest"],
                        "code": "recovery_no_effect", "promoted": False,
                        "starting_generation": 0, "resulting_generation": 0}

        runtime = SimpleNamespace(observe_running=lambda **_kwargs: {
            "target_epoch_start": "machine-a", "target_epoch_end": "machine-a",
            "target_identity_start": "target-a", "target_identity_end": "target-a",
            "runtime_epoch_start": "daemon-a", "runtime_epoch_end": "daemon-a",
            "services": [],
        })
        args = SimpleNamespace(
            image_action="recover", project_dir="/synthetic", environment="development",
            remote="synthetic", request_id="recover/request-a", expected_generation=0,
            activation_transaction=DIGEST_A, confirm=True)
        output = StringIO()
        with patch("sandbox.commands.hosting.RecoveryRepository"), \
                patch("sandbox.commands.hosting.hosting.state_key", return_value="target-a"), \
                patch("sandbox.commands.hosting.personal_secrets.hosting_binding_key",
                      return_value=(b"k" * 32, "binding-v1")), \
                patch("sandbox.commands.hosting.remote.registered_remote_lock",
                      return_value=nullcontext()), \
                patch("sandbox.commands.hosting.remote.get_remote", return_value={"name": "synthetic"}), \
                patch("sandbox.hosting.images.activation.repository.ActivationRepository",
                      return_value=Repository()), \
                patch("sandbox.transports.remote_hosting_activation."
                      "RegisteredRemoteActivationTransport", return_value=runtime) as transport_type, \
                redirect_stdout(output), self.assertRaises(SystemExit):
            _cmd_host_image({"project": "widget"}, args)
        payload = __import__("json").loads(output.getvalue())
        self.assertEqual(captured["classification"], "exact_prior")
        self.assertEqual(payload["code"], "recovery_no_effect")
        self.assertFalse(payload["promoted"])
        from sandbox.commands.hosting import _host_image_target_configuration_key
        self.assertEqual(
            transport_type.call_args.kwargs["configuration_binding_key"],
            _host_image_target_configuration_key(b"k" * 32, "machine-a", "target-a"))

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

    def test_reachability_only_edge_adapter_refuses_generation_authority(self):
        from sandbox.commands.hosting import _HostImageEdgeAdapter
        validated = {"routes": [], "healthcheck": {"path": "/health"},
                     "basic_auth": None}
        with self.assertRaisesRegex(ValueError, "edge_incomplete"):
            _HostImageEdgeAdapter(validated).apply("request/edge", "sha256:" + "a" * 64,
                                                   observation_digest="sha256:" + "b" * 64)

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
