from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import unittest

from sandbox.hosting.images.activation.models import activation_digest
from sandbox.hosting.images.activation.v2_models import (
    ActivationRequestV2, GenerationBoundEdgeReceiptV2,
    PrivateComposeInputSnapshotV2, RollbackCompatibilityGrantV2,
    validate_activation_generation,
)
from sandbox.hosting.images.activation.v2_service import ActivationServiceV2
from sandbox.hosting.images.plan_set import verify_release_bundle
from sandbox.hosting.images.staging_models import HelperIdentity, StagingTarget, staging_digest
from sandbox.hosting.images.staging_v2 import (
    BatchImageObservation, BatchObservation, StageRequestSet,
    StagedImageProofSet, StagingPolicySet,
)
from tests.test_hosting_image_plan_set import (
    FakeVerifier, make_bundle, payload_bytes, policy_mapping,
)


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
TARGET = {"machine_identity": "machine-a", "target_identity": "target-a",
          "daemon_identity": "daemon-a"}


def artifacts(release_offset=0):
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); receipt_digest = make_bundle(root)
        if release_offset:
            import hashlib
            receipt_path = root / "receipt.json"
            receipt = json.loads(receipt_path.read_text())
            for index, image in enumerate(receipt["images"], start=1):
                digit = f"{index + release_offset:x}"
                manifest = "sha256:" + digit * 64
                payload = payload_bytes(image["repository"], manifest)
                image.update(image_ref=f"{image['repository']}@{manifest}",
                             manifest_digest=manifest,
                             config_digest="sha256:" + f"{index + release_offset + 3:x}" * 64,
                             signature_payload_digest="sha256:" + hashlib.sha256(
                                 payload).hexdigest())
                (root / f"{image['name']}.payload.json").write_bytes(payload)
            raw = json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()
            receipt_path.write_bytes(raw)
            (root / "receipt.sha256").write_text(
                f"{hashlib.sha256(raw).hexdigest()}  receipt.json\n")
            receipt_digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        plan = verify_release_bundle(policy_mapping(receipt_digest), root, FakeVerifier())
    target = StagingTarget(**TARGET)
    helper = HelperIdentity(DIGEST_A, "sandbox-image-stage-helper-v2",
                            "runtime-v2", "systemd-cgroup-v2-batch-stage-v2")
    policy_body = {"schema_version": 2, "plan_set_digest": plan.plan_set_digest,
        "target": target.as_mapping(), "helper": helper.as_mapping(),
        "broker_recipient": f"ghcr-plan-set-read:{plan.plan_set_digest}",
        "broker_binding_id": "binding-a", "broker_binding_version": 1,
        "credential_reference_revision": "credentials-v1",
        "operation": "ghcr.plan-set.read",
        "capability_revision": "systemd-cgroup-v2-batch-stage-v2"}
    stage_policy = StagingPolicySet.from_mapping({**policy_body,
        "policy_digest": staging_digest(
            "sandbox.hosting.images.staging-policy-set.v2", policy_body)})
    stage_request = StageRequestSet.create(
        request_id="stage-v2-a", expected_generation=0, plan_set=plan,
        staging_policy_digest=stage_policy.policy_digest, target=target, confirmed=True)
    observed = tuple(BatchImageObservation(
        item.name, item.repository, item.image_ref, item.config_digest, item.platform,
        item.config_digest, "denied", "succeeded") for item in plan.receipt.images)
    observation_body = {"target_epoch_start": "machine-a", "target_epoch_end": "machine-a",
        "daemon_epoch_start": "daemon-a", "daemon_epoch_end": "daemon-a",
        "target": target.as_mapping(), "images": [item.as_mapping() for item in observed]}
    observation = BatchObservation(
        "machine-a", "machine-a", "daemon-a", "daemon-a", target, observed,
        staging_digest("sandbox.hosting.images.batch-observation.v2", observation_body))
    proof = StagedImageProofSet.create(stage_request, stage_policy, observation, 1)
    snapshot = PrivateComposeInputSnapshotV2.create(
        snapshot_id="compose-snapshot/fixture-a", provider_revision="provider-v2",
        target=TARGET, plan_set_digest=plan.plan_set_digest,
        selected_services=plan.policy.persistent_services,
        configuration_digest=DIGEST_B, expires_at=4_000_000_000)
    return plan, proof, snapshot


def grant_for(plan, proof, *, generation=0, prior_digest=None,
              candidate_plan_digest=None, candidate_proof_digest=None,
              candidate_policy_digest=None):
    prior = prior_digest or activation_digest(
        "sandbox.hosting.images.activation-genesis.v2",
        {"target": TARGET, "generation": generation})
    body = {"schema_version": 2, "authority_id": "rollback-authority/controller-a",
        "authority_revision": "rollback-v2", "target": TARGET,
        "expected_generation": generation, "prior_generation_digest": prior,
        "candidate_plan_set_digest": candidate_plan_digest or plan.plan_set_digest,
        "candidate_proof_set_digest": candidate_proof_digest or proof.proof_digest,
        "policy_digest": candidate_policy_digest or plan.policy.policy_digest, "issued_at": 1,
        "expires_at": 4_000_000_000, "authority_proof": "fixture-proof"}
    return RollbackCompatibilityGrantV2(**body, grant_digest=activation_digest(
        "sandbox.hosting.images.rollback-grant.v2", body))


def request_for(plan, proof, snapshot, grant, *, generation=0,
                operation="activate", request_id="activate-v2-a"):
    return ActivationRequestV2.create(
        request_id=request_id, operation=operation, expected_generation=generation,
        policy_digest=plan.policy.policy_digest, plan_set=plan, proof_set=proof,
        compose_snapshot=snapshot, rollback_grant_digest=grant.grant_digest,
        confirmed=True)


class FakeGrantVerifier:
    def verify(self, grant):
        return grant.authority_proof == "fixture-proof"


class FakeRepositoryV2:
    def __init__(self):
        self.state = {"generation": 0, "current": None, "previous": None}
        self.active = None; self.results = {}; self.events = []; self.commits = 0

    @contextmanager
    def operation_transaction(self, target):
        self.events.append("lock")
        yield self

    def lookup_terminal_v2(self, target, *, request_id, request_digest):
        value = self.results.get(request_id)
        if value is not None and value["request_digest"] != request_digest:
            raise ValueError("request_conflict")
        return value

    def snapshot(self, target): return self.state

    def accept_v2(self, request, **kwargs):
        self.events.append("accept")
        self.active = {"request_digest": request.request_digest,
                       "proof_set_digest": kwargs["proof_set_digest"]}
        return "accepted", {"transaction_digest": DIGEST_A}

    def transition_v2(self, target, request, phase, **values):
        self.events.append(phase)

    def commit_v2(self, target, request, result, generation):
        self.events.append("commit"); self.commits += 1
        self.state["previous"] = self.state["current"]
        self.state["current"] = generation
        self.state["generation"] += 1
        self.results[request.request_id] = result

    def fence_v2(self, target, request, result, **kwargs):
        self.events.append("fence"); self.results[request.request_id] = result


