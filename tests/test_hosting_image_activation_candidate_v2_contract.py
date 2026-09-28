"""Candidate-v2 is explicit, graph-owned authority; v1 remains readable."""

import unittest

from sandbox.hosting.images.activation.models import ActivationContractError
from sandbox.hosting.images.activation.v2_models import PrivateComposeInputSnapshotV2
from tests.test_hosting_image_activation_execution import graph_request_fixture


class CandidateV2ContractTests(unittest.TestCase):
    def test_contract_change_has_a_distinct_authenticated_snapshot(self):
        _plan, _proof, _legacy, original, _grant, _request, _contract = graph_request_fixture()
        values = original.body_mapping()
        values.pop("schema_version")
        values["input_contract"] = "candidate-v2"
        changed = PrivateComposeInputSnapshotV2.create(**values)
        self.assertNotEqual(changed.snapshot_digest, original.snapshot_digest)
        self.assertEqual(PrivateComposeInputSnapshotV2.from_mapping(changed.as_mapping()), changed)
        self.assertEqual(PrivateComposeInputSnapshotV2.from_mapping(original.as_mapping()), original)
        substituted = {**original.as_mapping(), "input_contract": "candidate-v2"}
        with self.assertRaises(ActivationContractError):
            PrivateComposeInputSnapshotV2.from_mapping(substituted)

    def test_candidate_v2_cannot_use_the_legacy_replace_without_a_graph(self):
        _plan, _proof, legacy, _original, _grant, _request, _contract = graph_request_fixture()
        values = legacy.body_mapping()
        values.pop("schema_version")
        with self.assertRaisesRegex(ActivationContractError, "init_mismatch"):
            PrivateComposeInputSnapshotV2.create(**values, input_contract="candidate-v2")


if __name__ == "__main__":
    unittest.main()
