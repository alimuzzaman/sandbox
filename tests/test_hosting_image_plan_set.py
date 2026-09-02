from __future__ import annotations

from copy import deepcopy
from contextlib import redirect_stdout
import hashlib
from io import StringIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sandbox.hosting.images.models import canonical_digest
from sandbox.hosting.images.plan_set import (
    IMAGE_NAMES, PlanSetContractError, SIGNATURE_MODE, _load_json_bytes,
    validate_verified_image_plan_set, verify_release_bundle,
)


SOURCE_SHA = "dbe62aa3d9871c22755d62af5cb499bedfba5fc8"
WORKFLOW = {
    "issuer": "https://token.actions.githubusercontent.com",
    "identity": "https://github.com/lenzora/lenzora/.github/workflows/prepare-hosted-production-images.yml@refs/heads/main",
    "repository": "lenzora/lenzora", "ref": "refs/heads/main", "sha": SOURCE_SHA,
}
PERSISTENT_SERVICES = (
    "lenzora-agent-auth-cleanup-worker", "lenzora-comparison-batch-worker",
    "lenzora-customer-data-worker", "lenzora-job-dispatcher",
    "lenzora-job-outbox-publisher", "lenzora-job-queue",
    "lenzora-job-readiness-monitor", "lenzora-job-reconciler", "lenzora-job-worker",
    "lenzora-monitor-worker", "lenzora-notification-outbox-worker",
    "lenzora-product-events-worker", "lenzora-snapshot-batch-worker",
    "lenzora-transactional-delivery-worker", "lenzora-web",
    "lenzora-webhook-delivery-worker", "lenzora-website-health-worker",
)
ONE_SHOT_SERVICES = (
    "lenzora-job-queue-topology-gate", "lenzora-migrate", "lenzora-storage-init",
)


class FakeVerifier:
    def __init__(self, fail_at: int | None = None):
        self.calls = []
        self.fail_at = fail_at

    def verify(self, blob, bundle, workflow):
        self.calls.append((hashlib.sha256(blob).hexdigest(),
                           hashlib.sha256(bundle).hexdigest(), workflow.as_mapping()))
        return len(self.calls) != self.fail_at


def bundle_bytes():
    return json.dumps({"mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json",
        "verificationMaterial": {"certificate": "synthetic"},
        "messageSignature": {"messageDigest": "synthetic"}},
        sort_keys=True, separators=(",", ":")).encode()


def payload_bytes(repository, manifest):
    return json.dumps({"critical": {"identity": {"docker-reference": repository},
        "image": {"docker-manifest-digest": manifest},
        "type": "cosign container image signature"}, "optional": {}},
        sort_keys=True, separators=(",", ":")).encode()


def make_bundle(root: Path):
    images = []
    for index, name in enumerate(IMAGE_NAMES, start=1):
        repository = f"ghcr.io/lenzora/lenzora/{name}"
        manifest = "sha256:" + str(index) * 64
        payload = payload_bytes(repository, manifest)
        bundle = bundle_bytes()
        (root / f"{name}.payload.json").write_bytes(payload)
        (root / f"{name}.bundle").write_bytes(bundle)
        images.append({"name": name, "repository": repository,
            "image_ref": f"{repository}@{manifest}", "manifest_digest": manifest,
            "config_digest": "sha256:" + str(index + 3) * 64,
            "platform": "linux/amd64",
            "media_type": "application/vnd.oci.image.manifest.v1+json",
            "signature_payload_digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
            "signature_bundle_digest": "sha256:" + hashlib.sha256(bundle).hexdigest()})
    receipt = {"schema_version": 1, "target": "production", "platform": "linux/amd64",
        "source_sha": SOURCE_SHA, "source_ref": "refs/heads/main", "sentry_sha": SOURCE_SHA,
        "workflow": WORKFLOW, "images": images}
    receipt_bytes = json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()
    (root / "receipt.json").write_bytes(receipt_bytes)
    (root / "receipt.sha256").write_text(
        f"{hashlib.sha256(receipt_bytes).hexdigest()}  receipt.json\n")
    (root / "receipt.bundle").write_bytes(bundle_bytes())
    return "sha256:" + hashlib.sha256(receipt_bytes).hexdigest()