class FakeTargetMutationPort:
    @contextmanager
    def target_mutation_transaction(self, target): yield self


class FakeHostStatePort:
    def __init__(self):
        from sandbox.hosting.images.activation.repository import empty_activation_state
        self.state = empty_activation_state()
    @contextmanager
    def atomic_host_state_transaction(self, target): yield self
    def read_activation_nested(self, target): return json.loads(json.dumps(self.state))
    def activation_acceptance_receipt(self, target, **kwargs):
        return "host-acceptance/" + "b" * 64
    def lookup_activation_acceptance(self, target, **kwargs):
        from types import SimpleNamespace
        active = self.state.get("active")
        accepted = type(active) is dict and active.get("request_digest") == kwargs["request_digest"]
        return SimpleNamespace(state="accepted" if accepted else "absent")
    def absent_activation_evidence(self, target, **kwargs):
        from types import SimpleNamespace
        return SimpleNamespace(state="absent")
    def compare_and_commit_activation(self, target, *, candidate, **kwargs):
        from sandbox.hosting.images.activation.repository import decode_activation_state
        self.state = decode_activation_state(candidate)
        return object()
    def update_activation_nested(self, target, expected_generation, update):
        from sandbox.hosting.images.activation.repository import decode_activation_state
        self.state = decode_activation_state(update(self.state)); return self.state
    def durable_terminal_authority_evidence(self, lease, **kwargs): return object()
    def store_activation_recovery_provisional(self, target, expected_generation, provisional):
        from sandbox.hosting.images.activation.repository import decode_activation_state
        self.state["recovery_provisional"] = provisional
        self.state = decode_activation_state(self.state)


class FakeCustody:
    def __init__(self):
        self.lease = None; self.released = 0; self.reject_proof_digest = None
    def validate_retained_proof(self, **kwargs):
        if kwargs["proof_digest"] == self.reject_proof_digest:
            raise ValueError("proof is not retained")
        return kwargs["supplied_proof"]
    def prepare(self, **kwargs):
        from types import SimpleNamespace
        self.lease = SimpleNamespace(
            lease_id=kwargs["lease_id"], holder=kwargs["holder"],
            proof_digest=kwargs["proof_digest"], target_identity="target-a",
            acceptance_receipt="host-acceptance/" + "b" * 64,
            phase="prepared", expired=False)
        return self.lease
    def promote(self, lease, evidence): return lease
    def lookup(self, lease_id): return self.lease if self.lease and self.lease.lease_id == lease_id else None
    def release(self, lease, evidence): self.released += 1
    def cancel(self, lease, evidence): return lease


class FakeStageRepositoryPort:
    def __init__(self): self.custody = FakeCustody()
    @contextmanager
    def proof_custody_transaction(self, *args, **kwargs): yield self.custody


class FakeRuntimeV2:
    class Crash(BaseException): pass
    def __init__(self, proof=None):
        self.replacements = []; self.rendered = None; self.running_mutation = None
        self.crash_during_replace = False
        self.proof = proof

    def observe_local_image(self, *, target, repository_digest):
        proof = self.proof or artifacts()[1]
        image = next(row for row in proof.observation.images if row.repo_digest == repository_digest)
        return {"repository": image.repository.split("/", 1)[1], "repo_digest": image.repo_digest,
            "config_digest": image.config_digest, "local_image_id": image.local_image_id,
            "platform": {"os": "linux", "architecture": "amd64"},
            "target_epoch_start": "machine-a", "target_epoch_end": "machine-a",
            "target_identity_start": "target-a", "target_identity_end": "target-a",
            "daemon_epoch_start": "daemon-a", "daemon_epoch_end": "daemon-a"}

    def render_topology_v2(self, **kwargs):
        services = {name: {"image": image, "build": None, "pull_policy": "never",
            "platform": {"os": "linux", "architecture": "amd64"}, "dependencies": [],
            "topology_identity": kwargs["topology_digest"], "compose_config_hash": DIGEST_A,
            "configuration_digest": DIGEST_A}
            for name, image in kwargs["service_image_bindings"].items()}
        self.rendered = {"services": services, "orphans": [], "runtime_epoch": "daemon-a",
                         "configuration_digest": DIGEST_B}
        return self.rendered

    def replace_services_v2(self, **kwargs):
        self.replacements.append(kwargs)
        if self.crash_during_replace:
            raise self.Crash()

    def observe_running(self, *, target, services, compose_project):
        images = self.rendered["services"]
        proof = self.proof or artifacts()[1]
        configs = {item.repo_digest: item.config_digest for item in proof.observation.images}
        rows = [{"service": service, "compose_project": compose_project,
            "runtime_identity": f"container-{service}", "declared_image": images[service]["image"],
            "repository_digest": images[service]["image"],
            "local_image_id": configs[images[service]["image"]],
            "config_digest": configs[images[service]["image"]],
            "platform": {"os": "linux", "architecture": "amd64"},
            "topology_identity": images[service]["topology_identity"],
            "compose_config_hash": DIGEST_A, "healthy": True} for service in services]
        if self.running_mutation: self.running_mutation(rows)
        return {"target_epoch_start": "machine-a", "target_epoch_end": "machine-a",
            "target_identity_start": "target-a", "target_identity_end": "target-a",
            "runtime_epoch_start": "daemon-a", "runtime_epoch_end": "daemon-a",
            "services": rows}

    def observe_running_v2(self, **kwargs):
        return self.observe_running(target=kwargs["target"], services=kwargs["services"],
                                    compose_project=kwargs["compose_project"])


