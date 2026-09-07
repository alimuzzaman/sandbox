import unittest

from sandbox.hosting.images.activation.execution_state import ExecutionProgressV2
from sandbox.hosting.images.activation.execution_runner import execution_step_subject
from tests.test_hosting_image_activation_execution import graph_request_fixture


class ExecutionEvidenceTests(unittest.TestCase):
    def test_only_complete_exact_graph_and_pre_forward_grant_can_be_retained(self):
        from sandbox.hosting.images.activation.execution_evidence import validate_execution_evidence
        plan, proof, _, snapshot, grant, request, contract = graph_request_fixture()
        progress = ExecutionProgressV2.create(graph=contract.graph, request_digest=request.request_digest,
                                              snapshot_digest=snapshot.snapshot_digest)
        subject = {"request_digest": request.request_digest, "target": snapshot.target,
            "generation": 1, "rollback_from_generation_digest": grant.prior_generation_digest,
            "plan_set_digest": plan.plan_set_digest, "proof_set_digest": proof.proof_digest,
            "policy_digest": plan.policy.policy_digest, "configuration_digest": snapshot.configuration_digest,
            "compose_snapshot_digest": snapshot.snapshot_digest,
            "service_image_bindings": [{"service": name} for name in plan.policy.persistent_services],
            "images": [{"name": row.name, "image_ref": row.image_ref, "config_digest": row.config_digest}
                       for row in plan.receipt.images]}
        evidence = {"compose_snapshot": snapshot.as_mapping(), "compatibility_grant": grant.as_mapping(),
                    "progress": progress.as_mapping()}
        with self.assertRaises(ValueError):
            validate_execution_evidence(evidence, subject=subject)
        while progress.next_step is not None:
            index, kind, services, stage = progress.next_step
            step = execution_step_subject(progress=progress, contract=contract, index=index)
            progress = progress.append(stage=stage, subject_digest=step["subject_digest"],
                container_identity=("container-" + services[0]) if kind == "initializer" and stage != "prepared" else None,
                exit_code=0 if stage == "exited" else None)
        evidence["progress"] = progress.as_mapping()
        self.assertEqual(validate_execution_evidence(evidence, subject=subject), evidence)
        for change in ({"request_digest": "sha256:" + "f" * 64}, {"generation": 2},
                       {"proof_set_digest": "sha256:" + "f" * 64}, {"service_image_bindings": []}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_execution_evidence(evidence, subject={**subject, **change})
        from sandbox.hosting.images.activation.v2_models import VerifiedActivationGenerationV2
        from sandbox.hosting.images.activation.v2_service import ActivationServiceV2
        from sandbox.hosting.images.activation.models import activation_digest
        from tests.test_hosting_image_activation_v2 import FakeRepositoryV2, FakeRuntimeV2, FakeEdgeV2, FakeGrantVerifier
        runtime, edge = FakeRuntimeV2(proof), FakeEdgeV2()
        service = ActivationServiceV2(repository=FakeRepositoryV2(), runtime_adapter=runtime, edge_adapter=edge,
                                      rollback_grant_verifier=FakeGrantVerifier(), clock=lambda: 100)
        images, bindings, topology = service._preflight_candidate(request, ("compose.yml",), "app")
        rendered = topology[0]
        observation = service.runtime_observer.observe(target=snapshot.target, compose_project="app",
            bindings=bindings, images=images, topology_digest=proof.observation.observation_digest,
            compose_config_hashes={name: row["compose_config_hash"] for name, row in rendered["services"].items()},
            edge_identity="sha256:" + "a" * 64, snapshot_digest=snapshot.snapshot_digest)
        subject.update(schema_version=2, images=list(images), service_image_bindings=list(bindings),
            topology_digest=proof.observation.observation_digest, compose_project="app",
            compose_projection=[{"service": name, **row} for name, row in sorted(rendered["services"].items())],
            service_projection=observation["services"], running_observation_digest=observation["observation_digest"],
            execution_evidence=evidence)
        subject_digest = activation_digest("sandbox.hosting.images.activation-generation-subject.v2", subject)
        receipt = edge.apply_generation_v2(request_digest=request.request_digest, target=snapshot.target,
            generation=1, generation_subject_digest=subject_digest, route_digest="sha256:" + "a" * 64,
            observation_digest=observation["observation_digest"])
        body = {**subject, "edge_receipt": receipt}
        generation = {**body, "generation_digest": activation_digest("sandbox.hosting.images.activation-generation.v2", body)}
        decoded = VerifiedActivationGenerationV2.from_mapping(generation)
        self.assertEqual(decoded.as_mapping(), generation)
        stripped = {key: value for key, value in body.items() if key != "execution_evidence"}
        stripped["generation_digest"] = activation_digest("sandbox.hosting.images.activation-generation.v2", stripped)
        with self.assertRaises(ValueError):
            VerifiedActivationGenerationV2.from_mapping(stripped)
