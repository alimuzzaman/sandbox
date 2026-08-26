"""Setup contracts for the proof-gated Credential Vault feature."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TestCredentialVaultSetup(unittest.TestCase):
    def test_capability_is_registered_but_not_adoptable(self):
        from sandbox.isolation.manifest import MANAGED_ISOLATION_CAPABILITIES

        declarations = {
            item["capability_id"]: item for item in MANAGED_ISOLATION_CAPABILITIES
        }
        declaration = declarations["outbound_credential_mediation"]
        self.assertEqual(declaration["runtime"], "managed-native")
        self.assertEqual(declaration["support_tier"], "implemented_unproven")
        self.assertIsNone(declaration["evidence_id"])
        self.assertFalse(declaration["adoptable"])
        self.assertIn("sandbox.isolation.credential_resolver",
                      declaration["contract_modules"])
        self.assertIn("sandbox.isolation.credential_binding",
                      declaration["contract_modules"])
        self.assertIn("sandbox.isolation.credential_request_broker",
                      declaration["contract_modules"])

    def test_managed_runtime_declares_the_same_capability_without_promotion(self):
        from sandbox.runtimes.manifest import RUNTIME_DECLARATIONS

        managed = next(item for item in RUNTIME_DECLARATIONS
                       if item["adapter_id"] == "ubuntu-nspawn")
        self.assertIn("outbound_credential_mediation",
                      managed["capabilities"])
        self.assertEqual(managed["support_tier"], "implemented_unproven")
        self.assertIsNone(managed["evidence_id"])
        self.assertFalse(managed["adoptable"])

    def test_fixture_contains_only_opaque_and_redacted_values(self):
        fixture = (ROOT / "tests" / "fixtures" / "credential_vault" / "README.md").read_text()
        for marker in (
                "ref:test:credential-vault:fixture",
                "<redacted-never-fixture-data>",
                "<64-hex-redacted>",
                "credential_pending",
        ):
            self.assertIn(marker, fixture)
        self.assertNotIn("Bearer ", fixture)
        self.assertNotIn("sk-", fixture)


if __name__ == "__main__":
    unittest.main()