class FakeEdgeV2:
    class Crash(BaseException): pass
    def __init__(self, invalid=False, stale=False, crash_at=None):
        self.invalid = invalid; self.stale = stale; self.crash_at = crash_at
        self.calls = 0; self.receipt = None
    def apply_generation_v2(self, **values):
        self.calls += 1
        if self.crash_at == "before_terminal": raise self.Crash()
        body = {"schema_version": 2, **values, "terminal": True}
        receipt = activation_digest(
            "sandbox.hosting.images.generation-bound-edge-receipt.v2", body)
        if self.invalid: receipt = DIGEST_A
        self.receipt = {**body, "receipt_digest": receipt}
        return self.receipt
    def observe_generation_v2(self, **values):
        if self.crash_at == "after_terminal": raise self.Crash()
        if self.stale:
            return {**self.receipt, "generation": self.receipt["generation"] + 1}
        return dict(self.receipt)


def execute(repo, runtime, edge, request, grant):
    service = ActivationServiceV2(repository=repo, runtime_adapter=runtime,
        edge_adapter=edge, rollback_grant_verifier=FakeGrantVerifier(), clock=lambda: 100)
    return service.execute(request, rollback_grant=grant,
        compose_files=("compose.yml",), compose_project="lenzora",
        edge_route_digest=DIGEST_A, admission_deadline="2999-01-01T00:00:00Z",
        stage_ledger_authority="feature-050-stage-ledger-v2", stage_ledger_revision=1)


