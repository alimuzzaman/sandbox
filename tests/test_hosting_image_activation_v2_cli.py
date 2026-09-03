from copy import deepcopy
import unittest

from sandbox.commands.hosting import _host_image_v2_recovery_observation
from sandbox.hosting.images.activation.v2_models import ReplacementIntentV2
from sandbox.hosting.images.activation.v2_repository import activation_recovery_intent_v2
from tests.test_hosting_image_activation_v2 import (
    DIGEST_A, FakeEdgeV2, FakeGrantVerifier, FakeHostStatePort,
    FakeRuntimeV2, FakeStageRepositoryPort, FakeTargetMutationPort,
    TARGET, artifacts, grant_for, request_for,
)


def recovery_state():
    from sandbox.hosting.images.activation.repository import (
        ActivationRepository, decode_activation_state,
    )
    from sandbox.hosting.images.activation.v2_service import ActivationServiceV2

    plan, proof, snapshot = artifacts()
    host = FakeHostStatePort()
    repository = ActivationRepository(
        host_state_port=host, stage_repository=FakeStageRepositoryPort(),
        target_mutation_port=FakeTargetMutationPort())
    first_grant = grant_for(plan, proof)
    first = request_for(plan, proof, snapshot, first_grant)
    common = {"compose_files": ("compose.yml",), "compose_project": "lenzora",
        "edge_route_digest": DIGEST_A,
        "admission_deadline": "2999-01-01T00:00:00Z",
        "stage_ledger_authority": "feature-050-stage-ledger-v2",
        "stage_ledger_revision": 1}
    assert ActivationServiceV2(repository=repository, runtime_adapter=FakeRuntimeV2(),
        edge_adapter=FakeEdgeV2(), rollback_grant_verifier=FakeGrantVerifier(),
        clock=lambda: 100).execute(first, rollback_grant=first_grant, **common)["ok"]
    prior = deepcopy(host.state["current"])
    second_grant = grant_for(plan, proof, generation=1,
                             prior_digest=prior["generation_digest"])
    second = request_for(plan, proof, snapshot, second_grant, generation=1,
                         request_id="fresh-process-recovery-v2")
    runtime = FakeRuntimeV2(); runtime.crash_during_replace = True
    service = ActivationServiceV2(repository=repository, runtime_adapter=runtime,
        edge_adapter=FakeEdgeV2(), rollback_grant_verifier=FakeGrantVerifier(),
        clock=lambda: 100)
    try:
        service.execute(second, rollback_grant=second_grant, **common)
    except FakeRuntimeV2.Crash:
        pass
    else:
        raise AssertionError("fixture replacement did not crash")

    # Make the retained candidate observably different from the prior runtime.
    state = deepcopy(host.state)
    raw = activation_recovery_intent_v2(state)
    image_name = raw["images"][0]["name"]
    new_ref = raw["images"][0]["image_ref"].split("@", 1)[0] + "@sha256:" + "f" * 64
    raw["images"][0].update(image_ref=new_ref, config_digest="sha256:" + "f" * 64,
                            local_image_id="sha256:" + "f" * 64)
    for row in raw["service_image_bindings"]:
        if row["image"] == image_name:
            row["image_ref"] = new_ref
    for row in raw["compose_projection"]:
        binding = next(item for item in raw["service_image_bindings"]
                       if item["service"] == row["service"])
        row["image"] = binding["image_ref"]
    values = {key: value for key, value in raw.items()
              if key not in {"schema_version", "replacement_intent_digest"}}
    state["active"]["replacement_intent"] = ReplacementIntentV2.create(
        **values).as_mapping()
    return decode_activation_state(state), prior


def new_rows(intent):
    images = {row["name"]: row for row in intent["images"]}
    compose = {row["service"]: row for row in intent["compose_projection"]}
    return [{"service": row["service"], "runtime_identity": "new-" + row["service"],
        "declared_image": row["image_ref"], "repository_digest": row["image_ref"],
        "local_image_id": images[row["image"]]["local_image_id"],
        "config_digest": images[row["image"]]["config_digest"],
        "platform": {"os": "linux", "architecture": "amd64"},
        "topology_identity": intent["topology_digest"],
        "compose_project": intent["compose_project"],
        "compose_config_hash": compose[row["service"]]["compose_config_hash"],
        "healthy": True} for row in intent["service_image_bindings"]]


class Transport:
    def __init__(self, rows): self.rows = rows; self.calls = 0
    def observe_running_v2(self, **_kwargs):
        self.calls += 1
        return {"target_epoch_start": TARGET["machine_identity"],
            "target_epoch_end": TARGET["machine_identity"],
            "target_identity_start": TARGET["target_identity"],
            "target_identity_end": TARGET["target_identity"],
            "runtime_epoch_start": TARGET["daemon_identity"],
            "runtime_epoch_end": TARGET["daemon_identity"],
            "services": deepcopy(self.rows)}


class FreshProcessV2RecoveryTests(unittest.TestCase):
    def test_exact_new_exact_prior_and_mismatch_are_closed_reads(self):
        state, prior = recovery_state()
        intent = activation_recovery_intent_v2(state)
        cases = {"exact_new": new_rows(intent),
                 "exact_prior": prior["service_projection"],
                 "ambiguous": deepcopy(new_rows(intent))}
        cases["ambiguous"][0]["healthy"] = False
        for expected, rows in cases.items():
            with self.subTest(expected=expected):
                transport = Transport(rows)
                observed = _host_image_v2_recovery_observation(
                    state, transport, intent)
                self.assertEqual(observed.classification, expected)
                self.assertEqual(transport.calls, 1)


if __name__ == "__main__":
    unittest.main()
