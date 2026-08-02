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

    def test_privileged_effective_gates_require_exact_helper_json(self):
        from sandbox.application.context import _native_helper_effective_probe

        class Process:
            def __init__(self, stdout, returncode=0):
                self.stdout = stdout; self.returncode = returncode; self.calls = []
            def run(self, argv, **kwargs):
                self.calls.append((argv, kwargs))
                return type("Result", (), {"returncode": self.returncode,
                                            "stdout": self.stdout})()

        for gate, probe in (("private_network", "private-network"),
                            ("nftables", "nftables"),
                            ("cgroup_delegation", "cgroup-delegation")):
            with self.subTest(gate=gate):
                process = Process('{"ok":true,"probe":"' + probe + '","state":"ready"}')
                self.assertTrue(_native_helper_effective_probe(process, "/fixed/helper", gate))
                self.assertEqual(process.calls[0], (("sudo", "-n", "/fixed/helper",
                                                     "preflight-probe", probe),
                                                    {"timeout": 20}))
        for payload in ('not-json', '{"ok":true,"probe":"nftables","state":"ready","extra":1}',
                        '{"ok":true,"probe":"wrong","state":"ready"}'):
            with self.subTest(payload=payload):
                self.assertFalse(_native_helper_effective_probe(
                    Process(payload), "/fixed/helper", "nftables"))


if __name__ == "__main__": unittest.main()
