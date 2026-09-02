import hashlib
import copy
import unittest

from sandbox.hosting.recovery.models import (
    RecoveryAction, RecoveryRequest, TargetIdentity, canonical_digest,
)
from sandbox.hosting.recovery.policy import classify_observation, validate_job_binding


class HostRecoveryPolicyTests(unittest.TestCase):
    def setUp(self):
        self.request = RecoveryRequest(
            RecoveryAction.OBSERVE_RECONCILE, "recover-1", "a" * 32,
            "apply-1", TargetIdentity("remote", "project", "development"), 0,
        )
        self.evidence = {
            "host_identity": "sha256:" + "1" * 64,
            "machine_identity": "machine-1",
            "runtime_identity": "sha256:" + "2" * 64,
            "source_identity": "sha256:" + "3" * 64,
            "source_revision": "a" * 40,
            "source_branch": "main",
            "source_clean": True,
            "config_digest": "sha256:" + "4" * 64,
            "manifest_digest": "sha256:" + "6" * 64,
            "secret_binding_metadata_id": "sha256:" + "a" * 64,
            "secret_binding_revision": 1,
            "secret_binding_key_version": "v1",
            "topology": ["web"],
            "images": [{"name": "web", "id": "sha256:" + "5" * 64}],
            "config_file_digests": [
                {"name": str(index), "digest": "sha256:" + str(7 + index) * 64}
                for index in range(4)],
            "phase_receipt_digest": "sha256:" + "8" * 64,
            "one_shot_phases": [],
        }
        self.evidence["edge_intent"] = {
            "records": [{"hostname": "example.test", "address": "192.0.2.1",
                         "proxied": False, "mode": "proxy", "target": None}],
            "routes": [{"hostname": "example.test", "mode": "proxy"}],
            "certificate_hostnames": ["example.test"],
            "proxied": False, "healthcheck_path": "/health",
            "basic_auth": {"enabled": False, "username": None},
        }
        self.evidence["edge_intent_digest"] = canonical_digest(
            self.evidence["edge_intent"])
        self.operation = {
            "schema_version": 1, "accepted_before_effects": True,
            "compose_file_count": 1,
            "expected_persistent_services": ["web"],
            "expected_initializer_services": [],
            "expected_one_shot_phases": [],
            "job_id": "a" * 32, "request_id": "apply-1",
            "target": self.request.target.as_dict(), "project_identity": "project-id",
            "starting_generation": 0,
            "project_root_digest": "sha256:" + hashlib.sha256(b"/project").hexdigest(),
            "source": {"clean": True, "identity": "source-id", "commit": "a" * 40},
            "evidence": self.evidence,
        }
        self.operation["digest"] = canonical_digest(self.operation)
        self.job = {"job_id": "a" * 32, "lifecycle": "failed", "submission": {
            "version": 1, "request_id": "apply-1", "project_identity": "project-id",
            "project_root": "/project", "cwd_relative": ".", "source": {"identity": "source-id",
            "commit": "a" * 40, "dirty_digest": None},
            "argv": ["./sb", "host", "apply", "--project-dir", "/project",
                     "--environment", "development", "--remote", "remote", "--confirm"]}}

    def observation(self):
        return {"schema_version": 1, "complete": True, "bounded": True,
                "epoch_start": "epoch", "epoch_end": "epoch",
                **self.evidence,
                "services": [{"service": "web", "state": "ready"}],
                "phases": [{"phase": "runtime", "state": "complete"}]}

    def test_exact_current_contract_is_eligible(self):
        self.assertIsNone(validate_job_binding(self.request, self.job, self.operation))
        refusal, evidence = classify_observation(self.operation, self.observation())
        self.assertIsNone(refusal)
        self.assertTrue(evidence["evidence_id"].startswith("sha256:"))

    def test_legacy_job_refuses_before_observation(self):
        self.job["submission"] = None
        self.assertEqual(validate_job_binding(self.request, self.job, self.operation),
                         "legacy_evidence")

    def test_same_tag_different_image_and_torn_epoch_refuse(self):
        observation = self.observation()
        observation["images"] = [{"name": "web", "id": "sha256:" + "d" * 64}]
        self.assertEqual(classify_observation(self.operation, observation)[0],
                         "mutation_required")
        observation = self.observation()
        observation["epoch_end"] = "restarted"
        self.assertEqual(classify_observation(self.operation, observation)[0],
                         "evidence_changed")

    def test_unrelated_job_source_and_target_bindings_refuse(self):
        cases = []
        unrelated = {**self.job, "submission": {**self.job["submission"],
                     "argv": ["./sb", "status"]}}
        cases.append(unrelated)
        wrong_source = {**self.job, "submission": {**self.job["submission"],
                        "source": {"identity": "other", "commit": "a" * 40,
                                   "dirty_digest": None}}}
        cases.append(wrong_source)
        dirty = {**self.job, "submission": {**self.job["submission"],
                 "source": {"identity": "source-id", "commit": "a" * 40,
                            "dirty_digest": "sha256:dirty"}}}
        cases.append(dirty)
        cases.append({**self.job, "job_id": "b" * 32})
        for job in cases:
            with self.subTest(job=job):
                self.assertEqual(validate_job_binding(self.request, job, self.operation),
                                 "binding_mismatch")

    def test_changed_config_source_host_runtime_and_image_refuse(self):
        for field, value, expected in (
            ("manifest_digest", "sha256:" + "9" * 64, "mutation_required"),
            ("secret_binding_key_version", "v2", "mutation_required"),
            ("source_revision", "b" * 40, "mutation_required"),
            ("source_branch", "other", "mutation_required"),
            ("source_clean", False, "mutation_required"),
            ("host_identity", "sha256:" + "9" * 64, "changed_target"),
            ("machine_identity", "machine-2", "changed_target"),
            ("edge_intent_digest", "sha256:" + "9" * 64, "changed_target"),
            ("runtime_identity", "sha256:" + "9" * 64, "changed_target"),
            ("phase_receipt_digest", "sha256:" + "9" * 64, "mutation_required"),
        ):
            observation = self.observation()
            observation[field] = value
            self.assertEqual(classify_observation(self.operation, observation)[0], expected)

    def test_exact_secret_binding_metadata_and_image_coverage_are_required(self):
        observation = self.observation()
        observation["secret_binding_metadata_id"] = "sha256:" + "9" * 64
        self.assertEqual(classify_observation(self.operation, observation)[0],
                         "mutation_required")
        observation = self.observation()
        observation["images"] = []
        self.assertEqual(classify_observation(self.operation, observation)[0],
                         "mutation_required")
        observation = self.observation()
        observation["images"][0]["id"] = "web:latest"
        self.assertEqual(classify_observation(self.operation, observation)[0],
                         "mutation_required")

    def test_every_compose_and_generated_config_digest_must_match(self):
        observation = self.observation()
        observation["config_file_digests"] = observation["config_file_digests"][:-1]
        self.assertEqual(classify_observation(self.operation, observation)[0],
                         "mutation_required")

    def test_operation_starting_generation_is_authoritative(self):
        self.operation["starting_generation"] = 2
        self.operation["digest"] = canonical_digest({
            key: value for key, value in self.operation.items() if key != "digest"})
        self.assertEqual(validate_job_binding(self.request, self.job, self.operation),
                         "generation_conflict")

    def test_legacy_operation_without_stable_machine_identity_refuses(self):
        self.operation["evidence"].pop("machine_identity")
        self.operation["digest"] = canonical_digest({
            key: value for key, value in self.operation.items() if key != "digest"})
        self.assertEqual(validate_job_binding(self.request, self.job, self.operation),
                         "legacy_evidence")

    def test_one_shot_phase_contract_is_exact_bounded_and_complete(self):
        cases = (
            ({}, [], "partial_evidence"),
            ([{"phase": "init:migrate", "state": "complete", "hostile": "x"}],
             ["init:migrate"], "partial_evidence"),
            ([{"phase": "init:migrate", "state": "complete"},
              {"phase": "init:migrate", "state": "complete"}],
             ["init:migrate"], "partial_evidence"),
            ([], ["init:migrate"], "partial_evidence"),
            ([{"phase": "init:migrate", "state": "pending"}],
             ["init:migrate"], "mutation_required"),
        )
        for phases, expected_phases, refusal in cases:
            with self.subTest(phases=phases):
                operation = copy.deepcopy(self.operation)
                operation["expected_one_shot_phases"] = expected_phases
                operation["expected_initializer_services"] = ["migrate"]
                operation["evidence"]["topology"] = ["web", "migrate"]
                operation["evidence"]["one_shot_phases"] = phases
                operation["digest"] = canonical_digest({
                    key: value for key, value in operation.items() if key != "digest"})
                self.assertEqual(
                    validate_job_binding(self.request, self.job, operation), refusal)
                observation = self.observation()
                observation["topology"] = ["web", "migrate"]
                observation["one_shot_phases"] = phases
                self.assertEqual(classify_observation(operation, observation)[0], refusal)

    def test_manifest_service_and_initializer_projections_are_mandatory(self):
        omitted = copy.deepcopy(self.operation)
        omitted.pop("expected_initializer_services")
        omitted.pop("expected_one_shot_phases")
        omitted["digest"] = canonical_digest({
            key: value for key, value in omitted.items() if key != "digest"})
        self.assertEqual(validate_job_binding(self.request, self.job, omitted),
                         "partial_evidence")

        empty = copy.deepcopy(self.operation)
        empty["expected_persistent_services"] = []
        empty["evidence"]["topology"] = []
        empty["digest"] = canonical_digest({
            key: value for key, value in empty.items() if key != "digest"})
        self.assertEqual(validate_job_binding(self.request, self.job, empty),
                         "partial_evidence")

        observation = self.observation()
        observation["services"] = []
        self.assertEqual(classify_observation(self.operation, observation)[0],
                         "mutation_required")


if __name__ == "__main__":
    unittest.main()
