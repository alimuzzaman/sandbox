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
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="activation-cli-delivery-")
        self.addCleanup(directory.cleanup)
        self.delivery_root = Path(directory.name)
        for module in ("sandbox.commands.hosting", "sandbox.delivery.hosting"):
            patcher = patch(module + ".RUNTIME_DIR", self.delivery_root / "runtime")
            patcher.start()
            self.addCleanup(patcher.stop)


    def test_stage_refuses_missing_boolean_and_unsupported_plan_schemas_neutrally(self):
        import json
        from sandbox.commands.hosting import _cmd_host_stage

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); plan_path = root / "plan.json"
            args = SimpleNamespace(project_dir=str(root), environment="development",
                remote="synthetic", request_id="stage/schema", verified_plan=str(plan_path),
                expected_generation=0, stage_status=False, confirm=True)
            for raw in ({}, {"schema_version": True}, {"schema_version": 3}):
                with self.subTest(raw=raw):
                    plan_path.write_text(json.dumps(raw))
                    output = StringIO()
                    with redirect_stdout(output), self.assertRaises(SystemExit):
                        _cmd_host_stage(args)
                    payload = json.loads(output.getvalue())
                    self.assertEqual(payload["schema_version"], 0)
                    self.assertEqual(payload["code"], "plan_invalid")

    def test_activate_and_rollback_refuse_bad_plan_schemas_neutrally(self):
        import json
        from sandbox.commands.hosting import _cmd_host_image

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); plan_path = root / "plan.json"
            proof_path = root / "proof.json"; proof_path.write_text("{}")
            args = SimpleNamespace(image_action="activate", project_dir=str(root),
                environment="development", remote="synthetic", request_id="activate/schema",
                expected_generation=0, verified_plan=str(plan_path),
                staged_proof=str(proof_path), admission_deadline="2999-01-01T00:00:00Z",
                confirm=True)
            for action in ("activate", "rollback"):
                args.image_action = action
                for raw in ({}, {"schema_version": True}, {"schema_version": 3}):
                    with self.subTest(action=action, raw=raw):
                        plan_path.write_text(json.dumps(raw))
                        output = StringIO()
                        with redirect_stdout(output), self.assertRaises(SystemExit):
                            _cmd_host_image({"project": "widget", "project_root": str(self.delivery_root),
                "environment": "development"}, args)
                        payload = json.loads(output.getvalue())
                        self.assertEqual(payload["schema_version"], 0)
                        self.assertEqual(payload["code"], "artifact_invalid")

    def test_activate_refuses_bad_staged_proof_schema_before_machine_bundle(self):
        import json
        from sandbox.commands.hosting import _cmd_host_image
        from tests.hosting_image_fixtures import verified_plan_mapping

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); plan_path = root / "plan.json"
            proof_path = root / "proof.json"
            plan_path.write_text(json.dumps(verified_plan_mapping()))
            args = SimpleNamespace(image_action="activate", project_dir=str(root),
                environment="development", remote="synthetic", request_id="activate/proof-schema",
                expected_generation=0, verified_plan=str(plan_path),
                staged_proof=str(proof_path), admission_deadline="2999-01-01T00:00:00Z",
                confirm=True)
            for raw in ({}, {"schema_version": True}, {"schema_version": 3}):
                with self.subTest(raw=raw):
                    proof_path.write_text(json.dumps(raw))
                    output = StringIO()
                    with patch("sandbox.commands.hosting._host_image_machine_bundle") as bundle, \
                            redirect_stdout(output), self.assertRaises(SystemExit):
                        _cmd_host_image({"project": "widget", "project_root": str(self.delivery_root),
                "environment": "development"}, args)
                    bundle.assert_not_called()
                    payload = json.loads(output.getvalue())
                    self.assertEqual(payload["schema_version"], 0)
                    self.assertEqual(payload["code"], "artifact_invalid")

    def test_v2_runtime_selector_is_manifest_derived_and_includes_deployed_override(self):
        from sandbox.commands.hosting import _host_image_v2_runtime_selector

        snapshot = SimpleNamespace(
            snapshot_id="compose-snapshot/a", snapshot_digest="sha256:" + "a" * 64,
            provider_revision="provider-v2", configuration_digest="sha256:" + "b" * 64,
            target={"machine_identity": "machine-a", "target_identity": "target-a",
                    "daemon_identity": "daemon-a"})
        validated = {"project": "widget", "environment": "production",
                     "compose": {"files": ["compose.yml", "compose.prod.yml"]}}
        with patch("sandbox.commands.hosting.remote.resolve_sandbox_home",
                   return_value="/srv/sandbox"), \
                patch("sandbox.commands.hosting.hosting.compose_project_name",
                      return_value="widget-production"):
            selector = _host_image_v2_runtime_selector(
                validated, {"name": "synthetic"}, snapshot)
        self.assertEqual(selector["compose_files"], (
            "/srv/sandbox/deploy-src/hosts/widget/compose.yml",
            "/srv/sandbox/deploy-src/hosts/widget/compose.prod.yml",
            "/srv/sandbox/runtime/hosts/widget/production/compose.override.yml"))
        self.assertEqual(selector["environment_file"],
                         "/srv/sandbox/runtime/hosts/widget/production/environment.env")
        self.assertNotIn("compose_files", snapshot.__dict__)

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
            _cmd_host_image({"project": "widget", "project_root": str(self.delivery_root),
                "environment": "development"}, args)
        payload = __import__("json").loads(output.getvalue())
        self.assertEqual(captured["classification"], "exact_prior")
        self.assertEqual(payload["code"], "recovery_no_effect")
        self.assertFalse(payload["promoted"])
        from sandbox.commands.hosting import _host_image_target_configuration_key
        self.assertEqual(
            transport_type.call_args.kwargs["configuration_binding_key"],
            _host_image_target_configuration_key(b"k" * 32, "machine-a", "target-a"))

    def test_recovery_dispatches_from_stored_v2_transaction_schema(self):
        from sandbox.commands.hosting import _cmd_host_image
        from tests.fixtures.hosting_image_activation import DIGEST_A, DIGEST_B

        target = {"machine_identity": "machine-a", "target_identity": "target-a",
                  "daemon_identity": "daemon-a"}
        active = {"schema_version": 2, "transaction_digest": DIGEST_A,
            "request_digest": DIGEST_B, "operation": "activate", "phase": "accepted",
            "effect_entered": False, "candidate_generation": None,
            "recovery_context": {"target": target, "compose_project": "widget",
                                 "selected_services": ["web"]}}
        called = {}

        class Repository:
            def operation_transaction(self, _target_key): return nullcontext()
            def snapshot(self, _target_key):
                return {"current": None, "active": active, "recovery_results": {}}
            def recover(self, *_args, **_kwargs):
                raise AssertionError("v2 state reached v1 recovery")
            def recover_v2(self, _target_key, **kwargs):
                called["observation"] = kwargs["observer"]().classification
                return {"schema_version": 2, "ok": False,
                    "request_id": kwargs["request_id"], "request_digest": kwargs["request_digest"],
                    "activation_request_id": "activate/v2",
                    "code": "recovery_no_effect", "promoted": False,
                    "starting_generation": 0, "resulting_generation": 0}

        runtime = SimpleNamespace(observe_running=lambda **_kwargs: {
            "target_epoch_start": "machine-a", "target_epoch_end": "machine-a",
            "target_identity_start": "target-a", "target_identity_end": "target-a",
            "runtime_epoch_start": "daemon-a", "runtime_epoch_end": "daemon-a",
            "services": []})
        args = SimpleNamespace(image_action="recover", project_dir="/synthetic",
            environment="development", remote="synthetic", request_id="recover/v2",
            expected_generation=0, activation_transaction=DIGEST_A, confirm=True)
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
                      "RegisteredRemoteActivationTransport", return_value=runtime), \
                redirect_stdout(output), self.assertRaises(SystemExit):
            _cmd_host_image({"project": "widget", "project_root": str(self.delivery_root),
                "environment": "development"}, args)
        payload = __import__("json").loads(output.getvalue())
        self.assertEqual(called["observation"], "exact_prior")
        self.assertEqual(payload["schema_version"], 2)

    def test_missing_selectors_refuse_before_manifest_or_state_open(self):
        result = run_sb("host", "image", "activate", "--json")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("explicit", result.stderr + result.stdout)

    def test_v2_activate_dispatches_with_only_manifest_derived_compose_paths(self):
        import json
        from sandbox.commands.hosting import _cmd_host_image
        from tests.test_hosting_image_activation_v2 import artifacts, grant_for

        plan, proof, snapshot = artifacts(graph=True)
        grant = grant_for(plan, proof, snapshot=snapshot)
        bundle = {"schema_version": 2, "compose_snapshot": snapshot.as_mapping(),
            "rollback_grant": grant.as_mapping(),
            "rollback_grant_public_key": "ssh-ed25519 AAAA",
            "stage_ledger": {"authority": "feature-050-stage-ledger-v2",
                             "revision": 1}}
        captured = {}

        from sandbox.hosting.images.activation.repository import ActivationRepository
        from sandbox.hosting.images.activation.v2_service import ActivationServiceV2
        from tests.test_hosting_image_activation_v2 import (
            FakeHostStatePort, FakeTargetMutationPort, FakeStageRepositoryPort,
            FakeRuntimeV2, FakeEdgeV2, FakeGrantVerifier,
        )
        repository = ActivationRepository(host_state_port=FakeHostStatePort(),
            stage_repository=FakeStageRepositoryPort(),
            target_mutation_port=FakeTargetMutationPort())

        class Service:
            def __init__(self, **kwargs):
                captured["service"] = kwargs
                self.owner = ActivationServiceV2(repository=kwargs["repository"],
                    runtime_adapter=FakeRuntimeV2(proof), edge_adapter=FakeEdgeV2(),
                    rollback_grant_verifier=FakeGrantVerifier(), clock=lambda: 100)
            def execute(self, request, **kwargs):
                captured["request"] = request
                captured["execute"] = kwargs
                return self.owner.execute(request, **kwargs)

        from tests.test_hosting import _public_acme_manifest
        (self.delivery_root / "sandbox.hosting.yml").write_text(
            _public_acme_manifest().replace("project: example-site", "project: lenzora")
            .replace("compose.yml", "docker-compose.hosted-production.yml"))
        (self.delivery_root / "docker-compose.hosted-production.yml").write_text("services: {}\n")
        validated = {"project": "lenzora", "environment": "production",
            "project_root": str(self.delivery_root),
            "compose": {"files": ["docker-compose.hosted-production.yml"]},
            "routes": [], "healthcheck": {"path": "/health"}, "basic_auth": None}
        args = SimpleNamespace(image_action="activate", project_dir="/synthetic",
            environment="production", remote="synthetic", request_id="activate/v2-cli",
            expected_generation=0, verified_plan=None, staged_proof=None,
            admission_deadline="2999-01-01T00:00:00Z", confirm=True)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan_path = root / "plan.json"; proof_path = root / "proof.json"
            plan_path.write_text(json.dumps(plan.as_mapping()))
            proof_path.write_text(json.dumps(proof.as_mapping()))
            args.verified_plan = str(plan_path); args.staged_proof = str(proof_path)
            output = StringIO()
            with patch("sandbox.commands.hosting.RecoveryRepository") as recovery, \
                    patch("sandbox.commands.hosting._host_image_machine_bundle",
                          return_value=bundle), \
                    patch("sandbox.commands.hosting.hosting.state_key",
                          return_value="target-a"), \
                    patch("sandbox.commands.hosting.hosting.compose_project_name",
                          return_value="lenzora-production"), \
                    patch("sandbox.commands.hosting._authenticated_machine_identity",
                          return_value="machine-a"), \
                    patch("sandbox.commands.hosting.personal_secrets.hosting_binding_key",
                          return_value=(b"k" * 32, "binding-v1")), \
                    patch("sandbox.commands.hosting.remote.registered_remote_lock",
                          return_value=nullcontext()), \
                    patch("sandbox.commands.hosting.remote.get_remote",
                          return_value={"name": "synthetic", "ssh": "fixture@example.test"}), \
                    patch("sandbox.commands.hosting.remote.resolve_sandbox_home",
                          return_value="/srv/sandbox"), \
                    patch("sandbox.commands.hosting._verify_edge"), \
                    patch("sandbox.commands.hosting._guarded_host_apply_plan",
                          return_value={"records": [], "cloudflare": {
                              "configured": True, "records": []}}), \
                    patch("sandbox.hosting.images.activation.repository.ActivationRepository",
                          return_value=repository), \
                    patch("sandbox.hosting.images.activation.v2_service.ActivationServiceV2",
                          Service), redirect_stdout(output):
                recovery.return_value.activation_host_state_port.return_value = object()
                recovery.return_value.target_mutation_port.return_value = object()
                _cmd_host_image(validated, args)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["ok"], payload)
        self.assertTrue(payload["delivery"]["delivery_succeeded"], payload)
        self.assertEqual(captured["request"].schema_version, 2)
        candidate_id = hashlib.sha256(snapshot.snapshot_id.encode()).hexdigest()
        self.assertEqual(captured["execute"]["compose_files"], (
            f"/srv/sandbox/runtime/hosts/lenzora/production/activation-inputs/{candidate_id}/effective.json",))
        self.assertEqual(captured["execute"]["compose_project"], "lenzora-production")

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

    def test_initial_immutable_activation_defers_live_edge_check_until_after_effect(self):
        from sandbox.commands.hosting import _HostImageEdgeAdapter
        validated = {"routes": [{"hostname": "example.test", "mode": "serve",
                                  "primary": True}],
                     "healthcheck": {"path": "/health"}, "basic_auth": None}

        class Repository:
            def snapshot(self, _target):
                return {
                    "generation": 0, "current": None, "previous": None,
                    "active": None, "results": {}, "tombstones": {},
                    "recovery_provisional": None, "recovery_results": {},
                }

        with patch("sandbox.commands.hosting._verify_edge") as verify:
            observed = _HostImageEdgeAdapter(
                validated, activation_repository=Repository(),
                target_identity="target-a").observe_plan()
        verify.assert_not_called()
        self.assertEqual(observed["routes"], [{
            "hostname": "example.test", "mode": "serve", "target": None,
            "primary": True, "healthcheck_path": "/health",
        }])

    def test_initial_edge_bootstrap_allows_only_terminal_refusal_history(self):
        from sandbox.commands.hosting import _HostImageEdgeAdapter
        validated = {"routes": [], "healthcheck": {"path": "/health"}}
        refusal = {"result_class": "refused", "ok": False,
                   "starting_generation": 0, "resulting_generation": 0,
                   "generation_digest": None, "code": "artifact_invalid"}
        state = {"generation": 0, "current": None, "previous": None,
                 "active": None, "results": {"request": {"result": refusal}},
                 "tombstones": {}, "recovery_provisional": None, "recovery_results": {}}
        repository = SimpleNamespace(snapshot=lambda _target: state)
        adapter = _HostImageEdgeAdapter(validated, activation_repository=repository,
                                       target_identity="target-a")
        with patch("sandbox.commands.hosting._verify_edge") as verify:
            adapter.observe_plan()
            verify.assert_not_called()
            for changes in ({"result_class": "uncertain"}, {"result_class": "success"},
                            {"starting_generation": 1}, {"resulting_generation": 1},
                            {"generation_digest": "sha256:" + "a" * 64}, {"ok": True}):
                state["results"] = {"request": {"result": {**refusal, **changes}}}
                adapter.observe_plan()
                verify.assert_called_once(); verify.reset_mock()
            state["results"] = {"request": {"result": refusal}}
            for field, value in (("generation", 1), ("current", {}), ("previous", {}),
                                 ("active", {}), ("tombstones", {"request": {}}),
                                 ("recovery_provisional", {}), ("recovery_results", {"r": {}})):
                original = state[field]; state[field] = value
                adapter.observe_plan()
                verify.assert_called_once(); verify.reset_mock()
                state[field] = original

    def test_initial_edge_bootstrap_allows_only_proven_effect_free_recovery(self):
        from sandbox.commands.hosting import _HostImageEdgeAdapter
        refusal = {"result_class": "refused", "ok": False,
                   "starting_generation": 0, "resulting_generation": 0,
                   "generation_digest": None, "code": "recovery_no_effect"}
        recovery = {"code": "recovery_no_effect", "ok": False, "promoted": False,
                    "starting_generation": 0, "resulting_generation": 0,
                    "activation_request_id": "activate-a"}
        state = {"generation": 0, "current": None, "previous": None, "active": None,
                 "results": {"activate-a": {"result": refusal}}, "tombstones": {},
                 "recovery_provisional": None, "recovery_results": {"recover-a": recovery}}
        adapter = _HostImageEdgeAdapter(
            {"routes": [], "healthcheck": {"path": "/health"}},
            activation_repository=SimpleNamespace(snapshot=lambda _target: state),
            target_identity="target-a")
        with patch("sandbox.commands.hosting._verify_edge") as verify:
            adapter.observe_plan()
            verify.assert_not_called()
            for changes in ({"code": "recovery_conflict"}, {"code": "committed"},
                            {"ok": True}, {"promoted": True}, {"starting_generation": 1},
                            {"resulting_generation": 1}, {"starting_generation": False},
                            {"activation_request_id": "missing"}):
                state["recovery_results"] = {"recover-a": {**recovery, **changes}}
                adapter.observe_plan()
                verify.assert_called_once(); verify.reset_mock()
            state["recovery_results"] = {"recover-a": recovery}
            state["active"] = {"request_id": "next-activation"}
            adapter.observe_plan()
            verify.assert_called_once()

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
