"""Terminal observation and public ACK are separate from effect authority."""
import unittest

from sandbox.hosting.shared_credentials.admission import (
    AdmissionError, require_committed, require_succeeded_operation,
)
from sandbox.jobs.credential_operations import OperationState
from tests import test_host_credentials_fixed_consumers as fixtures


class CredentialPublicationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.FixedManifestResolverTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.ledger = self.fixture.admission._ledger
        self.intent, self.receipt = self.fixture.intent, self.fixture.receipt
        row = require_committed(self.receipt, intent_digest=self.intent.intent_digest())
        row = self.ledger.start(row.scope, row.operation_id, expected_version=row.row_version)
        self.row = self.ledger.finish(row.scope, row.operation_id,
                                      expected_version=row.row_version, succeeded=True)

    def test_internal_success_is_unknown_until_safe_retirement_publication(self):
        self.assertEqual(self.row.state, OperationState.SUCCEEDED)
        self.assertEqual(self.row.public()["status"], "effects_unknown")
        observed = require_succeeded_operation(self.receipt, intent_digest=self.intent.intent_digest())
        self.assertEqual(observed.operation_id, self.row.operation_id)
        with self.assertRaises(AdmissionError):
            require_committed(self.receipt, intent_digest=self.intent.intent_digest())
        published = self.ledger.publish_success(self.row.scope, self.row.operation_id,
                                                expected_version=self.row.row_version)
        self.assertEqual(published.public()["status"], "succeeded")
        self.assertIsNotNone(published.published_at)

    def test_lost_retirement_never_exposes_success_or_reopens_execution(self):
        unknown = self.ledger.withhold_success(self.row.scope, self.row.operation_id,
                                               expected_version=self.row.row_version)
        self.assertEqual(unknown.public()["status"], "effects_unknown")
        self.assertEqual(self.ledger.replay(self.intent).state, OperationState.EFFECTS_UNKNOWN)
        with self.assertRaises(AdmissionError):
            require_succeeded_operation(self.receipt, intent_digest=self.intent.intent_digest())

    def test_terminal_observation_rechecks_real_gate_epoch(self):
        gate = self.receipt._actor._issuer._validator.__self__
        gate._policy_epoch = "replaced-gate-policy"
        with self.assertRaises(ValueError):
            require_succeeded_operation(self.receipt, intent_digest=self.intent.intent_digest())
        self.assertEqual(self.ledger.lookup(self.row.scope).public()["status"], "effects_unknown")


if __name__ == "__main__":
    unittest.main()
