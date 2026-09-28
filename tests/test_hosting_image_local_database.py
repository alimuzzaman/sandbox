"""Four-image local PostgreSQL releases retain the old production contract."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from sandbox.hosting.images.models import canonical_digest
from sandbox.hosting.images.plan_set import (
    PlanSetContractError, VerifiedImagePlanSet, verify_release_bundle,
)
from sandbox.hosting.images.provisioning import prepare_machine_policy
from tests.test_hosting_image_plan_set import (
    FakeVerifier, bundle_bytes, make_bundle, payload_bytes, policy_mapping,
)


def local_database_bundle(root: Path, environment="production"):
    make_bundle(root)
    receipt = json.loads((root / "receipt.json").read_text())
    reference = "ghcr.io/lenzora/lenzora/database"
    manifest = "sha256:" + "7" * 64
    payload = payload_bytes(reference, manifest)
    bundle = bundle_bytes()
    (root / "database.payload.json").write_bytes(payload)
    (root / "database.bundle").write_bytes(bundle)
    database = {"name": "database", "repository": reference,
        "image_ref": reference + "@" + manifest, "manifest_digest": manifest,
        "config_digest": "sha256:" + "8" * 64, "platform": "linux/amd64",
        "media_type": "application/vnd.oci.image.manifest.v1+json",
        "signature_payload_digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
        "signature_bundle_digest": "sha256:" + hashlib.sha256(bundle).hexdigest()}
    receipt.update(schema_version=2, target=environment,
        source_ref="refs/heads/dev" if environment == "development" else "refs/heads/main",
        images=[database, *receipt["images"]])
    receipt["workflow"]["ref"] = receipt["source_ref"]
    receipt["workflow"]["identity"] = receipt["workflow"]["identity"].split("@")[0] + "@" + receipt["source_ref"]
    encoded = json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    (root / "receipt.json").write_bytes(encoded)
    (root / "receipt.sha256").write_text(digest + "  receipt.json\n")
    policy = policy_mapping("sha256:" + digest)
    policy["receipt_schema_version"] = 2
    policy["target_scope"]["environment"] = environment
    policy["source_ref"] = receipt["source_ref"]
    policy["workflow"] = receipt["workflow"]
    policy["persistent_services"] = sorted([*policy["persistent_services"], "lenzora-db"])
    policy["service_image_bindings"] = sorted([
        *policy["service_image_bindings"], {"service": "lenzora-db", "image": "database"}],
        key=lambda row: row["service"])
    policy["activation_environment_bindings"].insert(0,
        {"image": "database", "environment_variable": "LENZORA_DATABASE_IMAGE"})
    policy.pop("policy_digest")
    policy["policy_digest"] = canonical_digest("sandbox.hosting.images.machine-plan-set-policy.v2", policy)
    return policy


class TestLocalDatabaseImageContract(unittest.TestCase):
    def test_both_environments_verify_all_five_signatures_and_roundtrip(self):
        for environment in ("development", "production"):
            with self.subTest(environment=environment), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                policy = local_database_bundle(root, environment)
                verifier = FakeVerifier()
                plan = verify_release_bundle(policy, root, verifier)
                self.assertEqual(len(verifier.calls), 5)
                self.assertEqual([image.name for image in plan.receipt.images],
                                 ["database", "queue", "web", "worker"])
                encoded = plan.as_mapping()
                self.assertEqual(encoded["receipt"]["schema_version"], 2)
                self.assertEqual(encoded["receipt"]["target"], environment)
                self.assertEqual(VerifiedImagePlanSet.from_mapping(encoded), plan)
                self.assertEqual(len(encoded["service_image_bindings"]), 21)

    def test_policy_provisioning_binds_the_receipt_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected = local_database_bundle(root, "development")
            policy = prepare_machine_policy(receipt_bytes=(root / "receipt.json").read_bytes(),
                authority_id=expected["authority_id"], policy_revision=1,
                target_scope=expected["target_scope"],
                persistent_services=tuple(expected["persistent_services"]),
                one_shot_services=tuple(expected["one_shot_services"]),
                service_image_bindings={row["service"]: row["image"] for row in expected["service_image_bindings"]},
                activation_environment_bindings={row["image"]: row["environment_variable"] for row in expected["activation_environment_bindings"]})
            self.assertEqual(policy.as_mapping(), expected)

    def test_wrong_environment_missing_database_or_unsigned_expansion_refuses(self):
        for mutation in ("environment", "database-proof", "policy-contract"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                policy = local_database_bundle(root, "development")
                if mutation == "environment":
                    policy["target_scope"]["environment"] = "production"
                elif mutation == "database-proof":
                    (root / "database.bundle").unlink()
                else:
                    policy.pop("receipt_schema_version")
                policy.pop("policy_digest")
                policy["policy_digest"] = canonical_digest("sandbox.hosting.images.machine-plan-set-policy.v2", policy)
                with self.assertRaises(PlanSetContractError):
                    verify_release_bundle(policy, root, FakeVerifier())

    def test_database_stage_helper_and_proof_roundtrip(self):
        import shutil
        import subprocess
        from sandbox.hosting.images import staging_helper
        from sandbox.hosting.images.staging_models import StagingContractError, staging_digest
        from sandbox.hosting.images.staging_v2 import BatchObservation, StagedImageProofSet
        from sandbox.hosting.images.staging_worker import StageWorkerV2
        from tests.test_hosting_image_staging_v2 import policy_set, request_set

        for environment in ("development", "production"):
            with self.subTest(environment=environment), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                plan = verify_release_bundle(local_database_bundle(root, environment), root, FakeVerifier())
                policy = policy_set(plan)
                request = request_set(plan, policy)
                class Capture:
                    def prepare(self, _remote, frame, *, timeout_seconds):
                        self.frame = frame
                        return object()
                transport = Capture()
                StageWorkerV2(transport).prepare(request, policy)
                frame = transport.frame
                frame["helper"]["artifact_digest"] = "sha256:" + hashlib.sha256(
                    Path(staging_helper.__file__).read_bytes()).hexdigest()
                pulled = []
                def runner(argv, *, environment, input_data=None, timeout=300):
                    command = tuple(argv)
                    output = b""
                    if command[:2] == ("docker", "login"):
                        Path(environment["DOCKER_CONFIG"]).mkdir(parents=True)
                    elif command[:2] == ("docker", "info"):
                        output = b"daemon-a\n"
                    elif command[:2] == ("docker", "pull"):
                        pulled.append(command[2])
                    elif command[:3] == ("docker", "image", "inspect"):
                        image = next(item for item in plan.receipt.images if item.image_ref == command[3])
                        output = json.dumps({"Id": image.config_digest,
                            "RepoDigests": [image.image_ref], "Os": "linux", "Architecture": "amd64"}).encode()
                    else:
                        raise AssertionError(command)
                    return subprocess.CompletedProcess(command, 0, stdout=output, stderr=b"")
                result = staging_helper.execute_v2(frame, b"synthetic-test-credential", run_root=root,
                    runner=runner, anonymous_probe=lambda *_: True,
                    cgroup_identity=lambda unit: "/app.slice/" + unit,
                    machine_epoch_reader=lambda: "raw-machine-a",
                    projected_identity_reader=lambda: "machine-a", remover=shutil.rmtree)
                self.assertEqual(result["code"], "staged")
                self.assertEqual(pulled, [item.image_ref for item in plan.receipt.images])
                observation = BatchObservation.from_mapping(result["payload"]["observation"])
                proof = StagedImageProofSet.create(request, policy, observation, 1)
                self.assertEqual(StagedImageProofSet.from_mapping(proof.as_mapping()), proof)
                downgraded = deepcopy(frame)
                downgraded.pop("receipt_schema_version")
                with self.assertRaises(ValueError):
                    staging_helper._closed_plan_v2(downgraded)
                downgraded = observation.body_mapping()
                downgraded.pop("receipt_schema_version")
                downgraded["observation_digest"] = staging_digest(
                    "sandbox.hosting.images.batch-observation.v2", downgraded)
                with self.assertRaises(StagingContractError):
                    BatchObservation.from_mapping(downgraded)

    def test_legacy_policy_and_plan_bytes_remain_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy = policy_mapping(make_bundle(root))
            before = deepcopy(policy)
            plan = verify_release_bundle(policy, root, FakeVerifier())
            self.assertEqual(plan.policy.as_mapping(), before)
            self.assertNotIn("schema_version", plan.as_mapping()["receipt"])
            self.assertNotIn("target", plan.as_mapping()["receipt"])
            self.assertEqual(VerifiedImagePlanSet.from_mapping(plan.as_mapping()), plan)


if __name__ == "__main__":
    unittest.main()
