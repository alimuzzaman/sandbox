from __future__ import annotations

import unittest

from sandbox.core import _cloudflare_access as access


class TestAccessValidation(unittest.TestCase):
    def test_exact_self_hosted_application_is_required(self):
        self.assertEqual(access.validate_application(
            {"id": "app", "type": "self_hosted", "domain": "hermes.asb.bd"}, "hermes.asb.bd")["id"], "app")
        with self.assertRaises(access.AccessError):
            access.validate_application({"type": "self_hosted", "domain": "other.asb.bd"}, "hermes.asb.bd")

    def test_policy_rejects_broad_or_mfa_disabled_rules(self):
        valid = {"id": "policy", "decision": "allow", "include": [{"email": {"email": "a@example.com"}}], "mfa_config": {"mfa_disabled": False}}
        self.assertTrue(access.validate_policy(valid)["mfa_required"])
        for value in (
            {"decision": "allow", "include": [{"everyone": {}}]},
            {"decision": "allow", "include": [{"email": {"email": "a@example.com"}}]},
            {"decision": "allow", "include": [{"email": {"email": "a@example.com"}}], "mfa_config": {"mfa_disabled": True}},
        ):
            with self.subTest(value=value):
                with self.assertRaises(access.AccessError):
                    access.validate_policy(value)
