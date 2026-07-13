import unittest

from sandbox.recovery.catalog import RecoveryCatalog
from sandbox.recovery.errors import RecoveryError, result
from sandbox.recovery.service import RecoveryService


class TestRecoveryService(unittest.TestCase):
    def test_result_redacts_recursive_secret_values_and_keys(self):
        payload = result(False, "create", data={"token": "visible", "nested": ["password=visible"]},
                         error=RecoveryError("passphrase=visible", "blocked"))
        rendered = str(payload)
        self.assertNotIn("visible", rendered)
        self.assertEqual(payload["error"]["code"], "blocked")

    def test_unknown_profile_returns_stable_failure_envelope(self):
        payload = RecoveryService(RecoveryCatalog(1, ())).plan(("missing",))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["action"], "plan")
        self.assertEqual(payload["error"]["code"], "unknown_profile")