def policy_mapping(receipt_digest):
    value = {"schema_version": 2, "authority_id": "machine-policy/lenzora-production",
        "policy_revision": 1,
        "target_scope": {"remote": "production", "project": "lenzora", "environment": "production"},
        "approved_receipt_digest": receipt_digest, "source_repository": "lenzora/lenzora",
        "source_ref": "refs/heads/main", "source_revision": SOURCE_SHA,
        "platform": "linux/amd64", "workflow": WORKFLOW,
        "persistent_services": list(PERSISTENT_SERVICES),
        "one_shot_services": list(ONE_SHOT_SERVICES),
        "service_image_bindings": [
            {"service": service, "image": (
                "queue" if service == "lenzora-job-queue" else
                "web" if service == "lenzora-web" else "worker")}
            for service in sorted(PERSISTENT_SERVICES + ONE_SHOT_SERVICES)],
        "activation_environment_bindings": [
            {"image": "queue", "environment_variable": "LENZORA_PRODUCTION_QUEUE_IMAGE"},
            {"image": "web", "environment_variable": "LENZORA_PRODUCTION_WEB_IMAGE"},
            {"image": "worker", "environment_variable": "LENZORA_PRODUCTION_WORKER_IMAGE"}],
        "signature_mode": SIGNATURE_MODE}
    value["policy_digest"] = canonical_digest(
        "sandbox.hosting.images.machine-plan-set-policy.v2", value)
    return value


