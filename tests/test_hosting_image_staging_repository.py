import json
import tempfile
import unittest
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from tests.hosting_image_fixtures import (
    FakeBroker, FakeWorker, local_observation, stage_request, staging_policy,
)


class OrderedTargetPort:
    def __init__(self, events): self.events = events
    @contextmanager
    def target_mutation_transaction(self, target):
        self.events.append(("enter", "target", target)); yield
        self.events.append(("exit", "target", target))


class OrderedHostPort:
    def __init__(self, events): self.events = events
    @contextmanager
    def atomic_host_state_transaction(self, target):
        self.events.append(("enter", "host", target)); yield
        self.events.append(("exit", "host", target))
    def validate_atomic_host_state_evidence(self, evidence): return True
    def validate_durable_terminal_authority(self, evidence): return True


class TestImageStagingRepository(unittest.TestCase):
    def repository(self, directory):
        from sandbox.hosting.images.staging_repository import StageRepository
        return StageRepository(Path(directory) / "stage")

    def committed(self, repository, request, policy):
        from sandbox.hosting.images.staging_models import StageResult, StagedImageProof
        decision, generation, _ = repository.accept(request)
        self.assertEqual(decision, "accepted")
        proof = StagedImageProof.create(request, policy, local_observation(policy), generation)
        return repository.commit(request, StageResult(
            1, True, "success", "staged", request.request_id, generation, proof))

    def custody(self, repository, target, events=None):
        events = events if events is not None else []
        return repository.proof_custody_transaction(
            target, target_mutation_port=OrderedTargetPort(events),
            host_state_port=OrderedHostPort(events))

    @staticmethod
    def host_evidence(lease, state, receipt=None):
        from sandbox.hosting.images.staging_models import AtomicHostStateEvidence, staging_digest
        body = {"holder": lease.holder, "activation_request_id": lease.activation_request_id,
                "activation_request_digest": lease.activation_request_digest,
                "proof_digest": lease.proof_digest, "state": state,
                "acceptance_receipt": receipt}
        return AtomicHostStateEvidence(**body, evidence_digest=staging_digest(
            "sandbox.hosting.images.atomic-host-state-evidence.v1", body))

    @staticmethod
    def terminal_evidence(lease, terminal="terminal-a"):
        from sandbox.hosting.images.staging_models import (
            DurableTerminalAuthorityEvidence, staging_digest,
        )
        body = {"holder": lease.holder, "proof_digest": lease.proof_digest,
                "acceptance_receipt": lease.acceptance_receipt,
                "terminal_receipt": terminal}
        return DurableTerminalAuthorityEvidence(**body, evidence_digest=staging_digest(
            "sandbox.hosting.images.durable-terminal-authority.v1", body))

    def prepare(self, port, result, request, *, lease_id="lease-a", deadline="2099-01-01T00:00:00Z"):
        return port.prepare(lease_id=lease_id, holder="activation-owner/request-a",
            admission_deadline=deadline, activation_request_id="activation-a",
            activation_request_digest="sha256:" + "c" * 64,
            stage_request_id=request.request_id, stage_request_digest=request.request_digest,
            proof_digest=result.proof.proof_digest, stage_generation=result.generation)

    def test_replay_conflict_generation_and_single_flight_first_commit_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self.repository(directory); policy = staging_policy()
            request = stage_request(policy=policy)
            decision, generation, _ = repository.accept(request)
            self.assertEqual((decision, generation), ("accepted", 1))
            self.assertEqual(repository.accept(request)[2].code, "accepted")
            second = stage_request(request_id="stage-request-b", generation=1, policy=policy)
            self.assertEqual(repository.accept(second)[2].code, "target_busy")
            stale = stage_request(request_id="stage-request-c", generation=0, policy=policy)
            self.assertEqual(repository.accept(stale)[2].code, "target_busy")
            changed = stage_request(generation=1, policy=policy)
            self.assertEqual(repository.accept(changed)[2].code, "request_conflict")

    def test_exact_terminal_replay_and_changed_id_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self.repository(directory); policy = staging_policy()
            request = stage_request(policy=policy); first = self.committed(repository, request, policy)
            decision, _generation, replay = repository.accept(request)
            self.assertEqual((decision, replay.as_mapping()), ("replay", first.as_mapping()))

    def test_uncertainty_is_durable_single_flight_fence(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self.repository(directory); request = stage_request()
            repository.accept(request); uncertain = repository.fence_possible_effect(request)
            self.assertEqual(uncertain.result_class, "uncertain")
            other = stage_request(request_id="stage-request-b", generation=1)
            self.assertEqual(repository.accept(other)[2].code, "target_busy")

    def test_tombstone_saturation_unconditionally_refuses_new_but_retains_replay(self):
        from sandbox.hosting.images.staging_models import MAX_TOMBSTONES
        from sandbox.hosting.images.staging_repository import StageRepositoryError
        with tempfile.TemporaryDirectory() as directory:
            repository = self.repository(directory); request = stage_request()
            with repository.target_lock(request.target.target_identity):
                state = repository._load_unlocked(request.target.target_identity)
                state["tombstones"] = {f"old-{index}": {
                    "request_id": f"old-{index}", "request_digest": "sha256:" + "1" * 64,
                    "proof_digest": "sha256:" + "2" * 64, "result_code": "proof_expired"}
                    for index in range(MAX_TOMBSTONES)}
                repository._write_unlocked(request.target.target_identity, state)
            with self.assertRaisesRegex(StageRepositoryError, "retention_full"):
                repository.accept(request)
            self.assertEqual(repository.lookup(request.target.target_identity, "old-1").code,
                             "proof_expired")

    def test_worst_case_terminal_space_is_reserved_before_effects(self):
        from sandbox.hosting.images.staging_models import MAX_LEDGER_BYTES
        from sandbox.hosting.images.staging_repository import (
            StageRepositoryError, TERMINAL_RESERVATION_BYTES,
        )
        with tempfile.TemporaryDirectory() as directory:
            repository = self.repository(directory); request = stage_request()
            with repository.target_lock(request.target.target_identity):
                state = repository._load_unlocked(request.target.target_identity)
                state["records"]["padding"] = {"opaque": "x" * (
                    MAX_LEDGER_BYTES - TERMINAL_RESERVATION_BYTES)}
                with self.assertRaises(StageRepositoryError):
                    repository._assert_reserved_bound({**state,
                        "active_owner": {"request_id": "x"},
                        "reserved_terminal_bytes": TERMINAL_RESERVATION_BYTES})

    def test_ordered_custody_pins_old_generation_and_promotes_after_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self.repository(directory); policy = staging_policy(); events = []
            first_request = stage_request(policy=policy)
            first = self.committed(repository, first_request, policy)
            second_request = stage_request(request_id="stage-request-b", generation=1, policy=policy)
            self.committed(repository, second_request, policy)
            with self.custody(repository, policy.target.target_identity, events) as port:
                lease = self.prepare(port, first, first_request)
            with repository.target_lock(policy.target.target_identity):
                state = repository._load_unlocked(policy.target.target_identity)
                raw = dict(state["leases"][lease.lease_id])
                raw["admission_deadline"] = "2000-01-01T00:00:00Z"
                state["leases"][lease.lease_id] = raw
                repository._write_unlocked(policy.target.target_identity, state)
            lease = replace(lease, admission_deadline="2000-01-01T00:00:00Z")
            with self.custody(repository, policy.target.target_identity, events) as port:
                receipt = "sha256:" + "d" * 64
                promoted = port.promote(lease, self.host_evidence(lease, "accepted", receipt))
                port.release(promoted, self.terminal_evidence(promoted))
            expected_once = [("enter", "target", "target-a"),
                ("enter", "host", "target-a"), ("exit", "host", "target-a"),
                ("exit", "target", "target-a")]
            self.assertEqual(events, expected_once + expected_once)
            self.assertEqual(lease.stage_generation, first.proof.staging_generation)

    def test_crashed_prepared_lease_stays_pinned_and_cancel_needs_exact_expired_absence(self):
        from sandbox.hosting.images.staging_repository import StageRepositoryError
        with tempfile.TemporaryDirectory() as directory:
            repository = self.repository(directory); policy = staging_policy()
            request = stage_request(policy=policy); result = self.committed(repository, request, policy)
            with self.custody(repository, policy.target.target_identity) as port:
                lease = self.prepare(port, result, request)
            with repository.target_lock(policy.target.target_identity):
                state = repository._load_unlocked(policy.target.target_identity)
                raw = dict(state["leases"][lease.lease_id])
                raw["admission_deadline"] = "2000-01-01T00:00:00Z"
                state["leases"][lease.lease_id] = raw
                repository._write_unlocked(policy.target.target_identity, state)
            expired = replace(lease, admission_deadline="2000-01-01T00:00:00Z")
            with self.custody(repository, policy.target.target_identity) as port:
                from sandbox.hosting.images.staging_models import (
                    AtomicHostStateEvidence, staging_digest,
                )
                wrong_body = {"holder": "activation-owner/other",
                    "activation_request_id": expired.activation_request_id,
                    "activation_request_digest": expired.activation_request_digest,
                    "proof_digest": expired.proof_digest, "state": "absent",
                    "acceptance_receipt": None}
                wrong = AtomicHostStateEvidence(**wrong_body,
                    evidence_digest=staging_digest(
                        "sandbox.hosting.images.atomic-host-state-evidence.v1", wrong_body))
                with self.assertRaises(StageRepositoryError):
                    port.cancel(expired, wrong)
                with self.assertRaises(StageRepositoryError):
                    port.cancel(expired, self.host_evidence(expired, "ambiguous"))
                port.cancel(expired, self.host_evidence(expired, "absent"))

    def test_sixty_four_live_pins_refuse_the_sixty_fifth(self):
        from sandbox.hosting.images.staging_models import MAX_LIVE_PROOF_LEASES
        from sandbox.hosting.images.staging_repository import StageRepositoryError
        with tempfile.TemporaryDirectory() as directory:
            repository = self.repository(directory); policy = staging_policy()
            request = stage_request(policy=policy); result = self.committed(repository, request, policy)
            with self.custody(repository, policy.target.target_identity) as port:
                for index in range(MAX_LIVE_PROOF_LEASES):
                    self.prepare(port, result, request, lease_id=f"lease-{index}")
                with self.assertRaisesRegex(StageRepositoryError, "lease_capacity"):
                    self.prepare(port, result, request, lease_id="lease-overflow")

    def test_acceptance_unknown_and_every_crash_phase_replay_without_duplicate_owner(self):
        phases = (None, "credential_pending", "helper_running", "pulling",
                  "cleanup_pending", "observing")
        for phase in phases:
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                repository = self.repository(directory); request = stage_request()
                self.assertIsNone(repository.lookup(request.target.target_identity, "unknown"))
                repository.accept(request)
                if phase is not None:
                    repository.transition(request, phase,
                        process={"unit_name": "exact", "unit_inactive": False},
                        cleanup={"complete": False})
                reopened = self.repository(directory)
                record = reopened.record_status(request.target.target_identity, request.request_id)
                self.assertEqual(record["phase"], phase or "accepted")
                self.assertEqual(reopened.accept(request)[2].result_class, "in_progress")
                other = stage_request(request_id="other-request", generation=1)
                self.assertEqual(reopened.accept(other)[2].code, "target_busy")

    def test_lost_acceptance_uses_public_status_then_exact_pre_effect_reconcile_once(self):
        from sandbox.hosting.images.staging_service import ImageStagingService
        with tempfile.TemporaryDirectory() as directory:
            repository = self.repository(directory); policy = staging_policy()
            request = stage_request(policy=policy)
            self.assertEqual(repository.accept(request)[:2], ("accepted", 1))
            worker = FakeWorker(); service = ImageStagingService(
                repository=self.repository(directory), broker=FakeBroker(), worker=worker)
            self.assertEqual(service.status(request).result_class, "in_progress")
            result = service.reconcile(request, policy,
                lambda _request, _record: {"exact_effect": False,
                    "unit_inactive": True, "cgroup_empty_or_removed": True,
                    "cleanup_complete": True})
            self.assertTrue(result.ok); self.assertEqual(len(worker.calls), 1)
            self.assertEqual(service.status(request).as_mapping(), result.as_mapping())

    def test_actual_near_sixteen_mib_ledger_refuses_public_accept_before_owner(self):
        from sandbox.hosting.images.staging_models import MAX_LEDGER_BYTES
        from sandbox.hosting.images.staging_repository import (
            StageRepositoryError, TERMINAL_RESERVATION_BYTES,
        )
        with tempfile.TemporaryDirectory() as directory:
            repository = self.repository(directory); request = stage_request()
            with repository.target_lock(request.target.target_identity):
                state = repository._load_unlocked(request.target.target_identity)
                state["records"]["retained-padding"] = {
                    "opaque": "x" * (MAX_LEDGER_BYTES - TERMINAL_RESERVATION_BYTES // 2)}
                repository._write_unlocked(request.target.target_identity, state)
            with self.assertRaisesRegex(StageRepositoryError, "retention_full"):
                repository.accept(request)
            self.assertIsNone(repository.record_status(
                request.target.target_identity, request.request_id))

    def test_sixty_four_full_proofs_compact_only_unpinned_and_make_permanent_tombstone(self):
        from sandbox.hosting.images.staging_models import MAX_PROOFS
        with tempfile.TemporaryDirectory() as directory:
            repository = self.repository(directory); policy = staging_policy(); completed = []
            for index in range(MAX_PROOFS):
                request = stage_request(request_id=f"stage-{index}", generation=index,
                                        policy=policy)
                completed.append((request, self.committed(repository, request, policy)))
            pinned_request, pinned_result = completed[0]
            with self.custody(repository, policy.target.target_identity) as port:
                lease = self.prepare(port, pinned_result, pinned_request, lease_id="pin-first")
            newest = stage_request(request_id="stage-overflow", generation=MAX_PROOFS,
                                   policy=policy)
            self.committed(repository, newest, policy)
            self.assertTrue(repository.lookup(policy.target.target_identity,
                                              pinned_request.request_id).ok)
            compacted_request = completed[1][0]
            self.assertEqual(repository.lookup(policy.target.target_identity,
                                               compacted_request.request_id).code,
                             "proof_expired")
            self.assertEqual(repository.accept(compacted_request)[2].code, "proof_expired")
            unsafe_reuse = stage_request(request_id=compacted_request.request_id,
                                         generation=MAX_PROOFS + 1, policy=policy)
            self.assertEqual(repository.accept(unsafe_reuse)[2].code, "request_conflict")
            with self.custody(repository, policy.target.target_identity) as port:
                self.assertEqual(port.prepare(**{
                    key: value for key, value in lease.as_mapping().items()
                    if key not in {"phase", "target_identity", "ledger_revision",
                                   "acceptance_receipt"}}).as_mapping(), lease.as_mapping())

    def test_lease_binding_replay_expiry_stale_generation_and_holder_authority_matrix(self):
        from sandbox.hosting.images.staging_models import StagingContractError
        from sandbox.hosting.images.staging_repository import StageRepositoryError
        with tempfile.TemporaryDirectory() as directory:
            repository = self.repository(directory); policy = staging_policy()
            request = stage_request(policy=policy); result = self.committed(repository, request, policy)
            with self.custody(repository, policy.target.target_identity) as port:
                lease = self.prepare(port, result, request)
                self.assertEqual(self.prepare(port, result, request).as_mapping(), lease.as_mapping())
                with self.assertRaises(StageRepositoryError):
                    port.prepare(lease_id=lease.lease_id, holder="activation-owner/other",
                        admission_deadline=lease.admission_deadline,
                        activation_request_id=lease.activation_request_id,
                        activation_request_digest=lease.activation_request_digest,
                        stage_request_id=request.request_id,
                        stage_request_digest=request.request_digest,
                        proof_digest=result.proof.proof_digest,
                        stage_generation=result.generation)
                with self.assertRaises(StageRepositoryError):
                    port.prepare(lease_id="stale-generation", holder=lease.holder,
                        admission_deadline=lease.admission_deadline,
                        activation_request_id=lease.activation_request_id,
                        activation_request_digest=lease.activation_request_digest,
                        stage_request_id=request.request_id,
                        stage_request_digest=request.request_digest,
                        proof_digest=result.proof.proof_digest,
                        stage_generation=result.generation + 1)
                for holder in ("process/123", "recovery/request-a"):
                    with self.assertRaises((StagingContractError, StageRepositoryError)):
                        port.prepare(lease_id="bad-" + holder.split("/")[0], holder=holder,
                            admission_deadline=lease.admission_deadline,
                            activation_request_id=lease.activation_request_id,
                            activation_request_digest=lease.activation_request_digest,
                            stage_request_id=request.request_id,
                            stage_request_digest=request.request_digest,
                            proof_digest=result.proof.proof_digest,
                            stage_generation=result.generation)
                with self.assertRaises(StageRepositoryError):
                    self.prepare(port, result, request, lease_id="already-expired",
                                 deadline="2000-01-01T00:00:00Z")

    def test_accepted_replay_cancel_and_release_require_exact_owner_evidence(self):
        from sandbox.hosting.images.staging_repository import StageRepositoryError
        with tempfile.TemporaryDirectory() as directory:
            repository = self.repository(directory); policy = staging_policy()
            request = stage_request(policy=policy); result = self.committed(repository, request, policy)
            with self.custody(repository, policy.target.target_identity) as port:
                lease = self.prepare(port, result, request)
                receipt = "sha256:" + "d" * 64
                accepted = port.promote(lease, self.host_evidence(lease, "accepted", receipt))
                self.assertEqual(port.promote(accepted,
                    self.host_evidence(accepted, "accepted", receipt)).as_mapping(),
                    accepted.as_mapping())
                from sandbox.hosting.images.staging_models import (
                    DurableTerminalAuthorityEvidence, staging_digest,
                )
                wrong_body = {"holder": "activation-owner/other",
                    "proof_digest": accepted.proof_digest,
                    "acceptance_receipt": accepted.acceptance_receipt,
                    "terminal_receipt": "terminal-a"}
                wrong = DurableTerminalAuthorityEvidence(**wrong_body,
                    evidence_digest=staging_digest(
                        "sandbox.hosting.images.durable-terminal-authority.v1", wrong_body))
                with self.assertRaises(StageRepositoryError):
                    port.release(accepted, wrong)
                port.release(accepted, self.terminal_evidence(accepted))

    def test_legacy_ledger_schema_is_rejected(self):
        from sandbox.hosting.images.staging_repository import StageRepositoryError
        with tempfile.TemporaryDirectory() as directory:
            repository = self.repository(directory); request = stage_request()
            ledger, _ = repository._paths(request.target.target_identity)
            ledger.parent.mkdir(parents=True)
            ledger.write_text(json.dumps({"schema_version": 1}))
            with self.assertRaisesRegex(StageRepositoryError, "ledger_invalid"):
                repository.lookup(request.target.target_identity, request.request_id)


if __name__ == "__main__": unittest.main()
