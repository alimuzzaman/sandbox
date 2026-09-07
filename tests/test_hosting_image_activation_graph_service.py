import hashlib
import unittest

from tests.test_hosting_image_activation_execution import graph_request_fixture
from tests.test_hosting_image_activation_v2 import (
    FakeRuntimeV2, FakeEdgeV2, FakeHostStatePort, FakeStageRepositoryPort, FakeTargetMutationPort, execute,
)


class GraphServiceTests(unittest.TestCase):
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
