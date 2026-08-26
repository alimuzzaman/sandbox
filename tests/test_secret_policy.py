from __future__ import annotations

import unittest

from sandbox.secrets.models import SecretBrokerError
from sandbox.secrets.policy import (
    classify,
    fixed_mask,
    length_bucket,
    metadata,
    validate,
    validate_destination,
    validate_key,
)


class TestSecretPolicy(unittest.TestCase):
    def test_length_buckets_are_fixed(self):
        self.assertEqual(length_bucket(0), "0")
        self.assertEqual(length_bucket(23), "16-23")
        self.assertEqual(length_bucket(24), "24-31")
        self.assertEqual(length_bucket(256), "256+")

    def test_metadata_does_not_return_value(self):
        result = metadata("API_TOKEN", "Abcdefghijklmnop12345678_-", exact_length=True)
        self.assertNotIn("value", result)
        self.assertEqual(result["length_bucket"], "24-31")
        self.assertTrue(result["exact_length_disclosed"])

    def test_recognized_mask_is_public_prefix_plus_last_four(self):
        value = "sk_test_" + "Abcdefghijklmnop1234567890"
        result = fixed_mask("STRIPE_KEY", value)
        self.assertEqual(result["public_prefix"], "sk_test_")
        self.assertEqual(result["last4"], "7890")
        self.assertEqual(result["masked"], "sk_test_<redacted>7890")
        self.assertNotIn("Abcdef", str(result))

    def test_unrecognized_mask_is_only_last_four(self):
        value = "Abcdefghijklmnop12345678_-"
        result = fixed_mask("API_TOKEN", value)
        self.assertIsNone(result["public_prefix"])
        self.assertEqual(result["masked"], "<redacted>78_-")

    def test_protected_classes_never_mask(self):
        values = (
            ("PASSWORD", "Abcdefghijklmnop12345678_-"),
            ("TOKEN", "aaa.bbb.ccc"),
            ("TOKEN", '{"token":"Abcdefghijklmnop12345678_-"}'),
            ("TOKEN", "postgres://user:pass@example.invalid/db"),
            ("TOKEN", "short"),
        )
        for key, value in values:
            with self.subTest(key=key, kind=classify(key, value).kind):
                with self.assertRaisesRegex(SecretBrokerError, "not eligible"):
                    fixed_mask(key, value)

    def test_validation_is_shape_only(self):
        result = validate(
            "stripe-secret-v1", "STRIPE_KEY",
            "sk_test_" + "Abcdefghijklmnop1234567890",
        )
        self.assertFalse(result["live_checked"])
        self.assertEqual(result["syntax"], "pass")
        self.assertNotIn("value", result)

    def test_openrouter_api_key_profile_checks_public_prefix_without_live_use(self):
        value = "sk-or-v1-" + ("0123456789abcdef" * 4)
        result = validate("openrouter-api-key", "OPENROUTER_KEY", value)
        self.assertEqual(result["syntax"], "pass")
        self.assertEqual(result["checks"]["public_prefix"], "pass")
        self.assertFalse(result["live_checked"])

        rejected = validate("openrouter-api-key", "OPENROUTER_KEY", "sk_test_" + "a" * 40)
        self.assertEqual(rejected["syntax"], "fail")
        self.assertEqual(rejected["checks"]["public_prefix"], "fail")

    def test_keys_and_destinations_fail_closed(self):
        with self.assertRaises(SecretBrokerError):
            validate_key("BAD-KEY")
        for destination in ("LD_PRELOAD", "DYLD_INSERT_LIBRARIES", "NODE_OPTIONS", "BASH_ENV"):
            with self.subTest(destination=destination):
                with self.assertRaisesRegex(SecretBrokerError, "alter process execution"):
                    validate_destination(destination)


if __name__ == "__main__":
    unittest.main()
