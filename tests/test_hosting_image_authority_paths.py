import json
import os
import tempfile
import unittest
from contextlib import contextmanager, nullcontext, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.hosting.images.provisioning import target_policy_selector


AUTHORITY = {
    "schema_version": 2,
    "rollback_authority_id": "rollback-authority/controller-a",
    "rollback_authority_revision": "rollback-v3",
    "rollback_public_key_path": "/private/authority.pub",
    "rollback_public_key": "ssh-ed25519 Zml4dHVyZQ==",
    "compose_provider_revision": "compose-provider-v2",
}


class AuthorityPathTests(unittest.TestCase):
    def test_authority_lookup_returns_public_projection_without_private_path(self):
        from sandbox.commands.hosting import _cmd_host_image_authority

        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory)); authority_dir = root / "hosting" / "image-activation" / "authorities"
            authority_dir.mkdir(parents=True, mode=0o700)
            selector = target_policy_selector("production", "lenzora", "production")
            path = authority_dir / f"{selector}.json"
            path.write_text(json.dumps(AUTHORITY), encoding="utf-8"); path.chmod(0o600)
            out = StringIO()
            with patch("sandbox.commands.hosting.RUNTIME_DIR", root), redirect_stdout(out):
                _cmd_host_image_authority(
                    {"project": "lenzora"},
                    SimpleNamespace(remote="production", environment="production"))
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["code"], "configured")
        self.assertEqual(payload["authority_id"], AUTHORITY["rollback_authority_id"])
        self.assertNotIn(AUTHORITY["rollback_public_key_path"], out.getvalue())
        self.assertNotIn("Zml4dHVyZQ==", out.getvalue())
        self.assertRegex(payload["public_key_digest"], r"^sha256:[0-9a-f]{64}$")

    def test_missing_authority_is_a_neutral_refusal(self):
        from sandbox.commands.hosting import _cmd_host_image_authority

        with tempfile.TemporaryDirectory() as directory:
            out = StringIO()
            with patch("sandbox.commands.hosting.RUNTIME_DIR", Path(directory)), \
                    redirect_stdout(out), self.assertRaises(SystemExit):
                _cmd_host_image_authority(
                    {"project": "lenzora"},
                    SimpleNamespace(remote="production", environment="production"))
        self.assertEqual(json.loads(out.getvalue()), {
            "schema_version": 2, "ok": False, "code": "authority_unavailable"})

    def test_machine_policy_can_reuse_installed_authority_without_opening_signer(self):
        from sandbox.commands.hosting import _cmd_host_image_provision
        from tests.test_hosting_image_plan_set import make_bundle, policy_mapping

        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory)); root.chmod(0o700); receipts = root / "receipts"; receipts.mkdir(mode=0o700)
            digest = make_bundle(receipts); template = policy_mapping(digest)

            class Port:
                @contextmanager
                def target_mutation_transaction(self, _target):
                    yield

            class Recovery:
                def target_mutation_port(self, _name):
                    return Port()

            compose = {"service": "lenzora-web",
                       "background_services": [item for item in template["persistent_services"]
                                               if item != "lenzora-web"],
                       "init_services": template["one_shot_services"]}
            validated = {"project": "lenzora", "environment": "production", "compose": compose}
            selector = target_policy_selector("production", "lenzora", "production")
            args = SimpleNamespace(
                remote="production", environment="production", provision_phase="machine-policy",
                confirm=True, use_installed_authority=True,
                signed_receipt_directory=str(receipts), policy_authority_id=template["authority_id"],
                policy_revision=1, rollback_public_key=None, rollback_authority_id=None,
                rollback_authority_revision=None, compose_provider_revision=None,
                service_image_binding=[f"{row['service']}={row['image']}"
                                       for row in template["service_image_bindings"]],
                activation_environment_binding=[f"{row['image']}={row['environment_variable']}"
                                               for row in template["activation_environment_bindings"]],
            )
            runtime = root / "runtime"
            authority_path = runtime / "hosting" / "image-activation" / "authorities" / f"{selector}.json"
            authority_path.parent.mkdir(parents=True, mode=0o700)
            from sandbox.hosting.images.provisioning import install_owner_only_json
            install_owner_only_json(authority_path, AUTHORITY)
            out = StringIO()
            with patch("sandbox.commands.hosting.RUNTIME_DIR", runtime), \
                    patch("sandbox.commands.hosting.RecoveryRepository", return_value=Recovery()), \
                    patch("sandbox.hosting.images.provisioning.SshAgentRollbackSigner",
                          side_effect=AssertionError("installed authority must avoid signer")), \
                    redirect_stdout(out):
                _cmd_host_image_provision({}, validated, args)
        payload = json.loads(out.getvalue())
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["authority_result_class"], "replayed")
        self.assertEqual(payload["authority_path"], str(authority_path))

    def test_installed_authority_selection_rejects_explicit_authority_inputs_before_target_lock(self):
        from sandbox.commands.hosting import _cmd_host_image_provision

        class Recovery:
            def target_mutation_port(self, _name):
                raise AssertionError("target lock opened before selector refusal")

        args = SimpleNamespace(
            remote="production", environment="production", provision_phase="machine-policy",
            confirm=True, use_installed_authority=True,
            signed_receipt_directory=None, policy_authority_id=None, policy_revision=None,
            rollback_public_key="/private/key", rollback_authority_id="authority",
            rollback_authority_revision="revision", compose_provider_revision="provider",
            service_image_binding=[], activation_environment_binding=[],
        )
        with patch("sandbox.commands.hosting.RecoveryRepository", return_value=Recovery()), \
                self.assertRaises(SystemExit):
            _cmd_host_image_provision({}, {"project": "lenzora", "environment": "production",
                                            "compose": {}}, args)

    def test_forward_review_reports_not_required_without_predecessor_and_does_not_open_artifacts(self):
        from sandbox.commands.hosting import _cmd_host_image_forward_review

        class Repository:
            def operation_transaction(self, _target): return nullcontext()
            def snapshot(self, _target):
                from sandbox.hosting.images.activation.repository import empty_activation_state
                return empty_activation_state()

        class Recovery:
            def activation_host_state_port(self): return object()
            def target_mutation_port(self, _name): return object()

        args = SimpleNamespace(remote="production", environment="production", request_id="forward-a",
                               expected_generation=0, verified_plan="/must-not-read/plan.json",
                               staged_proof="/must-not-read/proof.json")
        out = StringIO()
        with patch("sandbox.commands.hosting.RecoveryRepository", return_value=Recovery()), \
                patch("sandbox.hosting.images.activation.repository.ActivationRepository",
                      return_value=Repository()), \
                patch("sandbox.commands.hosting.hosting.state_key", return_value="target-a"), \
                redirect_stdout(out):
            _cmd_host_image_forward_review({"project": "lenzora"}, args)
        self.assertEqual(json.loads(out.getvalue()), {
            "schema_version": 1, "ok": True, "code": "not_required"})

    def test_forward_review_refuses_generation_mismatch_before_reading_plan_or_proof(self):
        from sandbox.commands.hosting import _cmd_host_image_forward_review

        class Repository:
            def operation_transaction(self, _target): return nullcontext()
            def snapshot(self, _target): return {
                "generation": 1, "active": None, "settlements": {},
            }

        class Recovery:
            def activation_host_state_port(self): return object()
            def target_mutation_port(self, _name): return object()

        args = SimpleNamespace(remote="production", environment="production", request_id="forward-a",
                               expected_generation=0, verified_plan="/must-not-read/plan.json",
                               staged_proof="/must-not-read/proof.json")
        out = StringIO()
        with patch("sandbox.commands.hosting.RecoveryRepository", return_value=Recovery()), \
                patch("sandbox.hosting.images.activation.repository.ActivationRepository",
                      return_value=Repository()), \
                patch("sandbox.commands.hosting.hosting.state_key", return_value="target-a"), \
                redirect_stdout(out), self.assertRaises(SystemExit):
            _cmd_host_image_forward_review({"project": "lenzora"}, args)
        self.assertEqual(json.loads(out.getvalue())["code"], "authority_unavailable")


if __name__ == "__main__":
    unittest.main()