class ActivationV2Tests(unittest.TestCase):
    def test_all_images_are_proven_then_one_exact_atomic_compose_effect_commits(self):
        plan, proof, snapshot = artifacts(); grant = grant_for(plan, proof)
        request = request_for(plan, proof, snapshot, grant)
        repo = FakeRepositoryV2(); runtime = FakeRuntimeV2(); edge = FakeEdgeV2()
        result = execute(repo, runtime, edge, request, grant)
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(runtime.replacements), 1)
        replacement = runtime.replacements[0]
        self.assertEqual(set(replacement["service_image_bindings"]),
                         set(plan.policy.persistent_services))
        self.assertEqual(set(replacement["environment_bindings"]), {
            "LENZORA_PRODUCTION_QUEUE_IMAGE", "LENZORA_PRODUCTION_WEB_IMAGE",
            "LENZORA_PRODUCTION_WORKER_IMAGE"})
        self.assertEqual(repo.commits, 1); self.assertEqual(edge.calls, 1)
        generation = repo.state["current"]
        self.assertEqual(generation["schema_version"], 2)
        self.assertEqual(len(generation["images"]), 3)
        self.assertEqual(len(generation["service_image_bindings"]),
                         len(plan.policy.persistent_services))

    def test_any_running_service_identity_mismatch_fences_without_commit(self):
        plan, proof, snapshot = artifacts(); grant = grant_for(plan, proof)
        request = request_for(plan, proof, snapshot, grant)
        repo = FakeRepositoryV2(); runtime = FakeRuntimeV2()
        runtime.running_mutation = lambda rows: rows[0].update(
            declared_image="ghcr.io/lenzora/lenzora/web@sha256:" + "f" * 64)
        result = execute(repo, runtime, FakeEdgeV2(), request, grant)
        self.assertFalse(result["ok"]); self.assertEqual(result["result_class"], "uncertain")
        self.assertEqual(repo.commits, 0); self.assertEqual(len(runtime.replacements), 1)

    def test_generation_bound_edge_receipt_is_required_after_effect(self):
        plan, proof, snapshot = artifacts(); grant = grant_for(plan, proof)
        request = request_for(plan, proof, snapshot, grant)
        repo = FakeRepositoryV2(); runtime = FakeRuntimeV2()
        result = execute(repo, runtime, FakeEdgeV2(invalid=True), request, grant)
        self.assertEqual(result["result_class"], "uncertain")
        self.assertEqual(result["code"], "edge_incomplete"); self.assertEqual(repo.commits, 0)

    def test_stale_independent_edge_observation_fences_after_effect(self):
        plan, proof, snapshot = artifacts(); grant = grant_for(plan, proof)
        request = request_for(plan, proof, snapshot, grant)
        repo = FakeRepositoryV2(); result = execute(
            repo, FakeRuntimeV2(), FakeEdgeV2(stale=True), request, grant)
        self.assertEqual(result["result_class"], "uncertain")
        self.assertIn(result["code"], {"edge_incomplete", "edge_uncertain"})
        self.assertEqual(repo.commits, 0)

    def test_expired_private_snapshot_refuses_before_effect(self):
        plan, proof, _ = artifacts()
        snapshot = PrivateComposeInputSnapshotV2.create(
            snapshot_id="compose-snapshot/expired", provider_revision="provider-v2",
            target=TARGET, plan_set_digest=plan.plan_set_digest,
            selected_services=plan.policy.persistent_services,
            configuration_digest=DIGEST_B, expires_at=50)
        grant = grant_for(plan, proof); request = request_for(plan, proof, snapshot, grant)
        repo = FakeRepositoryV2(); runtime = FakeRuntimeV2()
        result = execute(repo, runtime, FakeEdgeV2(), request, grant)
        self.assertEqual(result["result_class"], "refused")
        self.assertEqual(runtime.replacements, []); self.assertEqual(repo.commits, 0)

    def test_rollback_reads_retained_v2_generation_and_uses_one_effect(self):
        plan, proof, snapshot = artifacts(); repo = FakeRepositoryV2()
        first_grant = grant_for(plan, proof)
        first = request_for(plan, proof, snapshot, first_grant)
        self.assertTrue(execute(repo, FakeRuntimeV2(), FakeEdgeV2(), first, first_grant)["ok"])
        current_digest = repo.state["current"]["generation_digest"]
        second_grant = grant_for(plan, proof, generation=1, prior_digest=current_digest)
        second = request_for(plan, proof, snapshot, second_grant, generation=1,
                             request_id="activate-v2-b")
        self.assertTrue(execute(repo, FakeRuntimeV2(), FakeEdgeV2(), second, second_grant)["ok"])
        rollback_target = repo.state["previous"]["generation_digest"]
        retained = repo.state["previous"]
        rollback_grant = grant_for(
            plan, proof, generation=2, prior_digest=rollback_target,
            candidate_plan_digest=retained["plan_set_digest"],
            candidate_proof_digest=retained["proof_set_digest"],
            candidate_policy_digest=retained["policy_digest"])
        rollback = request_for(plan, proof, snapshot, rollback_grant, generation=2,
                               operation="rollback", request_id="rollback-v2-a")
        runtime = FakeRuntimeV2()
        result = execute(repo, runtime, FakeEdgeV2(), rollback, rollback_grant)
        self.assertTrue(result["ok"], result); self.assertEqual(len(runtime.replacements), 1)
        self.assertEqual(repo.state["current"]["rollback_from_generation_digest"], rollback_target)

    def test_rollback_custody_uses_selected_previous_release_not_current_proof(self):
        plan_a, proof_a, snapshot_a = artifacts()
        plan_b, proof_b, snapshot_b = artifacts(release_offset=4)
        self.assertNotEqual(plan_a.plan_set_digest, plan_b.plan_set_digest)
        self.assertNotEqual(proof_a.proof_digest, proof_b.proof_digest)
        host = FakeHostStatePort(); stage = FakeStageRepositoryPort()
        from sandbox.hosting.images.activation.repository import ActivationRepository
        repository = ActivationRepository(host_state_port=host,
            stage_repository=stage, target_mutation_port=FakeTargetMutationPort())
        first_grant = grant_for(plan_a, proof_a)
        first = request_for(plan_a, proof_a, snapshot_a, first_grant)
        first_result = ActivationServiceV2(repository=repository,
            runtime_adapter=FakeRuntimeV2(proof_a), edge_adapter=FakeEdgeV2(),
            rollback_grant_verifier=FakeGrantVerifier(), clock=lambda: 100).execute(
                first, rollback_grant=first_grant, compose_files=("compose.yml",),
                compose_project="lenzora", edge_route_digest=DIGEST_A,
                admission_deadline="2999-01-01T00:00:00Z",
                stage_ledger_authority="feature-050-stage-ledger-v2",
                stage_ledger_revision=1)
        self.assertTrue(first_result["ok"], first_result)
        second_grant = grant_for(
            plan_b, proof_b, generation=1,
            prior_digest=host.state["current"]["generation_digest"])
        second = request_for(plan_b, proof_b, snapshot_b, second_grant,
                             generation=1, request_id="activate-release-b")
        second_result = ActivationServiceV2(repository=repository,
            runtime_adapter=FakeRuntimeV2(proof_b), edge_adapter=FakeEdgeV2(),
            rollback_grant_verifier=FakeGrantVerifier(), clock=lambda: 100).execute(
                second, rollback_grant=second_grant, compose_files=("compose.yml",),
                compose_project="lenzora", edge_route_digest=DIGEST_A,
                admission_deadline="2999-01-01T00:00:00Z",
                stage_ledger_authority="feature-050-stage-ledger-v2",
                stage_ledger_revision=1)
        self.assertTrue(second_result["ok"], second_result)
        previous = host.state["previous"]
        self.assertEqual(previous["proof_set_digest"], proof_a.proof_digest)

        rollback_grant = grant_for(
            plan_a, proof_a, generation=2,
            prior_digest=previous["generation_digest"],
            candidate_plan_digest=previous["plan_set_digest"],
            candidate_proof_digest=previous["proof_set_digest"],
            candidate_policy_digest=previous["policy_digest"])
        # Old behavior admitted this current-release proof while restoring the
        # selected previous release.  It must now fail before custody/effect.
        wrong = request_for(plan_b, proof_b, snapshot_b, rollback_grant,
            generation=2, operation="rollback", request_id="rollback-wrong-proof")
        wrong_runtime = FakeRuntimeV2(proof_b)
        refused = ActivationServiceV2(repository=repository,
            runtime_adapter=wrong_runtime, edge_adapter=FakeEdgeV2(),
            rollback_grant_verifier=FakeGrantVerifier(), clock=lambda: 100).execute(
                wrong, rollback_grant=rollback_grant, compose_files=("compose.yml",),
                compose_project="lenzora", edge_route_digest=DIGEST_A,
                admission_deadline="2999-01-01T00:00:00Z",
                stage_ledger_authority="feature-050-stage-ledger-v2",
                stage_ledger_revision=1)
        self.assertEqual(refused["code"], "rollback_grant_mismatch")
        self.assertEqual(wrong_runtime.replacements, [])
        self.assertEqual(host.state["generation"], 2)

        stage.custody.reject_proof_digest = proof_a.proof_digest
        missing = request_for(plan_a, proof_a, snapshot_a, rollback_grant,
            generation=2, operation="rollback", request_id="rollback-missing-custody")
        missing_runtime = FakeRuntimeV2(proof_a)
        no_custody = ActivationServiceV2(repository=repository,
            runtime_adapter=missing_runtime, edge_adapter=FakeEdgeV2(),
            rollback_grant_verifier=FakeGrantVerifier(), clock=lambda: 100).execute(
                missing, rollback_grant=rollback_grant, compose_files=("compose.yml",),
                compose_project="lenzora", edge_route_digest=DIGEST_A,
                admission_deadline="2999-01-01T00:00:00Z",
                stage_ledger_authority="feature-050-stage-ledger-v2",
                stage_ledger_revision=1)
        self.assertFalse(no_custody["ok"])
        self.assertEqual(missing_runtime.replacements, [])
        self.assertEqual(host.state["generation"], 2)
        stage.custody.reject_proof_digest = None

        correct = request_for(plan_a, proof_a, snapshot_a, rollback_grant,
            generation=2, operation="rollback", request_id="rollback-correct-proof")
        accepted = ActivationServiceV2(repository=repository,
            runtime_adapter=FakeRuntimeV2(proof_a), edge_adapter=FakeEdgeV2(),
            rollback_grant_verifier=FakeGrantVerifier(), clock=lambda: 100).execute(
                correct, rollback_grant=rollback_grant, compose_files=("compose.yml",),
                compose_project="lenzora", edge_route_digest=DIGEST_A,
                admission_deadline="2999-01-01T00:00:00Z",
                stage_ledger_authority="feature-050-stage-ledger-v2",
                stage_ledger_revision=1)
        self.assertTrue(accepted["ok"], accepted)
        self.assertEqual(host.state["current"]["proof_set_digest"], proof_a.proof_digest)

    def test_v2_rollback_refuses_retained_v1_kind_before_effect(self):
        from tests.test_hosting_image_activation_service import prepared_rollback
        v1_repository, *_ = prepared_rollback()
        plan, proof, snapshot = artifacts()
        repo = FakeRepositoryV2(); repo.state = {
            "generation": 2, "current": v1_repository.state["current"],
            "previous": v1_repository.state["previous"]}
        grant = grant_for(plan, proof, generation=2, prior_digest=DIGEST_A)
        request = request_for(plan, proof, snapshot, grant, generation=2,
            operation="rollback", request_id="rollback-mixed-kind")
        runtime = FakeRuntimeV2()
        result = execute(repo, runtime, FakeEdgeV2(), request, grant)
        self.assertEqual(result["code"], "rollback_unavailable")
        self.assertEqual(runtime.replacements, [])

    def test_private_snapshot_contract_cannot_contain_secret_material(self):
        _, _, snapshot = artifacts(); raw = snapshot.as_mapping(); raw["secrets"] = {"DB": "value"}
        with self.assertRaises(ValueError):
            PrivateComposeInputSnapshotV2.from_mapping(raw)

    def test_request_and_generation_dispatch_refuse_unknown_or_caller_chosen_kind(self):
        plan, proof, snapshot = artifacts(); grant = grant_for(plan, proof)
        request = request_for(plan, proof, snapshot, grant)
        self.assertEqual(ActivationRequestV2.from_mapping(request.as_mapping()), request)
        raw = request.as_mapping(); raw["generation_kind"] = "v1"
        with self.assertRaises(ValueError): ActivationRequestV2.from_mapping(raw)
        raw = request.as_mapping(); raw["schema_version"] = True
        with self.assertRaises(ValueError): ActivationRequestV2.from_mapping(raw)
        with self.assertRaises(ValueError):
            validate_activation_generation({"schema_version": 3})

    def test_persisted_v2_generation_strictly_decodes_without_v1_conversion(self):
        from sandbox.hosting.images.activation.repository import (
            decode_activation_state, empty_activation_state,
        )
        plan, proof, snapshot = artifacts(); grant = grant_for(plan, proof)
        request = request_for(plan, proof, snapshot, grant)
        repo = FakeRepositoryV2()
        self.assertTrue(execute(repo, FakeRuntimeV2(), FakeEdgeV2(), request, grant)["ok"])
        state = empty_activation_state(); state["generation"] = 1
        state["current"] = repo.state["current"]
        decoded = decode_activation_state(state)
        self.assertEqual(validate_activation_generation(decoded["current"]).schema_version, 2)
        changed = json.loads(json.dumps(state)); changed["current"]["schema_version"] = 3
        from sandbox.hosting.images.activation.repository import ActivationRepositoryError
        with self.assertRaises(ActivationRepositoryError): decode_activation_state(changed)

    def test_shared_state_accepts_only_explicit_v2_active_transaction(self):
        from sandbox.hosting.images.activation.repository import (
            ActivationRepositoryError, decode_activation_state, empty_activation_state,
        )
        from sandbox.hosting.images.activation.v2_repository import accept_candidate_v2
        plan, proof, snapshot = artifacts(); grant = grant_for(plan, proof)
        request = request_for(plan, proof, snapshot, grant)
        pin = {"lease_id": "activation-lease/" + "a" * 48,
            "holder": "activation-owner/activate-v2-a", "phase": "accepted",
            "proof_digest": proof.proof_digest,
            "host_acceptance_receipt": "host-acceptance/" + "b" * 64}
        status, state, _ = accept_candidate_v2(
            empty_activation_state(), request, holder=pin["holder"], proof_pin=pin,
            recovery_context={"target": TARGET, "compose_project": "lenzora",
                              "selected_services": list(plan.policy.persistent_services)},
            prior_generation_digest=grant.prior_generation_digest)
        self.assertEqual(status, "accepted")
        self.assertEqual(decode_activation_state(state)["active"]["schema_version"], 2)
        changed = json.loads(json.dumps(state)); changed["active"]["schema_version"] = True
        with self.assertRaises(ActivationRepositoryError): decode_activation_state(changed)

    def test_v2_persistence_codec_refuses_unknown_codes_ids_pins_and_context_drift(self):
        from copy import deepcopy
        from sandbox.hosting.images.activation.repository import (
            ActivationRepositoryError, decode_activation_state, empty_activation_state,
        )
        from sandbox.hosting.images.activation.v2_repository import (
            accept_candidate_v2, validate_result_v2,
        )
        base_result = {"schema_version": 2, "ok": False, "result_class": "refused",
            "code": "artifact_invalid", "request_id": "request-a",
            "request_digest": DIGEST_A, "transaction_digest": DIGEST_B,
            "starting_generation": 0, "resulting_generation": 0,
            "generation_digest": None}
        self.assertEqual(validate_result_v2(base_result), base_result)
        for key, value in (("code", "invented"), ("request_id", "x" * 600),
                           ("request_digest", "sha256:bad"),
                           ("starting_generation", True)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_result_v2({**base_result, key: value})
        plan, proof, snapshot = artifacts(); grant = grant_for(plan, proof)
        request = request_for(plan, proof, snapshot, grant)
        pin = {"lease_id": "activation-lease/" + "a" * 48,
            "holder": "activation-owner/activate-v2-a", "phase": "accepted",
            "proof_digest": proof.proof_digest,
            "host_acceptance_receipt": "host-acceptance/" + "b" * 64}
        _, state, _ = accept_candidate_v2(
            empty_activation_state(), request, holder=pin["holder"], proof_pin=pin,
            recovery_context={"target": TARGET, "compose_project": "lenzora",
                "selected_services": list(plan.policy.persistent_services)},
            prior_generation_digest=grant.prior_generation_digest)
        mutations = (
            lambda raw: raw["active"].update(holder="activation-owner/other"),
            lambda raw: raw["active"]["proof_pin"].update(
                host_acceptance_receipt="acceptance/not-canonical"),
            lambda raw: raw["active"]["recovery_context"]["target"].update(extra="bad"),
            lambda raw: raw["active"]["recovery_context"].update(selected_services=["dup", "dup"]),
            lambda raw: raw["active"].update(transaction_digest=DIGEST_B),
        )
        for mutate in mutations:
            changed = deepcopy(state); mutate(changed)
            with self.subTest(mutate=mutate), self.assertRaises(ActivationRepositoryError):
                decode_activation_state(changed)

    def test_real_repository_coordinates_shared_generation_and_whole_proof_custody(self):
        from sandbox.hosting.images.activation.repository import (
            ActivationRepository, decode_activation_state,
        )
        plan, proof, snapshot = artifacts(); grant = grant_for(plan, proof)
        request = request_for(plan, proof, snapshot, grant)
        host = FakeHostStatePort(); stage = FakeStageRepositoryPort()
        repository = ActivationRepository(
            host_state_port=host, stage_repository=stage,
            target_mutation_port=FakeTargetMutationPort())
        service = ActivationServiceV2(
            repository=repository, runtime_adapter=FakeRuntimeV2(),
            edge_adapter=FakeEdgeV2(), rollback_grant_verifier=FakeGrantVerifier(),
            clock=lambda: 100)
        result = service.execute(
            request, rollback_grant=grant, compose_files=("compose.yml",),
            compose_project="lenzora", edge_route_digest=DIGEST_A,
            admission_deadline="2999-01-01T00:00:00Z",
            stage_ledger_authority="feature-050-stage-ledger-v2",
            stage_ledger_revision=1)
        self.assertTrue(result["ok"], result)
        self.assertEqual(host.state["generation"], 1)
        self.assertEqual(host.state["current"]["schema_version"], 2)
        self.assertEqual(stage.custody.released, 1)

    def test_v2_recovery_closes_accepted_no_effect_without_v1_result_conversion(self):
        from sandbox.hosting.images.activation.repository import (
            ActivationRepository, decode_activation_state,
        )
        from sandbox.hosting.recovery.models import ActivationRecoveryObservation
        plan, proof, snapshot = artifacts(); grant = grant_for(plan, proof)
        request = request_for(plan, proof, snapshot, grant)
        host = FakeHostStatePort(); stage = FakeStageRepositoryPort()
        repository = ActivationRepository(host_state_port=host,
            stage_repository=stage, target_mutation_port=FakeTargetMutationPort())
        status, _ = repository.accept_v2(
            request, proof_set_digest=proof.proof_digest,
            recovery_context={"target": TARGET, "compose_project": "lenzora",
                "selected_services": list(plan.policy.persistent_services)},
            prior_generation_digest=grant.prior_generation_digest,
            admission_deadline="2999-01-01T00:00:00Z",
            stage_ledger_authority="feature-050-stage-ledger-v2", stage_ledger_revision=1)
        self.assertEqual(status, "accepted")
        transaction = host.state["active"]["transaction_digest"]
        body = {"transaction_digest": transaction, "expected_generation": 0,
            "classification": "exact_prior", "target_epoch_start": "machine-a",
            "target_epoch_end": "machine-a", "target_identity_start": "target-a",
            "target_identity_end": "target-a", "runtime_epoch_start": "daemon-a",
            "runtime_epoch_end": "daemon-a"}
        observation = ActivationRecoveryObservation(**body,
            evidence_identity=activation_digest("fixture.recovery.v2", body))
        outcome = repository.recover_v2(
            "target-a", request_id="recover-v2-a", request_digest=DIGEST_A,
            expected_generation=0, observer=lambda: observation)
        self.assertEqual(outcome["code"], "recovery_no_effect")
        self.assertEqual(outcome["schema_version"], 2)
        self.assertIsNone(host.state["active"])
        terminal = host.state["results"][request.request_id]["result"]
        self.assertEqual(terminal["schema_version"], 2)
        self.assertFalse(terminal["ok"])
        replay = repository.recover_v2(
            "target-a", request_id="recover-v2-a", request_digest=DIGEST_A,
            expected_generation=0,
            observer=lambda: self.fail("v2 terminal replay must not observe or decode v1"))
        self.assertEqual(replay, outcome)
        self.assertEqual(replay["schema_version"], 2)

        # The shared codec may retain a genuine v1 recovery result, but the v2
        # entry point never accepts it as its terminal replay.
        from copy import deepcopy
        from sandbox.hosting.images.activation.repository import ActivationRepositoryError
        host.state = deepcopy(host.state)
        host.state["recovery_results"]["recover-v2-a"]["schema_version"] = 1
        host.state = decode_activation_state(host.state)
        with self.assertRaises(ActivationRepositoryError):
            repository.recover_v2(
                "target-a", request_id="recover-v2-a", request_digest=DIGEST_A,
                expected_generation=0,
                observer=lambda: self.fail("v1 recovery result must not enter v2 replay"))

    def test_crash_before_edge_terminal_is_durable_and_never_repeats_effect(self):
        from copy import deepcopy
        from sandbox.hosting.images.activation.repository import ActivationRepository
        from sandbox.hosting.images.activation.repository import (
            ActivationRepositoryError, decode_activation_state,
        )
        plan, proof, snapshot = artifacts(); grant = grant_for(plan, proof)
        request = request_for(plan, proof, snapshot, grant)
        host = FakeHostStatePort(); stage = FakeStageRepositoryPort()
        repository = ActivationRepository(host_state_port=host,
            stage_repository=stage, target_mutation_port=FakeTargetMutationPort())
        edge = FakeEdgeV2(crash_at="before_terminal")
        service = ActivationServiceV2(repository=repository,
            runtime_adapter=FakeRuntimeV2(), edge_adapter=edge,
            rollback_grant_verifier=FakeGrantVerifier(), clock=lambda: 100)
        kwargs = {"rollback_grant": grant, "compose_files": ("compose.yml",),
            "compose_project": "lenzora", "edge_route_digest": DIGEST_A,
            "admission_deadline": "2999-01-01T00:00:00Z",
            "stage_ledger_authority": "feature-050-stage-ledger-v2",
            "stage_ledger_revision": 1}
        with self.assertRaises(FakeEdgeV2.Crash): service.execute(request, **kwargs)
        active = host.state["active"]
        self.assertEqual(active["phase"], "edge_pending")
        self.assertFalse(active["edge_result"]["terminal"])
        self.assertIsNone(active["candidate_generation"])
        changed = deepcopy(host.state)
        changed["active"]["edge_result"]["receipt_digest"] = DIGEST_A
        with self.assertRaises(ActivationRepositoryError):
            decode_activation_state(changed)
        changed = deepcopy(host.state)
        changed["active"]["running_observation"]["services"][0]["extra"] = "bad"
        with self.assertRaises(ActivationRepositoryError):
            decode_activation_state(changed)
        replay = service.execute(request, **kwargs)
        self.assertEqual(replay["result_class"], "uncertain")
        self.assertEqual(edge.calls, 1)

    def test_crash_during_replace_retains_closed_intent_for_new_or_prior_recovery(self):
        from copy import deepcopy
        from sandbox.hosting.images.activation.repository import (
            ActivationRepository, ActivationRepositoryError, decode_activation_state,
        )
        from sandbox.hosting.images.activation.v2_models import ReplacementIntentV2
        from sandbox.hosting.images.activation.v2_repository import (
            activation_recovery_intent_v2, activation_recovery_projection,
        )
        from sandbox.hosting.recovery.policy import classify_activation_transition
        plan, proof, snapshot = artifacts(); host = FakeHostStatePort()
        stage = FakeStageRepositoryPort()
        repository = ActivationRepository(host_state_port=host,
            stage_repository=stage, target_mutation_port=FakeTargetMutationPort())
        first_grant = grant_for(plan, proof)
        first = request_for(plan, proof, snapshot, first_grant)
        self.assertTrue(ActivationServiceV2(repository=repository,
            runtime_adapter=FakeRuntimeV2(), edge_adapter=FakeEdgeV2(),
            rollback_grant_verifier=FakeGrantVerifier(), clock=lambda: 100).execute(
                first, rollback_grant=first_grant, compose_files=("compose.yml",),
                compose_project="lenzora", edge_route_digest=DIGEST_A,
                admission_deadline="2999-01-01T00:00:00Z",
                stage_ledger_authority="feature-050-stage-ledger-v2",
                stage_ledger_revision=1)["ok"])
        prior = deepcopy(host.state["current"])
        second_grant = grant_for(plan, proof, generation=1,
                                 prior_digest=prior["generation_digest"])
        second = request_for(plan, proof, snapshot, second_grant, generation=1,
                             request_id="replace-crash-v2")
        runtime = FakeRuntimeV2(); runtime.crash_during_replace = True
        runtime.running_mutation = lambda rows: [row.update(
            runtime_identity="new-" + row["runtime_identity"]) for row in rows]
        service = ActivationServiceV2(repository=repository, runtime_adapter=runtime,
            edge_adapter=FakeEdgeV2(), rollback_grant_verifier=FakeGrantVerifier(),
            clock=lambda: 100)
        kwargs = {"rollback_grant": second_grant, "compose_files": ("compose.yml",),
            "compose_project": "lenzora", "edge_route_digest": DIGEST_A,
            "admission_deadline": "2999-01-01T00:00:00Z",
            "stage_ledger_authority": "feature-050-stage-ledger-v2",
            "stage_ledger_revision": 1}
        with self.assertRaises(FakeRuntimeV2.Crash):
            service.execute(second, **kwargs)
        active = host.state["active"]
        self.assertEqual(active["phase"], "runtime_pending")
        intent = activation_recovery_intent_v2(host.state)
        encoded = json.dumps(intent, sort_keys=True)
        self.assertNotIn("environment_file", encoded)
        self.assertNotIn("compose_files", encoded)
        self.assertNotIn("raw_render", encoded)
        self.assertEqual(intent["compose_snapshot"]["snapshot_id"],
                         snapshot.snapshot_id)

        observed = runtime.observe_running_v2(
            target=TARGET, services=tuple(plan.policy.persistent_services),
            compose_project="lenzora", topology_digest=intent["topology_digest"],
            compose_config_hashes={row["service"]: row["compose_config_hash"]
                                   for row in intent["compose_projection"]},
            snapshot_digest=intent["compose_snapshot"]["snapshot_digest"])
        projection = activation_recovery_projection(
            host.state, observed_services=observed["services"])
        new_observation = {key: observed[key] for key in (
            "target_epoch_start", "target_epoch_end", "target_identity_start",
            "target_identity_end", "runtime_epoch_start", "runtime_epoch_end")}
        new_observation.update(generation_digest=projection.new_generation_digest,
                               services=observed["services"])
        self.assertEqual(classify_activation_transition(
            projection, new_observation).classification, "exact_new")

        # A different retained new intent makes the unchanged current runtime
        # classify as exact-prior, without needing any caller artifact.
        changed = deepcopy(host.state); raw = deepcopy(intent)
        image_name = raw["images"][0]["name"]
        new_image = raw["images"][0]["image_ref"].split("@", 1)[0] + "@sha256:" + "f" * 64
        raw["images"][0].update(image_ref=new_image,
                                config_digest="sha256:" + "f" * 64,
                                local_image_id="sha256:" + "f" * 64)
        for row in raw["service_image_bindings"]:
            if row["image"] == image_name: row["image_ref"] = new_image
        for row in raw["compose_projection"]:
            binding = next(item for item in raw["service_image_bindings"]
                           if item["service"] == row["service"])
            row["image"] = binding["image_ref"]
        values = {key: value for key, value in raw.items()
                  if key not in {"schema_version", "replacement_intent_digest"}}
        changed["active"]["replacement_intent"] = ReplacementIntentV2.create(
            **values).as_mapping()
        changed = decode_activation_state(changed)
        prior_rows = prior["service_projection"]
        prior_projection = activation_recovery_projection(
            changed, observed_services=prior_rows)
        prior_observation = {"target_epoch_start": "machine-a",
            "target_epoch_end": "machine-a", "target_identity_start": "target-a",
            "target_identity_end": "target-a", "runtime_epoch_start": "daemon-a",
            "runtime_epoch_end": "daemon-a",
            "generation_digest": prior["generation_digest"], "services": prior_rows}
        self.assertEqual(classify_activation_transition(
            prior_projection, prior_observation).classification, "exact_prior")

        for mutate in (
                lambda state: state["active"]["replacement_intent"].pop("route_digest"),
                lambda state: state["active"]["replacement_intent"].update(
                    environment_file="/private/env"),
                lambda state: state["active"]["replacement_intent"].update(
                    replacement_intent_digest=DIGEST_B)):
            invalid = deepcopy(host.state); mutate(invalid)
            with self.assertRaises(ActivationRepositoryError):
                decode_activation_state(invalid)

    def test_crash_after_edge_terminal_recovers_exact_candidate_without_reapply(self):
        from copy import deepcopy
        from sandbox.hosting.images.activation.repository import ActivationRepository
        from sandbox.hosting.images.activation.repository import (
            ActivationRepositoryError, decode_activation_state,
        )
        from sandbox.hosting.images.activation.v2_repository import activation_recovery_projection
        from sandbox.hosting.recovery.policy import classify_activation_transition
        plan, proof, snapshot = artifacts(); grant = grant_for(plan, proof)
        request = request_for(plan, proof, snapshot, grant)
        host = FakeHostStatePort(); stage = FakeStageRepositoryPort()
        repository = ActivationRepository(host_state_port=host,
            stage_repository=stage, target_mutation_port=FakeTargetMutationPort())
        edge = FakeEdgeV2(crash_at="after_terminal")
        service = ActivationServiceV2(repository=repository,
            runtime_adapter=FakeRuntimeV2(), edge_adapter=edge,
            rollback_grant_verifier=FakeGrantVerifier(), clock=lambda: 100)
        kwargs = {"rollback_grant": grant, "compose_files": ("compose.yml",),
            "compose_project": "lenzora", "edge_route_digest": DIGEST_A,
            "admission_deadline": "2999-01-01T00:00:00Z",
            "stage_ledger_authority": "feature-050-stage-ledger-v2",
            "stage_ledger_revision": 1}
        with self.assertRaises(FakeEdgeV2.Crash): service.execute(request, **kwargs)
        active = host.state["active"]; self.assertTrue(active["edge_result"]["terminal"])
        self.assertIsNotNone(active["candidate_generation"])
        changed = deepcopy(host.state)
        changed["active"]["candidate_generation"] = None
        with self.assertRaises(ActivationRepositoryError):
            decode_activation_state(changed)
        changed = deepcopy(host.state)
        changed["active"]["edge_result"]["generation"] += 1
        with self.assertRaises(ActivationRepositoryError):
            decode_activation_state(changed)
        replay = service.execute(request, **kwargs)
        self.assertEqual(replay["result_class"], "uncertain"); self.assertEqual(edge.calls, 1)
        projection = activation_recovery_projection(host.state)
        candidate = active["candidate_generation"]
        runtime_evidence = {"target_epoch_start": "machine-a", "target_epoch_end": "machine-a",
            "target_identity_start": "target-a", "target_identity_end": "target-a",
            "runtime_epoch_start": "daemon-a", "runtime_epoch_end": "daemon-a",
            "generation_digest": candidate["generation_digest"],
            "services": candidate["service_projection"]}
        observation = classify_activation_transition(projection, runtime_evidence)
        self.assertEqual(observation.classification, "exact_new")
        outcome = repository.recover_v2(
            "target-a", request_id="recover-v2-edge", request_digest=DIGEST_B,
            expected_generation=0, observer=lambda: observation)
        self.assertEqual(outcome["code"], "committed")
        self.assertEqual(host.state["generation"], 1)
        self.assertIsNone(host.state["active"]); self.assertEqual(edge.calls, 1)

    def test_recovery_projection_bound_is_64_and_refuses_65(self):
        from sandbox.hosting.recovery.models import ActivationTransitionProjection
        service = {"service": "service-0", "runtime_identity": "container-0",
            "declared_image": "ghcr.io/acme/app@" + DIGEST_A,
            "repository_digest": "ghcr.io/acme/app@" + DIGEST_A,
            "local_image_id": DIGEST_A, "config_digest": DIGEST_A,
            "platform": {"os": "linux", "architecture": "amd64"},
            "topology_identity": DIGEST_A, "compose_project": "app",
            "compose_config_hash": DIGEST_A, "healthy": True}
        rows64 = tuple({**service, "service": f"service-{index}",
                        "runtime_identity": f"container-{index}"} for index in range(64))
        base = {"transaction_digest": DIGEST_A, "request_digest": DIGEST_B,
            "operation": "activate", "phase": "edge_pending", "effect_entered": True,
            "expected_generation": 0, "new_generation_digest": DIGEST_A,
            "prior_generation_digest": None, "target": TARGET,
            "new_services": rows64, "prior_services": ()}
        self.assertEqual(len(ActivationTransitionProjection(**base).new_services), 64)
        row65 = {**service, "service": "service-64", "runtime_identity": "container-64"}
        with self.assertRaises(ValueError):
            ActivationTransitionProjection(**{**base, "new_services": rows64 + (row65,)})


class RemoteActivationTransportV2Tests(unittest.TestCase):
    def test_replace_is_one_no_build_no_pull_effect_and_secret_values_never_enter_argv(self):
        from sandbox.transports.remote_hosting_activation import RegisteredRemoteActivationTransport
        plan, proof, snapshot = artifacts(); calls = []
        images = {row["service"]: row["image_ref"] for row in plan.as_mapping()["service_image_bindings"]
                  if row["kind"] == "persistent"}
        by_name = {row["name"]: row["image_ref"] for row in plan.as_mapping()["images"]}
        environment = {row["environment_variable"]: by_name[row["image"]]
                       for row in plan.as_mapping()["activation_environment_bindings"]}
        def runner(**kwargs):
            calls.append(kwargs)
            argv = kwargs["argv"]
            if argv[:2] == ("docker", "info"):
                stdout = "daemon-a\n"
            elif "config" in argv:
                services = {name: {"image": image, "depends_on": {}}
                            for name, image in images.items()}
                stdout = json.dumps({"services": services,
                    "x-sandbox-configuration-digest": DIGEST_B,
                    "x-sandbox-compose-config-hashes": {name: DIGEST_A for name in services},
                    "x-sandbox-has-configs": False, "x-sandbox-has-secrets": True,
                    "x-sandbox-has-external-networks": False})
            elif argv[:1] == ("sandbox-activation-observe-running-v2",):
                config_by_ref = {item.image_ref: item.config_digest
                                 for item in plan.receipt.images}
                stdout = json.dumps([{"service": name, "compose_project": "lenzora",
                    "runtime_identity": f"container-{name}", "declared_image": image,
                    "repository_digest": image, "local_image_id": config_by_ref[image],
                    "config_digest": config_by_ref[image],
                    "platform": {"os": "linux", "architecture": "amd64"},
                    "healthy": True} for name, image in images.items()])
            else:
                stdout = ""
            return {"returncode": 0, "stdout": stdout, "stderr": "", "terminated": True}
        transport = RegisteredRemoteActivationTransport(
            argv_runner=runner, configuration_binding_key=b"k" * 32,
            target_identity_observer=lambda: {
                "machine_identity": "machine-a", "target_identity": "target-a"})
        selected = tuple(images)
        transport.render_topology_v2(compose_files=("compose.yml",), project_name="lenzora",
            selected_services=selected, service_image_bindings=images,
            environment_bindings=environment,
            topology_digest=proof.observation.observation_digest,
            private_compose_snapshot=snapshot.as_mapping())
        transport.replace_services_v2(compose_files=("compose.yml",), project_name="lenzora",
            services=selected, service_image_bindings=images,
            environment_bindings=environment, snapshot_digest=snapshot.snapshot_digest,
            timeout_seconds=300)
        observed = transport.observe_running_v2(
            target=TARGET, services=selected, compose_project="lenzora",
            topology_digest=proof.observation.observation_digest,
            compose_config_hashes={name: DIGEST_A for name in selected},
            snapshot_digest=snapshot.snapshot_digest)
        self.assertTrue(all(row["topology_identity"] == proof.observation.observation_digest
                            for row in observed["services"]))
        effects = [call for call in calls if "up" in call["argv"]]
        self.assertEqual(len(effects), 1)
        self.assertIn("--no-build", effects[0]["argv"])
        self.assertEqual(effects[0]["argv"][effects[0]["argv"].index("--pull") + 1], "never")
        self.assertNotIn("DB_PASSWORD", json.dumps(calls))


if __name__ == "__main__":
    unittest.main()
