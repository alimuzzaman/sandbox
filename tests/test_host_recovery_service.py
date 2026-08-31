import hashlib
import copy
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from sandbox.hosting.recovery.models import (
    RecoveryAction, RecoveryRequest, TargetIdentity, canonical_digest,
)
from sandbox.hosting.recovery.repository import RecoveryRepository
from sandbox.hosting.recovery.service import RecoveryAuthorityError, RecoveryService


class HostRecoveryServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.repository = RecoveryRepository(root / "hosts.json", root / "locks")
        self.target = TargetIdentity("remote", "project", "development")
        self.request = RecoveryRequest(
            RecoveryAction.OBSERVE_RECONCILE, "recover-1", "a" * 32,
            "apply-1", self.target, 0,
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
            "topology": ["web"], "images": [{"name": "web", "id": "sha256:" + "5" * 64}],
            "config_file_digests": [
                {"name": str(index), "digest": "sha256:" + str(7 + index) * 64}
                for index in range(4)],
            "phase_receipt_digest": "sha256:" + "8" * 64,
            "one_shot_phases": [], "pending_phases": ["edge"],
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
        operation = {"schema_version": 1, "accepted_before_effects": True,
                     "compose_file_count": 1,
                     "expected_persistent_services": ["web"],
                     "expected_initializer_services": [],
                     "expected_one_shot_phases": [],
                     "job_id": "a" * 32, "request_id": "apply-1",
                     "target": self.target.as_dict(), "project_identity": "project-id",
                     "starting_generation": 0,
                     "project_root_digest": "sha256:" + hashlib.sha256(b"/project").hexdigest(),
                     "source": {"clean": True, "identity": "source-id", "commit": "a" * 40},
                     "evidence": self.evidence}
        operation["digest"] = canonical_digest(operation)
        state = self.repository.load()
        self.repository.target(state, self.target.key)["hosting_operation"] = operation
        self.repository._write(state)
        self.calls = []

    def tearDown(self):
        self.temporary.cleanup()

    def observation(self, _request, _operation):
        self.calls.append("observe")
        return {"schema_version": 1, "complete": True, "bounded": True,
                "epoch_start": "epoch", "epoch_end": "epoch", **self.evidence,
                "services": [{"service": "web", "state": "ready"}],
                "phases": [{"phase": "runtime", "state": "complete"}]}

    def service(self, job=None):
        return RecoveryService(
            repository=self.repository,
            job_lookup=lambda _job_id: job or self.job(),
            source_check=lambda _operation: True, observer=self.observation,
        )

    def test_exact_reconcile_advances_once_and_replay_observes_nothing(self):
        with patch.object(self.repository, "_write",
                          wraps=self.repository._write) as durable_write:
            first = self.service().recover(self.request)
            second = self.service().recover(self.request)
        self.assertTrue(first["ok"])
        self.assertEqual(second["result_class"], "already_reconciled")
        self.assertEqual(second["original_result_class"], "observation_reconciled")
        self.assertEqual(second["generation"], first["generation"])
        self.assertEqual(self.calls, ["observe", "observe"])
        self.assertEqual(first["generation"], {"expected": 0, "resulting": 1})
        self.assertEqual(durable_write.call_count, 3)

    def test_distinct_request_cannot_consume_same_observation_authority(self):
        first = self.service().recover(self.request)
        distinct = RecoveryRequest(
            RecoveryAction.OBSERVE_RECONCILE, "recover-2", "a" * 32,
            "apply-1", self.target, 1)
        second = self.service().recover(distinct)
        self.assertTrue(first["ok"])
        self.assertEqual(second["result_class"], "mutation_required")
        self.assertEqual(self.calls, ["observe", "observe"])

    def test_historical_legacy_shape_refuses_before_remote_observation(self):
        entered = []
        service = RecoveryService(
            repository=self.repository,
            job_lookup=lambda _job: {
                "job_id": "a" * 32, "lifecycle": "failed", "submission": None},
            source_check=lambda _operation: True, observer=self.observation,
            broker_guard=lambda _request: entered.append("broker"))
        result = service.recover(self.request)
        self.assertEqual(result["result_class"], "legacy_evidence")
        self.assertEqual(self.calls, [])
        self.assertEqual(entered, [])
        self.assertFalse(self.repository.lock_dir.exists())

    def test_broker_guard_spans_observation_and_durable_commit(self):
        events = []

        @contextmanager
        def guard(_request):
            events.append("enter")
            yield
            saved = self.repository.load()["hosts"][self.target.key]
            self.assertEqual(saved["generation"], 1)
            events.append("exit")

        def observe(request, operation):
            self.assertEqual(events, ["enter"])
            return self.observation(request, operation)

        service = RecoveryService(
            repository=self.repository, job_lookup=lambda _job: self.job(),
            source_check=lambda _op: True, observer=observe,
            broker_guard=guard)
        self.assertTrue(service.recover(self.request)["ok"])
        self.assertEqual(events, ["enter", "exit"])

    def job(self):
        return {"job_id": "a" * 32, "lifecycle": "failed", "submission": {
            "version": 1, "request_id": "apply-1", "project_identity": "project-id",
            "project_root": "/project", "cwd_relative": ".", "source": {"identity": "source-id",
                "commit": "a" * 40, "dirty_digest": None},
            "argv": ["./sb", "host", "apply", "--project-dir", "/project",
                     "--environment", "development", "--remote", "remote", "--confirm"]}}

    def test_confirmed_sole_pending_edge_reaches_only_edge_adapter(self):
        observed = self.service().recover(self.request)
        witness = []
        edge = RecoveryRequest(
            RecoveryAction.CONTINUE_EDGE, "edge-1", "a" * 32, "apply-1",
            self.target, 1, observation_request_id="recover-1",
            evidence_id=observed["evidence"]["id"], confirmed=True)
        service = RecoveryService(
            repository=self.repository, job_lookup=lambda _job: self.job(),
            source_check=lambda _op: True, observer=self.observation,
            governance_check=lambda _req: True,
            edge_adapter=lambda _req, _op: witness.append("edge") or True,
        )
        result = service.recover(edge)
        self.assertEqual(result["result_class"], "edge_only_completed")
        self.assertEqual(witness, ["edge"])

        replay = service.recover(edge)
        self.assertEqual(replay["result_class"], "edge_only_completed")
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(witness, ["edge"])

    def test_runtime_change_between_validation_and_commit_refuses(self):
        observations = [
            {"schema_version": 1, "complete": True, "bounded": True,
             "epoch_start": "epoch", "epoch_end": "epoch", **self.evidence,
             "services": [{"service": "web", "state": "ready"}],
             "phases": [{"phase": "runtime", "state": "complete"}],
             "race_marker": "before"},
            {"schema_version": 1, "complete": True, "bounded": True,
             "epoch_start": "epoch", "epoch_end": "epoch", **self.evidence,
             "services": [{"service": "web", "state": "ready"}],
             "phases": [{"phase": "runtime", "state": "complete"}],
             "race_marker": "after"},
        ]
        calls = []

        def observe(*_args):
            calls.append("observe")
            if len(calls) == 2:
                provisional = self.repository.load()["hosts"][self.target.key]
                self.assertEqual(provisional["generation"], 0)
                self.assertNotIn("recovery_receipt", provisional)
                self.assertEqual(provisional["recovery_attempts"], [])
                self.assertIs(provisional["recovery_provisional"]["authorizing"], False)
                self.assertEqual(
                    provisional["active_operation"]["phase"],
                    "reconciliation_provisional")
            return observations.pop(0)

        service = RecoveryService(
            repository=self.repository, job_lookup=lambda _job: self.job(),
            source_check=lambda _op: True,
            observer=observe)
        result = service.recover(self.request)
        self.assertEqual(result["result_class"], "evidence_changed")
        saved = self.repository.load()["hosts"][self.target.key]
        self.assertEqual(saved["generation"], 0)
        self.assertNotIn("recovery_receipt", saved)
        self.assertNotIn("recovery_provisional", saved)
        self.assertEqual(saved["recovery_attempts"][-1]["result_class"],
                         "evidence_changed")

    def test_process_loss_after_provisional_commit_resumes_only_postcheck(self):
        class SimulatedProcessLoss(BaseException):
            pass

        calls = []

        def interrupted(request, operation):
            calls.append("observe")
            if len(calls) == 2:
                raise SimulatedProcessLoss()
            return self.observation(request, operation)

        interrupted_service = RecoveryService(
            repository=self.repository, job_lookup=lambda _job: self.job(),
            source_check=lambda _op: True, observer=interrupted)
        with self.assertRaises(SimulatedProcessLoss):
            interrupted_service.recover(self.request)
        provisional = self.repository.load()["hosts"][self.target.key]
        self.assertEqual(provisional["generation"], 0)
        self.assertNotIn("recovery_receipt", provisional)
        self.assertEqual(provisional["recovery_attempts"], [])
        self.assertIs(provisional["recovery_provisional"]["authorizing"], False)

        edge_effects = []
        edge_request = RecoveryRequest(
            RecoveryAction.CONTINUE_EDGE, "edge-during-provisional", "a" * 32,
            "apply-1", self.target, 0,
            observation_request_id=self.request.request_id,
            evidence_id=provisional["recovery_provisional"]["evidence_id"],
            confirmed=True)
        blocked_edge = RecoveryService(
            repository=self.repository, job_lookup=lambda _job: self.job(),
            source_check=lambda _op: True, observer=self.observation,
            governance_check=lambda _req: True,
            edge_adapter=lambda *_args: edge_effects.append("edge") or True)
        self.assertEqual(blocked_edge.recover(edge_request)["result_class"],
                         "operation_busy")
        self.assertEqual(edge_effects, [])

        resume_calls = []
        resumed = RecoveryService(
            repository=self.repository, job_lookup=lambda _job: self.job(),
            source_check=lambda _op: True,
            observer=lambda request, operation: (
                resume_calls.append("postcheck") or self.observation(request, operation)))
        result = resumed.recover(self.request)
        self.assertEqual(result["result_class"], "observation_reconciled")
        self.assertEqual(resume_calls, ["postcheck"])
        saved = self.repository.load()["hosts"][self.target.key]
        self.assertEqual(saved["generation"], 1)
        self.assertNotIn("recovery_provisional", saved)

    def test_missing_confirmation_governance_and_broader_pending_phase_refuse(self):
        observed = self.service().recover(self.request)
        base = dict(job_id="a" * 32, original_request_id="apply-1",
                    target=self.target, expected_generation=1,
                    observation_request_id="recover-1",
                    evidence_id=observed["evidence"]["id"])
        witness = []
        unconfirmed = RecoveryRequest(
            RecoveryAction.CONTINUE_EDGE, "edge-unconfirmed", confirmed=False, **base)
        service = RecoveryService(
            repository=self.repository, job_lookup=lambda _job: self.job(),
            source_check=lambda _op: True, observer=self.observation,
            governance_check=lambda _req: True,
            edge_adapter=lambda *_args: witness.append("edge") or True)
        self.assertEqual(service.recover(unconfirmed)["result_class"],
                         "confirmation_required")
        no_governance = RecoveryRequest(
            RecoveryAction.CONTINUE_EDGE, "edge-no-governance",
            confirmed=True, **base)
        blocked = RecoveryService(
            repository=self.repository, job_lookup=lambda _job: self.job(),
            source_check=lambda _op: True, observer=self.observation,
            governance_check=lambda _req: False,
            edge_adapter=lambda *_args: witness.append("edge") or True)
        self.assertEqual(blocked.recover(no_governance)["result_class"],
                         "governance_unavailable")
        state = self.repository.load()
        record = state["hosts"][self.target.key]
        observation_attempt = next(item for item in record["recovery_attempts"]
                                   if item["request_id"] == "recover-1")
        observation_attempt["phases"].append({"phase": "migration", "state": "pending"})
        self.repository._write(state)
        broader = RecoveryRequest(
            RecoveryAction.CONTINUE_EDGE, "edge-broader", confirmed=True, **base)
        self.assertEqual(service.recover(broader)["result_class"], "mutation_required")
        self.assertEqual(witness, [])

    def test_uncertain_edge_fences_different_request(self):
        observed = self.service().recover(self.request)
        base = dict(job_id="a" * 32, original_request_id="apply-1",
                    target=self.target, expected_generation=1,
                    observation_request_id="recover-1",
                    evidence_id=observed["evidence"]["id"], confirmed=True)
        calls = []
        service = RecoveryService(
            repository=self.repository, job_lookup=lambda _job: self.job(),
            source_check=lambda _op: True, observer=self.observation,
            governance_check=lambda _req: True,
            edge_adapter=lambda *_args: calls.append("edge") or (_ for _ in ()).throw(TimeoutError()))
        first = service.recover(RecoveryRequest(
            RecoveryAction.CONTINUE_EDGE, "edge-uncertain", **base))
        second = service.recover(RecoveryRequest(
            RecoveryAction.CONTINUE_EDGE, "edge-different", **base))
        self.assertEqual(first["result_class"], "effect_unknown")
        self.assertEqual(second["result_class"], "effect_unknown")
        self.assertEqual(calls, ["edge"])

    def test_malformed_uncertainty_fails_closed_before_distinct_edge(self):
        observed = self.service().recover(self.request)
        state = self.repository.load()
        state["hosts"][self.target.key]["recovery_uncertainty"] = {"schema_version": 1}
        self.repository._write(state)
        edge = RecoveryRequest(
            RecoveryAction.CONTINUE_EDGE, "edge-malformed", "a" * 32, "apply-1",
            self.target, 1, observation_request_id="recover-1",
            evidence_id=observed["evidence"]["id"], confirmed=True)
        effects = []
        service = RecoveryService(
            repository=self.repository, job_lookup=lambda _job: self.job(),
            source_check=lambda _op: True, observer=self.observation,
            governance_check=lambda _req: True,
            edge_adapter=lambda *_args: effects.append("edge") or True)
        result = service.recover(edge)
        self.assertEqual(result["result_class"], "persistence_failed")
        self.assertEqual(effects, [])

    def test_expired_observation_cannot_start_edge(self):
        now = [100]
        observe_service = RecoveryService(
            repository=self.repository, job_lookup=lambda _job: self.job(),
            source_check=lambda _op: True, observer=self.observation,
            clock=lambda: now[0], evidence_ttl_seconds=5)
        observed = observe_service.recover(self.request)
        now[0] = 105
        edge = RecoveryRequest(
            RecoveryAction.CONTINUE_EDGE, "edge-expired", "a" * 32, "apply-1",
            self.target, 1, observation_request_id="recover-1",
            evidence_id=observed["evidence"]["id"], confirmed=True)
        witness = []
        service = RecoveryService(
            repository=self.repository, job_lookup=lambda _job: self.job(),
            source_check=lambda _op: True, observer=self.observation,
            governance_check=lambda _req: True,
            edge_adapter=lambda *_args: witness.append("edge") or True,
            clock=lambda: now[0])
        self.assertEqual(service.recover(edge)["result_class"], "expired_evidence")
        self.assertEqual(witness, [])

    def test_persisted_owner_survives_process_loss_and_blocks_takeover(self):
        state = self.repository.load()
        record = state["hosts"][self.target.key]
        record["active_operation"] = {
            "schema_version": 1, "request_id": "lost-owner",
            "request_digest": "sha256:" + "1" * 64,
            "action": "observe_reconcile", "expected_generation": 0,
            "accepted_at": 1, "started_at": 1,
            "phase": "observation_pending", "effect_entered": False,
        }
        self.repository._write(state)
        result = self.service().recover(self.request)
        self.assertEqual(result["result_class"], "operation_busy")
        saved = self.repository.load()["hosts"][self.target.key]
        self.assertEqual(saved["active_operation"]["request_id"], "lost-owner")
        self.assertEqual(self.calls, [])

    def test_empty_active_or_uncertainty_state_fails_closed(self):
        for field in ("active_operation", "recovery_uncertainty"):
            with self.subTest(field=field):
                state = self.repository.load()
                state["hosts"][self.target.key][field] = {}
                self.repository._write(state)
                result = self.service().recover(self.request)
                self.assertEqual(result["result_class"], "persistence_failed")
                self.assertEqual(self.calls, [])
                state["hosts"][self.target.key].pop(field)
                self.repository._write(state)

    def test_same_observation_owner_resumes_after_pre_effect_process_loss(self):
        state = self.repository.load()
        self.repository.begin(state, self.target.key, self.request)
        result = self.service().recover(self.request)
        self.assertEqual(result["result_class"], "observation_reconciled")
        self.assertEqual(self.calls, ["observe", "observe"])
        saved = self.repository.load()["hosts"][self.target.key]
        self.assertIsNone(saved["active_operation"])
        self.assertEqual(saved["generation"], 1)

    def test_effect_entered_owner_never_resumes_as_observation(self):
        state = self.repository.load()
        record = state["hosts"][self.target.key]
        record["active_operation"] = {
            "schema_version": 1, "request_id": "edge-owner",
            "request_digest": "sha256:" + "9" * 64,
            "action": "continue_edge", "expected_generation": 0,
            "accepted_at": 1, "started_at": 1,
            "phase": "effect_entered", "effect_entered": True,
            "effect_entered_at": 1,
        }
        self.repository._write(state)
        result = self.service().recover(self.request)
        self.assertEqual(result["result_class"], "effect_unknown")
        self.assertEqual(result["result_family"], "uncertain")
        self.assertEqual(self.calls, [])

    def test_persistence_fault_returns_stable_failure(self):
        with patch.object(self.repository, "_write", side_effect=OSError("synthetic")):
            result = self.service().recover(self.request)
        self.assertEqual(result["result_class"], "persistence_failed")
        self.assertEqual(result["result_family"], "failed")

    def test_repointed_registered_authority_refuses_before_observation(self):
        entered = []

        @contextmanager
        def changed_authority(_request, _operation):
            entered.append("authority")
            raise RecoveryAuthorityError("registered remote changed")
            yield

        service = RecoveryService(
            repository=self.repository, job_lookup=lambda _job: self.job(),
            source_check=lambda _operation: True, observer=self.observation,
            authority_guard=changed_authority)
        result = service.recover(self.request)
        self.assertEqual(result["result_class"], "changed_target")
        self.assertEqual(entered, ["authority"])
        self.assertEqual(self.calls, [])

    def test_edge_commit_failure_is_uncertain_and_persisted_owner_fences_retry(self):
        observed = self.service().recover(self.request)
        edge = RecoveryRequest(
            RecoveryAction.CONTINUE_EDGE, "edge-persist", "a" * 32, "apply-1",
            self.target, 1, observation_request_id="recover-1",
            evidence_id=observed["evidence"]["id"], confirmed=True)
        calls = []
        service = RecoveryService(
            repository=self.repository, job_lookup=lambda _job: self.job(),
            source_check=lambda _op: True, observer=self.observation,
            governance_check=lambda _req: True,
            edge_adapter=lambda *_args: calls.append("edge") or True)
        with patch.object(self.repository, "commit", side_effect=OSError("disk full")):
            first = service.recover(edge)
        second = service.recover(RecoveryRequest(
            RecoveryAction.CONTINUE_EDGE, "edge-retry", "a" * 32, "apply-1",
            self.target, 1, observation_request_id="recover-1",
            evidence_id=observed["evidence"]["id"], confirmed=True))
        self.assertEqual(first["result_class"], "effect_unknown")
        self.assertEqual(second["result_class"], "effect_unknown")
        observation_takeover = service.recover(RecoveryRequest(
            RecoveryAction.OBSERVE_RECONCILE, "observe-takeover", "a" * 32,
            "apply-1", self.target, 1))
        self.assertEqual(observation_takeover["result_class"], "effect_unknown")
        self.assertEqual(
            self.repository.load()["hosts"][self.target.key]["active_operation"]
            ["request_id"], "edge-persist")
        self.assertEqual(calls, ["edge"])

    def test_edge_exception_plus_uncertainty_commit_failure_stays_effect_unknown(self):
        observed = self.service().recover(self.request)
        edge = RecoveryRequest(
            RecoveryAction.CONTINUE_EDGE, "edge-error-persist", "a" * 32,
            "apply-1", self.target, 1, observation_request_id="recover-1",
            evidence_id=observed["evidence"]["id"], confirmed=True)
        service = RecoveryService(
            repository=self.repository, job_lookup=lambda _job: self.job(),
            source_check=lambda _op: True, observer=self.observation,
            governance_check=lambda _req: True,
            edge_adapter=lambda *_args: (_ for _ in ()).throw(RuntimeError("edge")))
        with patch.object(self.repository, "commit", side_effect=OSError("disk full")):
            result = service.recover(edge)
        self.assertEqual(result["result_class"], "effect_unknown")
        self.assertEqual(result["result_family"], "uncertain")
        self.assertEqual(
            self.repository.load()["hosts"][self.target.key]["active_operation"]
            ["request_id"], "edge-error-persist")

    def test_missing_governance_verifier_never_calls_edge_adapter(self):
        observed = self.service().recover(self.request)
        edge = RecoveryRequest(
            RecoveryAction.CONTINUE_EDGE, "edge-no-governance", "a" * 32,
            "apply-1", self.target, 1, observation_request_id="recover-1",
            evidence_id=observed["evidence"]["id"], confirmed=True)
        entered = []
        service = RecoveryService(
            repository=self.repository, job_lookup=lambda _job: self.job(),
            source_check=lambda _op: True, observer=self.observation,
            edge_adapter=lambda *_args: entered.append("edge") or True)
        result = service.recover(edge)
        self.assertEqual(result["result_class"], "governance_unavailable")
        self.assertEqual(entered, [])

    def test_malformed_one_shot_phases_refuse_without_owner_wedge(self):
        original = self.repository.load()["hosts"][self.target.key]["hosting_operation"]
        cases = (
            ("mapping", {}, [], "partial_evidence"),
            ("hostile", [{"phase": "init:migrate", "state": "complete",
                           "private": "x"}], ["init:migrate"], "partial_evidence"),
            ("duplicate", [{"phase": "init:migrate", "state": "complete"},
                            {"phase": "init:migrate", "state": "complete"}],
             ["init:migrate"], "partial_evidence"),
            ("missing", [], ["init:migrate"], "partial_evidence"),
            ("pending", [{"phase": "init:migrate", "state": "pending"}],
             ["init:migrate"], "mutation_required"),
        )
        for label, phases, expected_phases, expected in cases:
            with self.subTest(label=label):
                operation = copy.deepcopy(original)
                operation["expected_one_shot_phases"] = expected_phases
                operation["expected_initializer_services"] = ["migrate"]
                operation["evidence"]["topology"] = ["web", "migrate"]
                operation["evidence"]["one_shot_phases"] = phases
                operation["digest"] = canonical_digest({
                    key: value for key, value in operation.items() if key != "digest"})
                self.repository._write({"version": 1, "hosts": {self.target.key: {
                    "generation": 0, "hosting_operation": operation}}})
                request = RecoveryRequest(
                    RecoveryAction.OBSERVE_RECONCILE, f"phase-{label}", "a" * 32,
                    "apply-1", self.target, 0)
                result = self.service().recover(request)
                self.assertEqual(result["result_class"], expected)
                record = self.repository.load()["hosts"][self.target.key]
                self.assertIsNone(record.get("active_operation"))
        self.assertEqual(self.calls, [])

    def test_omitted_initializer_projections_and_empty_services_never_reconcile(self):
        original = self.repository.load()["hosts"][self.target.key]["hosting_operation"]
        cases = []
        omitted = copy.deepcopy(original)
        omitted.pop("expected_initializer_services")
        omitted.pop("expected_one_shot_phases")
        cases.append(("omitted", omitted))
        empty = copy.deepcopy(original)
        empty["expected_persistent_services"] = []
        empty["evidence"]["topology"] = []
        cases.append(("empty", empty))
        for label, operation in cases:
            with self.subTest(label=label):
                operation["digest"] = canonical_digest({
                    key: value for key, value in operation.items() if key != "digest"})
                self.repository._write({"version": 1, "hosts": {self.target.key: {
                    "generation": 0, "hosting_operation": operation}}})
                request = RecoveryRequest(
                    RecoveryAction.OBSERVE_RECONCILE, f"projection-{label}",
                    "a" * 32, "apply-1", self.target, 0)
                result = self.service().recover(request)
                self.assertEqual(result["result_class"], "partial_evidence")
                self.assertFalse(result["ok"])
                self.assertIsNone(self.repository.load()["hosts"][self.target.key].get(
                    "active_operation"))
        self.assertEqual(self.calls, [])

        state = {"version": 1, "hosts": {self.target.key: {
            "generation": 0, "hosting_operation": copy.deepcopy(original)}}}
        self.repository._write(state)

        def empty_services(request, operation):
            value = self.observation(request, operation)
            value["services"] = []
            return value

        service = RecoveryService(
            repository=self.repository, job_lookup=lambda _job: self.job(),
            source_check=lambda _operation: True, observer=empty_services)
        request = RecoveryRequest(
            RecoveryAction.OBSERVE_RECONCILE, "projection-empty-observation",
            "a" * 32, "apply-1", self.target, 0)
        result = service.recover(request)
        self.assertEqual(result["result_class"], "mutation_required")
        self.assertIsNone(self.repository.load()["hosts"][self.target.key].get(
            "active_operation"))

    def test_exact_replay_precedes_missing_job_but_unseen_does_not_create_locks(self):
        first = self.service().recover(self.request)
        service = RecoveryService(
            repository=self.repository,
            job_lookup=lambda _job: (_ for _ in ()).throw(RuntimeError("missing")),
            source_check=lambda _operation: True, observer=self.observation)
        replay = service.recover(self.request)
        self.assertEqual(replay["result_class"], "already_reconciled")
        self.assertEqual(self.calls, ["observe", "observe"])

        root = Path(self.temporary.name) / "unseen"
        repository = RecoveryRepository(root / "hosts.json", root / "locks")
        unseen = RecoveryRequest(
            RecoveryAction.OBSERVE_RECONCILE, "never-seen", "b" * 32,
            "apply-other", self.target, 0)
        unseen_service = RecoveryService(
            repository=repository,
            job_lookup=lambda _job: (_ for _ in ()).throw(RuntimeError("missing")),
            source_check=lambda _operation: True, observer=self.observation)
        result = unseen_service.recover(unseen)
        self.assertEqual(result["result_class"], "job_ineligible")
        self.assertFalse(repository.state_path.exists())
        self.assertFalse(repository.lock_dir.exists())


if __name__ == "__main__":
    unittest.main()
