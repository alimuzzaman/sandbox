import hashlib
import unittest

from sandbox.hosting.images.activation.models import activation_digest
from tests.test_hosting_image_activation_execution import graph_request_fixture
from tests.test_hosting_image_activation_v2 import (
    FakeRuntimeV2, FakeEdgeV2, FakeHostStatePort, FakeStageRepositoryPort,
    FakeTargetMutationPort, FakeGrantVerifier, execute,
)


class GraphServiceTests(unittest.TestCase):
    def test_process_death_before_combined_edge_write_stays_uncertain_and_never_promotes(self):
        from sandbox.hosting.images.activation.repository import ActivationRepository
        from sandbox.hosting.images.activation.v2_service import ActivationServiceV2
        from sandbox.hosting.recovery.models import ActivationRecoveryObservation

        _plan, proof, _legacy, _snapshot, grant, request, _contract = graph_request_fixture()
        host, stage = FakeHostStatePort(), FakeStageRepositoryPort()

        class Crash(BaseException):
            pass

        class CrashBeforeEdgeRepository(ActivationRepository):
            def transition_v2(self, target, candidate, phase, **values):
                if phase == "edge_pending":
                    raise Crash()
                return super().transition_v2(target, candidate, phase, **values)

        repository = CrashBeforeEdgeRepository(
            host_state_port=host, stage_repository=stage,
            target_mutation_port=FakeTargetMutationPort(),
        )
        edge = FakeEdgeV2()
        with self.assertRaises(Crash):
            ActivationServiceV2(
                repository=repository, runtime_adapter=FakeRuntimeV2(proof),
                edge_adapter=edge, rollback_grant_verifier=FakeGrantVerifier(),
                clock=lambda: 100,
            ).execute(
                request, rollback_grant=grant, compose_files=("compose.yml",),
                compose_project="lenzora", edge_route_digest="sha256:" + "a" * 64,
                admission_deadline="2999-01-01T00:00:00Z",
                stage_ledger_authority="feature-050-stage-ledger-v2",
                stage_ledger_revision=1,
            )
        active = host.state["active"]
        self.assertEqual(active["phase"], "runtime_pending")
        self.assertIsNone(active["running_observation"])
        self.assertIsNone(active["edge_result"])
        self.assertEqual(edge.calls, 0)

        body = {
            "transaction_digest": active["transaction_digest"],
            "expected_generation": 0,
            "classification": "exact_new",
            "target_epoch_start": "machine-a", "target_epoch_end": "machine-a",
            "target_identity_start": "target-a", "target_identity_end": "target-a",
            "runtime_epoch_start": "daemon-a", "runtime_epoch_end": "daemon-a",
        }
        observation = ActivationRecoveryObservation(
            **body,
            evidence_identity=activation_digest("fixture.graph-recovery.v2", body),
        )
        outcome = repository.recover_v2(
            "target-a", request_id="recover-graph-window",
            request_digest="sha256:" + "b" * 64, expected_generation=0,
            observer=lambda: observation,
        )
        self.assertEqual(outcome["code"], "effect_unknown")
        self.assertEqual(host.state["generation"], 0)
        self.assertIsNotNone(host.state["active"])

    def test_schema2_runtime_proven_without_edge_receipt_is_not_promotable(self):
        from sandbox.hosting.images.activation.repository import recovery_decision

        transaction = {
            "schema_version": 2, "operation": "activate",
            "phase": "runtime_proven", "effect_entered": True,
            "init_receipts": [], "running_observation": {"observation_digest": "x"},
        }
        self.assertEqual(
            recovery_decision(transaction, "exact_new"),
            ("recovery_conflict", False, False),
        )

    def test_retained_schema2_runtime_proven_recovery_stays_conflicted(self):
        from copy import deepcopy
        from sandbox.hosting.images.activation.repository import (
            ActivationRepository, decode_activation_state,
        )
        from sandbox.hosting.images.activation.v2_service import ActivationServiceV2
        from sandbox.hosting.recovery.models import ActivationRecoveryObservation

        _plan, proof, _legacy, _snapshot, grant, request, _contract = graph_request_fixture()
        host, stage = FakeHostStatePort(), FakeStageRepositoryPort()
        repository = ActivationRepository(
            host_state_port=host, stage_repository=stage,
            target_mutation_port=FakeTargetMutationPort(),
        )
        edge = FakeEdgeV2(crash_at="before_terminal")
        service = ActivationServiceV2(
            repository=repository, runtime_adapter=FakeRuntimeV2(proof),
            edge_adapter=edge, rollback_grant_verifier=FakeGrantVerifier(),
            clock=lambda: 100,
        )
        with self.assertRaises(FakeEdgeV2.Crash):
            service.execute(
                request, rollback_grant=grant, compose_files=("compose.yml",),
                compose_project="lenzora", edge_route_digest="sha256:" + "a" * 64,
                admission_deadline="2999-01-01T00:00:00Z",
                stage_ledger_authority="feature-050-stage-ledger-v2",
                stage_ledger_revision=1,
            )

        # This is the old retained v2 shape: runtime proof was durable, but
        # the edge receipt and candidate generation were not part of that write.
        old = deepcopy(host.state)
        old["active"]["phase"] = "runtime_proven"
        old["active"]["edge_result"] = None
        old["active"]["candidate_generation"] = None
        host.state = decode_activation_state(old)
        active = host.state["active"]
        body = {
            "transaction_digest": active["transaction_digest"],
            "expected_generation": 0,
            "classification": "exact_new",
            "target_epoch_start": "machine-a", "target_epoch_end": "machine-a",
            "target_identity_start": "target-a", "target_identity_end": "target-a",
            "runtime_epoch_start": "daemon-a", "runtime_epoch_end": "daemon-a",
        }
        observation = ActivationRecoveryObservation(
            **body,
            evidence_identity=activation_digest(
                "fixture.old-runtime-proven.v2", body),
        )
        observations = []

        def observe():
            observations.append(len(observations) + 1)
            return observation

        outcome = repository.recover_v2(
            "target-a", request_id="recover-old-runtime-proven",
            request_digest="sha256:" + "b" * 64, expected_generation=0,
            observer=observe,
        )
        self.assertEqual(outcome["code"], "recovery_conflict")
        self.assertFalse(outcome["promoted"])
        self.assertEqual(host.state["generation"], 0)
        self.assertEqual(host.state["active"]["phase"], "runtime_proven")
        self.assertIsNone(host.state["recovery_provisional"])
        self.assertEqual(len(observations), 2)

        replay = repository.recover_v2(
            "target-a", request_id="recover-old-runtime-proven",
            request_digest="sha256:" + "b" * 64, expected_generation=0,
            observer=lambda: self.fail("stored recovery must not re-observe"),
        )
        self.assertEqual(replay, outcome)
        self.assertEqual(len(observations), 2)

    def test_real_repository_commits_only_after_full_graph_and_retains_evidence(self):
        from sandbox.hosting.images.activation.repository import ActivationRepository, decode_activation_state
        _, proof, _, snapshot, grant, request, contract = graph_request_fixture()
        host, stage = FakeHostStatePort(), FakeStageRepositoryPort()
        repository = ActivationRepository(host_state_port=host, stage_repository=stage,
                                          target_mutation_port=FakeTargetMutationPort())
        actions = []
        test = self
        class Runtime(FakeRuntimeV2):
            def bind_execution_v2(self, value):
                test.assertEqual(value, request)
            def execute_graph_step_v2(self, *, action, subject, container_identity, timeout_seconds):
                active = host.state["active"]
                test.assertTrue(active["effect_entered"])
                event = active["execution_progress"]["events"][-1]
                expected_stage = {"create": "prepared", "inspect": "created", "start": "effect_entered",
                    "wait": "effect_entered", "cleanup": "exited", "replace": "effect_entered", "ready": "effect_entered"}[action]
                test.assertEqual(event["stage"], expected_stage)
                test.assertEqual(event["subject_digest"], subject["subject_digest"])
                actions.append((action, tuple(subject["services"])))
                if getattr(self, "fail_start", False) and action == "start":
                    raise OSError("synthetic lost start response")
                return {"subject_digest": subject["subject_digest"], "exit_code": 0 if action == "wait" else None,
                    "container_identity": hashlib.sha256(subject["services"][0].encode()).hexdigest() if subject["kind"] == "initializer" else None,
                    "terminated": True}
        runtime = Runtime(proof)
        result = execute(repository, runtime, FakeEdgeV2(), request, grant)
        self.assertTrue(result["ok"], result)
        self.assertEqual(runtime.replacements, [])
        self.assertEqual(host.state["generation"], 1)
        evidence = host.state["current"]["execution_evidence"]
        self.assertEqual(evidence["compose_snapshot"], snapshot.as_mapping())
        self.assertEqual(evidence["compatibility_grant"], grant.as_mapping())
        self.assertEqual(evidence["progress"]["events"][-1]["stage"], "ready")
        self.assertEqual([services[0] for action, services in actions if action == "start"],
                         list(contract.graph.initializer_order))
        self.assertEqual(decode_activation_state(host.state), host.state)

        host, stage = FakeHostStatePort(), FakeStageRepositoryPort()
        repository = ActivationRepository(host_state_port=host, stage_repository=stage,
                                          target_mutation_port=FakeTargetMutationPort())
        runtime, edge = Runtime(proof), FakeEdgeV2()
        runtime.fail_start = True
        result = execute(repository, runtime, edge, request, grant)
        self.assertFalse(result["ok"])
        self.assertEqual(result["result_class"], "uncertain")
        self.assertEqual(host.state["generation"], 0)
        self.assertTrue(host.state["active"]["effect_entered"])
        self.assertEqual(host.state["active"]["execution_progress"]["events"][-1]["stage"], "effect_entered")
        self.assertEqual(edge.calls, 0)
        before = len(actions)
        replay = execute(repository, runtime, edge, request, grant)
        self.assertFalse(replay["ok"])
        self.assertEqual(len(actions), before)
        self.assertEqual(host.state["generation"], 0)
