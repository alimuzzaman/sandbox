from __future__ import annotations

import unittest


class TestHostnameIntentSelection(unittest.TestCase):
    def test_new_default_and_existing_legacy_are_distinct(self):
        from sandbox.config.domains import normalize_domain_policy

        new = normalize_domain_policy({"root": "/tmp/new", "_domains_raw": {}})
        legacy = normalize_domain_policy({
            "root": "/tmp/old", "_persisted_hostname": "old.tst", "_domains_raw": {},
        })
        self.assertEqual((new["tld"], new["hostnameSource"]), ("test", "default"))
        self.assertEqual((legacy["hostname"], legacy["hostnameSource"]),
                         ("old.tst", "persisted"))

    def test_existing_local_is_preserved_but_requires_migration(self):
        from sandbox.config.domains import normalize_domain_policy

        policy = normalize_domain_policy({
            "root": "/tmp/old", "_persisted_hostname": "old.local", "_domains_raw": {},
        })
        self.assertEqual(policy["hostname"], "old.local")
        self.assertEqual(policy["migrationState"], "required")

    def test_public_fqdn_is_verify_only(self):
        from sandbox.config.domains import normalize_domain_policy

        policy = normalize_domain_policy({
            "root": "/tmp/public",
            "_domains_raw": {"project": {"hostname": "app.example.com"}},
        })
        self.assertEqual(policy["suffixClass"], "public")
        self.assertEqual(policy["hostname"], "app.example.com")


if __name__ == "__main__":
    unittest.main()
