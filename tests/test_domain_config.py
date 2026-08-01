from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


class TestDomainConfig(unittest.TestCase):
    def test_omitted_identity_retains_default_provenance_and_uses_test(self):
        from sandbox.config.domains import normalize_domain_policy

        policy = normalize_domain_policy({
            "root": "/tmp/example",
            "label": "default",
            "_domains_raw": {"project": {}, "machine_override": {}},
        })

        self.assertEqual(policy["hostname"], None)
        self.assertEqual(policy["tld"], "test")
        self.assertEqual(policy["hostnameSource"], "default")
        self.assertFalse(policy["explicit"])

    def test_explicit_legacy_tld_is_not_treated_as_omitted(self):
        from sandbox.config.domains import normalize_domain_policy

        policy = normalize_domain_policy({
            "root": "/tmp/example",
            "_domains_raw": {"project": {"tld": "tst"}, "machine_override": {}},
        })

        self.assertEqual(policy["tld"], "tst")
        self.assertEqual(policy["suffixClass"], "legacy_private")
        self.assertEqual(policy["hostnameSource"], "project")
        self.assertTrue(policy["explicit"])

    def test_persisted_hostname_wins_without_renaming(self):
        from sandbox.config.domains import normalize_domain_policy

        policy = normalize_domain_policy({
            "root": "/tmp/example",
            "domain": "Existing.TST",
            "tld": "tst",
            "_domains_raw": {"project": {}, "machine_override": {}},
        })

        self.assertEqual(policy["hostname"], "existing.tst")
        self.assertEqual(policy["tld"], "tst")
        self.assertEqual(policy["hostnameSource"], "persisted")

    def test_machine_override_wins_and_reports_source(self):
        from sandbox.config.domains import normalize_domain_policy

        policy = normalize_domain_policy({
            "root": "/tmp/example",
            "_domains_raw": {
                "project": {"hostname": "project.test", "strategy": "resolved"},
                "machine_override": {"hostname": "local.test", "strategy": "hosts"},
            },
        })

        self.assertEqual(policy["hostname"], "local.test")
        self.assertEqual(policy["strategy"], "hosts")
        self.assertEqual(policy["hostnameSource"], "machine_override")
        self.assertEqual(policy["strategySource"], "machine_override")

    def test_new_local_identity_is_rejected_before_observation(self):
        from sandbox.config.domains import normalize_domain_policy

        with self.assertRaisesRegex(ValueError, r"\.local.*\.test"):
            normalize_domain_policy({
                "root": "/tmp/example",
                "_domains_raw": {
                    "project": {"hostname": "example.local"},
                    "machine_override": {},
                },
            })

    def test_unknown_domain_key_is_rejected(self):
        from sandbox.config.domains import normalize_domain_policy

        with self.assertRaisesRegex(ValueError, "unknown domains key"):
            normalize_domain_policy({
                "root": "/tmp/example",
                "_domains_raw": {"project": {"surprise": True}, "machine_override": {}},
            })

    def test_generic_compose_descriptor_preserves_omission(self):
        from sandbox.config.facade import resolve_project_config

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "compose.yml").write_text("services: {}\n")
            (root / "sandbox.config.json").write_text(json.dumps({
                "kind": "compose",
                "compose": {"file": "compose.yml", "service": "web", "internal_port": 80,
                            "health_path": "/"},
            }))
            result = resolve_project_config(
                root, legacy_loader=lambda *_args, **_kwargs: self.fail("legacy loader called"),
            )

        self.assertEqual(result["domains"]["tld"], "test")
        self.assertEqual(result["domains"]["hostnameSource"], "default")

    def test_wordpress_loader_carries_raw_project_tld_provenance(self):
        import sandbox_core as sc

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sandbox.config.json").write_text(json.dumps({"slug": "fixture", "tld": "tst"}))
            with mock.patch.dict("os.environ", {"SANDBOX_PROJECT_ROOTS": str(root.parent)}):
                result = sc.load_project_config(root)

        self.assertEqual(result["domains"]["tld"], "tst")
        self.assertEqual(result["domains"]["hostnameSource"], "project")


if __name__ == "__main__":
    unittest.main()