class TestVerifiedImagePlanSet(unittest.TestCase):
    def test_public_verify_command_emits_plan_without_manifest_access(self):
        from sandbox.commands.hosting import _cmd_host_image_verify

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); bundle = root / "bundle"; bundle.mkdir()
            digest = make_bundle(bundle)
            policy_path = root / "policy.json"
            policy_path.write_text(json.dumps(policy_mapping(digest), separators=(",", ":")))
            policy_path.chmod(0o600)
            output = StringIO()
            args = SimpleNamespace(
                machine_plan_set_policy=str(policy_path),
                signed_receipt_directory=str(bundle),
            )
            with patch("sandbox.hosting.images.plan_set.CosignOfflineVerifier",
                       return_value=FakeVerifier()), redirect_stdout(output):
                _cmd_host_image_verify(args)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["plan_set"]["schema_version"], 2)

    def test_public_verify_command_rejects_duplicate_policy_keys(self):
        from sandbox.commands.hosting import _cmd_host_image_verify

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); bundle = root / "bundle"; bundle.mkdir()
            make_bundle(bundle)
            policy_path = root / "policy.json"
            policy_path.write_text('{"schema_version":2,"schema_version":2}')
            policy_path.chmod(0o600)
            output = StringIO()
            args = SimpleNamespace(
                machine_plan_set_policy=str(policy_path),
                signed_receipt_directory=str(bundle),
            )
            with redirect_stdout(output), self.assertRaises(SystemExit):
                _cmd_host_image_verify(args)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "input_invalid")

    def test_all_offline_signatures_produce_closed_plan_set(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); receipt_digest = make_bundle(root); verifier = FakeVerifier()
            plan = verify_release_bundle(policy_mapping(receipt_digest), root, verifier)

        self.assertEqual(len(verifier.calls), 4)
        self.assertEqual([row["name"] for row in plan.as_mapping()["images"]],
                         list(IMAGE_NAMES))
        bindings = {row["service"]: row["image_ref"]
                    for row in plan.as_mapping()["service_image_bindings"]}
        self.assertIn("lenzora-job-queue", bindings)
        self.assertIn("lenzora-web", bindings)
        self.assertEqual(len(bindings), 20)
        self.assertEqual(bindings["lenzora-migrate"],
                         next(row["image_ref"] for row in plan.as_mapping()["images"]
                              if row["name"] == "worker"))
        self.assertEqual(validate_verified_image_plan_set(plan.as_mapping()), plan)

    def test_any_signature_refusal_emits_no_plan(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); digest = make_bundle(root)
            with self.assertRaises(PlanSetContractError) as raised:
                verify_release_bundle(policy_mapping(digest), root, FakeVerifier(fail_at=3))
        self.assertEqual(raised.exception.code, "signature_invalid")

    def test_receipt_or_policy_binding_drift_refuses(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); digest = make_bundle(root)
            policy = policy_mapping(digest)
            policy["service_image_bindings"][0]["image"] = "web"
            with self.assertRaises(PlanSetContractError) as raised:
                verify_release_bundle(policy, root, FakeVerifier())
        self.assertEqual(raised.exception.code, "policy_mismatch")

    def test_plan_set_is_closed_and_digest_bound(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); digest = make_bundle(root)
            raw = verify_release_bundle(policy_mapping(digest), root, FakeVerifier()).as_mapping()
        changed = deepcopy(raw)
        queue_binding = next(row for row in changed["service_image_bindings"]
                             if row["image"] == "queue")
        queue_binding["image_ref"] = next(row["image_ref"] for row in changed["images"]
                                           if row["name"] == "web")
        with self.assertRaises(PlanSetContractError):
            validate_verified_image_plan_set(changed)
        changed = deepcopy(raw); changed["extra"] = True
        with self.assertRaises(PlanSetContractError):
            validate_verified_image_plan_set(changed)

    def test_duplicate_json_keys_and_boolean_revision_refuse(self):
        with self.assertRaises(PlanSetContractError):
            _load_json_bytes(b'{"schema_version":1,"schema_version":1}')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); digest = make_bundle(root); policy = policy_mapping(digest)
            policy["policy_revision"] = True
            policy["policy_digest"] = canonical_digest(
                "sandbox.hosting.images.machine-plan-set-policy.v2",
                {key: value for key, value in policy.items() if key != "policy_digest"})
            with self.assertRaises(PlanSetContractError):
                verify_release_bundle(policy, root, FakeVerifier())

    def test_boolean_receipt_version_refuses(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); digest = make_bundle(root)
            receipt_path = root / "receipt.json"
            receipt = json.loads(receipt_path.read_text())
            receipt["schema_version"] = True
            receipt_bytes = json.dumps(
                receipt, sort_keys=True, separators=(",", ":")).encode()
            receipt_path.write_bytes(receipt_bytes)
            (root / "receipt.sha256").write_text(
                f"{hashlib.sha256(receipt_bytes).hexdigest()}  receipt.json\n")
            policy = policy_mapping(
                "sha256:" + hashlib.sha256(receipt_bytes).hexdigest())
            with self.assertRaises(PlanSetContractError):
                verify_release_bundle(policy, root, FakeVerifier())

    def test_duplicate_service_authority_and_total_bundle_cap_refuse(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); digest = make_bundle(root); policy = policy_mapping(digest)
            policy["service_image_bindings"][1]["service"] = \
                policy["service_image_bindings"][0]["service"]
            policy["policy_digest"] = canonical_digest(
                "sandbox.hosting.images.machine-plan-set-policy.v2",
                {key: value for key, value in policy.items() if key != "policy_digest"})
            with self.assertRaises(PlanSetContractError):
                verify_release_bundle(policy, root, FakeVerifier())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); digest = make_bundle(root)
            with patch("sandbox.hosting.images.plan_set.MAX_V2_BUNDLE_SET_BYTES", 32):
                with self.assertRaises(PlanSetContractError) as raised:
                    verify_release_bundle(policy_mapping(digest), root, FakeVerifier())
            self.assertEqual(raised.exception.code, "input_too_large")


if __name__ == "__main__":
    unittest.main()
