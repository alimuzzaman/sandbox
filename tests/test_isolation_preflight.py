import unittest


class TestIsolationPreflight(unittest.TestCase):
    def facts(self):
        return {"os_id": "ubuntu", "version_id": "24.04", "systemd_version": 255}

    def test_every_effective_gate_is_required_and_code_presence_does_not_advertise(self):
        from sandbox.isolation.preflight import IsolationPreflight
        preflight = IsolationPreflight(
            facts=self.facts, command_probe=lambda _command: True,
            effective_probe=lambda _gate: True,
        ).inspect()
        self.assertTrue(preflight["ok"])
        self.assertFalse(preflight["adoptable"])
        gates = {item["gate"] for item in preflight["checks"]}
        for required in ("cgroup_v2", "cgroup_delegation", "user_namespaces",
                         "private_network", "nftables", "apparmor_enforcing", "seccomp"):
            self.assertIn(required, gates)

    def test_one_missing_effective_gate_blocks_before_mutation(self):
        from sandbox.isolation.preflight import IsolationPreflight
        result = IsolationPreflight(
            facts=self.facts, command_probe=lambda _command: True,
            effective_probe=lambda gate: gate != "nftables",
        ).inspect()
        self.assertFalse(result["ok"]); self.assertFalse(result["mutated"])
        self.assertEqual(result["reason"]["code"], "isolation_prerequisite_missing")
        self.assertIn("nftables", result["reason"]["missing"])

    def test_wrong_distribution_version_or_missing_binary_blocks(self):
        from sandbox.isolation.preflight import IsolationPreflight
        result = IsolationPreflight(
            facts=lambda: {"os_id": "ubuntu", "version_id": "22.04", "systemd_version": 249},
            command_probe=lambda command: command != "bwrap", effective_probe=lambda _gate: True,
        ).inspect()
        self.assertFalse(result["ok"])
        self.assertIn("platform", result["reason"]["missing"])
        self.assertIn("command:bwrap", result["reason"]["missing"])


if __name__ == "__main__": unittest.main()
