from __future__ import annotations

import json
from contextlib import contextmanager, redirect_stdout
from io import StringIO
from pathlib import Path
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.hosting.images.provisioning import (
    ProvisioningError, SshAgentRollbackSigner, install_owner_only_json,
    install_owner_only_json_pair,
    prepare_activation_bundle, prepare_machine_policy, prepare_stage_bundle,
    target_policy_selector,
)
from sandbox.hosting.images.staging_models import HelperIdentity, StagingTarget
from tests.test_hosting_image_plan_set import FakeVerifier, make_bundle, policy_mapping
from tests.test_hosting_image_staging_v2 import observation, plan_set, policy_set, request_set


class ProvisioningTests(unittest.TestCase):
    def test_missing_authority_refuses_before_target_mutation_port_is_opened(self):
        from sandbox.commands.hosting import _cmd_host_image_provision
        class Recovery:
            def target_mutation_port(self, _name):
                raise AssertionError("authority must be complete before opening target state")
        args = SimpleNamespace(remote="production", environment="production",
            provision_phase="machine-policy", confirm=True,
            signed_receipt_directory=None, policy_authority_id=None, policy_revision=None,
            rollback_public_key=None, rollback_authority_id=None,
            rollback_authority_revision=None, compose_provider_revision=None,
            service_image_binding=[], activation_environment_binding=[])
        output = StringIO()
        with patch("sandbox.commands.hosting.RecoveryRepository", return_value=Recovery()), \
                redirect_stdout(output), self.assertRaises(SystemExit):
            _cmd_host_image_provision({}, {"project": "p", "environment": "production",
                "compose": {}}, args)
        self.assertEqual(json.loads(output.getvalue())["code"], "artifact_invalid")

    def test_machine_policy_cli_installs_authority_pair_and_replays(self):
        from sandbox.commands.hosting import _cmd_host_image_provision
        template = None
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            root = Path(temp); root.chmod(0o700); receipts = root / "receipts"; receipts.mkdir()
            digest = make_bundle(receipts); template = policy_mapping(digest)
            private = root / "signer"
            subprocess.run(("/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "",
                "-f", str(private)), check=True)
            public = private.with_suffix(".pub"); public.chmod(0o600)
            class Port:
                @contextmanager
                def target_mutation_transaction(self, _target): yield
            class Recovery:
                def target_mutation_port(self, _name): return Port()
            compose = {"service": "lenzora-web",
                "background_services": [item for item in template["persistent_services"]
                                        if item != "lenzora-web"],
                "init_services": template["one_shot_services"]}
            validated = {"project": "lenzora", "environment": "production",
                         "compose": compose}
            args = SimpleNamespace(remote="production", environment="production",
                provision_phase="machine-policy", confirm=True,
                signed_receipt_directory=str(receipts),
                policy_authority_id=template["authority_id"], policy_revision=1,
                rollback_public_key=str(public),
                rollback_authority_id="rollback-authority/controller-a",
                rollback_authority_revision="rollback-v2",
                compose_provider_revision="compose-provider-v2",
                service_image_binding=[f"{row['service']}={row['image']}"
                    for row in template["service_image_bindings"]],
                activation_environment_binding=[
                    f"{row['image']}={row['environment_variable']}"
                    for row in template["activation_environment_bindings"]])
            outputs = []
            with patch("sandbox.commands.hosting.RUNTIME_DIR", root / "runtime"), \
                    patch("sandbox.commands.hosting.RecoveryRepository", return_value=Recovery()):
                for _ in range(2):
                    stream = StringIO()
                    with redirect_stdout(stream): _cmd_host_image_provision({}, validated, args)
                    outputs.append(json.loads(stream.getvalue()))
            self.assertEqual([item["result_class"] for item in outputs],
                             ["installed", "replayed"])
            self.assertEqual([item["authority_result_class"] for item in outputs],
                             ["installed", "replayed"])
            for item in outputs:
                self.assertNotIn(str(private), json.dumps(item))

    def test_stage_bundle_cli_mints_fixed_binding_without_printing_secret(self):
        from sandbox.commands.hosting import _cmd_host_image_provision
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            root = Path(temp); root.chmod(0o700); project = root / "project"; project.mkdir()
            personal = root / ".env"; canary = "secret-stage-canary-never-output"
            personal.write_text(f"GHCR_TOKEN={canary}\n"); personal.chmod(0o600)
            plan = plan_set(); plan_path = root / "plan.json"
            plan_path.write_text(json.dumps(plan.as_mapping()))
            class Port:
                @contextmanager
                def target_mutation_transaction(self, _target): yield
            class Recovery:
                def target_mutation_port(self, _name): return Port()
            validated = {"project": "lenzora", "environment": "production",
                "project_root": str(project), "compose": {}}
            args = SimpleNamespace(remote="production", environment="production",
                provision_phase="stage-bundle", confirm=True, verified_plan=str(plan_path),
                expected_generation=0, credential_source_reference="personal/GHCR_TOKEN",
                credential_expires_at="2999-01-01T00:00:00Z")
            output = StringIO()
            runtime = root / "runtime"
            (runtime / "hosting").mkdir(parents=True, mode=0o700)
            with patch("sandbox.commands.hosting.RUNTIME_DIR", runtime), \
                    patch("sandbox.core._paths.RUNTIME_DIR", runtime), \
                    patch("sandbox.hosting.images.staging_repository.RUNTIME_DIR", runtime), \
                    patch("sandbox.core._paths.ENV_LOCAL", personal), \
                    patch("sandbox.commands.hosting.RecoveryRepository", return_value=Recovery()), \
                    patch("sandbox.commands.hosting._authenticated_machine_identity",
                          return_value="machine-a"), \
                    patch("sandbox.transports.remote_hosting_images.RegisteredRemoteImageTransport.observe_authority",
                          return_value={"daemon_identity": "daemon-a", "helper": {}}), \
                    redirect_stdout(output):
                try: _cmd_host_image_provision({}, validated, args)
                except SystemExit: pass
            payload = json.loads(output.getvalue())
            self.assertTrue(payload["ok"], payload)
            self.assertEqual(payload["stage_generation"], 0)
            self.assertNotIn(canary, output.getvalue())
            installed = json.loads(Path(payload["installed_path"]).read_text())
            self.assertEqual(installed["binding"]["owner"], "personal")
            self.assertNotIn(canary, json.dumps(installed))

    def test_machine_policy_is_receipt_bound_and_selector_is_fixed(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            root = Path(temp); digest = make_bundle(root); receipt = (root / "receipt.json").read_bytes()
            template = policy_mapping(digest)
            policy = prepare_machine_policy(
                receipt_bytes=receipt, authority_id=template["authority_id"],
                policy_revision=1, target_scope=template["target_scope"],
                persistent_services=tuple(template["persistent_services"]),
                one_shot_services=tuple(template["one_shot_services"]),
                service_image_bindings={row["service"]: row["image"]
                    for row in template["service_image_bindings"]},
                activation_environment_bindings={row["image"]: row["environment_variable"]
                    for row in template["activation_environment_bindings"]})
        self.assertEqual(policy.approved_receipt_digest, digest)
        self.assertEqual(target_policy_selector("production", "lenzora", "production"),
            __import__("hashlib").sha256(b"production\0lenzora\0production").hexdigest())

    def test_owner_only_install_replays_and_refuses_conflict_or_symlink(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            root = Path(temp); root.chmod(0o700); path = root / "policy" / "a.json"
            self.assertEqual(install_owner_only_json(path, {"schema_version": 2}), "installed")
            self.assertEqual(install_owner_only_json(path, {"schema_version": 2}), "replayed")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaisesRegex(ProvisioningError, "conflict"):
                install_owner_only_json(path, {"schema_version": 3})
            linked = root / "linked"; linked.symlink_to(root / "policy", target_is_directory=True)
            with self.assertRaisesRegex(ProvisioningError, "path_unsafe"):
                install_owner_only_json(linked / "b.json", {"schema_version": 2})

    def test_pair_preflight_leaves_no_new_companion_on_known_conflict(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            root = Path(temp); root.chmod(0o700)
            conflict = root / "policy.json"; companion = root / "authority.json"
            install_owner_only_json(conflict, {"value": "old"})
            with self.assertRaisesRegex(ProvisioningError, "conflict"):
                install_owner_only_json_pair(((companion, {"value": "new"}),
                    (conflict, {"value": "different"})))
            self.assertFalse(companion.exists())

    def test_stage_bundle_binds_plan_target_helper_and_existing_broker_metadata(self):
        from sandbox.isolation.credential_binding import CredentialBinding
        plan = plan_set(); scope = plan.policy.target_scope
        target = StagingTarget("machine-a",
            f"{scope.remote}/{scope.project}/{scope.environment}", "daemon-a")
        helper = HelperIdentity("sha256:" + "9" * 64, "sandbox-image-stage-helper-v2",
            "a" * 40, "systemd-cgroup-v2-batch-stage-v2")
        binding = CredentialBinding(
            binding_id="binding-a", instance_id="machine-a", source_reference="personal/GHCR_TOKEN",
            policy_digest="a" * 64, egress_digest="b" * 64, broker_digest="c" * 64,
            scheme="https", host="ghcr.io", port=443, method="GET", path="/token",
            auth_form="authorization_bearer", expires_at="2999-01-01T00:00:00Z",
            owner="hosting/image-stage", version=3, state="ready")
        bundle = prepare_stage_bundle(plan=plan, target=target, helper=helper,
            binding=binding, credential_reference_revision="credential-revision-a",
            secret_sources={})
        self.assertEqual(set(bundle), {"policy", "binding", "secret_sources"})
        self.assertEqual(bundle["policy"]["target"], target.as_mapping())
        self.assertNotIn("GHCR_TOKEN", json.dumps(bundle).replace("personal/GHCR_TOKEN", ""))
        wrong = StagingTarget("machine-a", "other/target/value", "daemon-a")
        with self.assertRaisesRegex(ProvisioningError, "target_mismatch"):
            prepare_stage_bundle(plan=plan, target=wrong, helper=helper, binding=binding,
                credential_reference_revision="credential-revision-a", secret_sources={})

    def test_broker_revision_observer_returns_no_credential_value(self):
        from sandbox.isolation.credential_binding import CredentialBinding
        from sandbox.isolation.credential_resolver import SecretReferenceResolver
        from sandbox.secrets.sources import SourceRegistry
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            root = Path(temp); root.chmod(0o700); source = root / ".env"
            source.write_text("GHCR_TOKEN=secret-canary-never-output\n"); source.chmod(0o600)
            binding = CredentialBinding(
                binding_id="binding-a", instance_id="machine-a",
                source_reference="personal/GHCR_TOKEN", policy_digest="a" * 64,
                egress_digest="b" * 64, broker_digest="c" * 64, scheme="https",
                host="ghcr.io", port=443, method="GET", path="/token",
                auth_form="authorization_bearer", expires_at="2999-01-01T00:00:00Z",
                owner="personal", state="ready")
            resolver = SecretReferenceResolver(SourceRegistry(
                root, {}, personal_path=source, project_scope=root), owner=binding.owner)
            revision = resolver.observe_reference_revision(binding, revision_key=b"k" * 32)
        self.assertRegex(revision, r"^r1_[0-9a-f]{32}$")
        self.assertNotIn("secret-canary", revision)

    def test_activation_bundle_uses_exact_ledger_and_verified_signature(self):
        plan = plan_set(); policy = policy_set(plan); request = request_set(plan, policy)
        proof = __import__("sandbox.hosting.images.staging_v2", fromlist=["StagedImageProofSet"]).StagedImageProofSet.create(
            request, policy, observation(plan, policy), 1)
        with tempfile.TemporaryDirectory(dir=Path.home()) as temp:
            root = Path(temp); root.chmod(0o700); private = root / "authority"
            env = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
            subprocess.run(("/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "",
                "-f", str(private)), env=env, check=True)
            public = private.with_suffix(".pub"); public.chmod(0o600)
            def runner(argv, **kwargs):
                changed = tuple(str(private) if item == str(public) else item for item in argv)
                return subprocess.run(changed, **kwargs)
            signer = SshAgentRollbackSigner(public, "rollback-authority/controller-a", runner=runner)
            record = {"phase": "succeeded", "request_id": proof.request_id,
                "request_digest": proof.request_digest, "generation": proof.staging_generation,
                "ledger_revision": 7}
            bundle = prepare_activation_bundle(plan=plan, proof=proof, stage_record=record,
                stage_ledger_revision=7, current_generation=0,
                current_generation_digest="sha256:" + "0" * 64,
                target=proof.target.as_mapping(), configuration_digest="sha256:" + "b" * 64,
                snapshot_id="compose-snapshot/test-a", provider_revision="provider-v2",
                snapshot_expires_at=1000, authority_revision="rollback-v2",
                signer=signer, now=100)
        self.assertEqual(bundle["stage_ledger"],
            {"authority": "feature-050-stage-ledger-v2", "revision": 7})
        self.assertNotIn(str(private), json.dumps(bundle))
        record["ledger_revision"] = 8
        with self.assertRaisesRegex(ProvisioningError, "ledger_mismatch"):
            prepare_activation_bundle(plan=plan, proof=proof, stage_record=record,
                stage_ledger_revision=7, current_generation=0,
                current_generation_digest="sha256:" + "0" * 64,
                target=proof.target.as_mapping(), configuration_digest="sha256:" + "b" * 64,
                snapshot_id="compose-snapshot/test-a", provider_revision="provider-v2",
                snapshot_expires_at=1000, authority_revision="rollback-v2",
                signer=signer, now=100)
        record["ledger_revision"] = 7
        with self.assertRaisesRegex(ProvisioningError, "artifact_invalid"):
            prepare_activation_bundle(plan=plan, proof=proof, stage_record=record,
                stage_ledger_revision=7, current_generation=0,
                current_generation_digest="sha256:" + "0" * 64,
                target=proof.target.as_mapping(), configuration_digest="sha256:" + "b" * 64,
                snapshot_id="compose-snapshot/test-a", provider_revision="provider-v2",
                snapshot_expires_at=100, authority_revision="rollback-v2",
                signer=signer, now=100)
        class BadSigner:
            authority_id = "rollback-authority/controller-a"
            public_key = signer.public_key
            def sign(self, _unsigned): return "YmFk"
        with self.assertRaisesRegex(ProvisioningError, "signature_invalid"):
            prepare_activation_bundle(plan=plan, proof=proof, stage_record=record,
                stage_ledger_revision=7, current_generation=0,
                current_generation_digest="sha256:" + "0" * 64,
                target=proof.target.as_mapping(), configuration_digest="sha256:" + "b" * 64,
                snapshot_id="compose-snapshot/test-a", provider_revision="provider-v2",
                snapshot_expires_at=1000, authority_revision="rollback-v2",
                signer=BadSigner(), now=100)


if __name__ == "__main__": unittest.main()
