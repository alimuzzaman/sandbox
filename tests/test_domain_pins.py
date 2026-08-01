from __future__ import annotations

import unittest


class TestDomainPins(unittest.TestCase):
    def test_machine_override_wins_each_field_and_source_is_independent(self):
        from sandbox.config.domains import normalize_domain_policy

        policy = normalize_domain_policy({
            "root": "/tmp/project",
            "_domains_raw": {
                "project": {"hostname": "project.test", "strategy": "systemd-resolved"},
                "machine_override": {"strategy": "hosts"},
            },
        })
        self.assertEqual(policy["hostname"], "project.test")
        self.assertEqual(policy["hostnameSource"], "project")
        self.assertEqual(policy["strategy"], "hosts")
        self.assertEqual(policy["strategySource"], "machine_override")


if __name__ == "__main__":
    unittest.main()
