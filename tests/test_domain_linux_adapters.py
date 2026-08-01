from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest


class Process:
    def __init__(self, returncodes=()):
        self.calls = []
        self.returncodes = iter(returncodes)

    def run(self, argv, *, timeout):
        self.calls.append((tuple(argv), timeout))
        try:
            code = next(self.returncodes)
        except StopIteration:
            code = 0
        return subprocess.CompletedProcess(argv, code, "", "failed" if code else "")


class TestLinuxDomainAdapters(unittest.TestCase):
    def test_hosts_adapter_is_exact_only_and_refuses_wildcard(self):
        from sandbox.network.adapters.hosts import HostsAdapter

        adapter = HostsAdapter(helper="/fixed/helper", process=object())
        self.assertEqual(adapter.plan("demo.test", "127.0.0.77")["kind"], "exact")
        with self.assertRaisesRegex(ValueError, "wildcard"):
            adapter.plan("*.demo.test", "127.0.0.77")

    def test_networkmanager_resolved_mode_delegates_to_resolved_contract(self):
        from sandbox.network.adapters.networkmanager import NetworkManagerAdapter

        delegate = type("Delegate", (), {"plan": lambda self, *args: {"delegate": args}})()
        adapter = NetworkManagerAdapter(delegate)
        self.assertEqual(adapter.plan("test", "127.0.0.54", 5300)["delegate"],
                         ("test", "127.0.0.54", 5300))

    def test_direct_dnsmasq_requires_declared_owned_directory(self):
        from sandbox.network.adapters.dnsmasq import DnsmasqAdapter

        with self.assertRaisesRegex(ValueError, "owned"):
            DnsmasqAdapter(config_directory="/etc/dnsmasq.d", owned_directory=None,
                           process=object())

    def test_dnsmasq_validates_complete_config_and_rolls_back_failed_reload(self):
        from sandbox.network.adapters.dnsmasq import DnsmasqAdapter

        with tempfile.TemporaryDirectory() as tmp:
            include = Path(tmp) / "includes"
            owned = include / "sandbox"
            process = Process((0, 0, 1, 0))
            adapter = DnsmasqAdapter(
                config_directory=str(include), owned_directory=str(owned),
                process=process, validate_argv=("dnsmasq", "--test"),
                reload_argv=("reload",),
            )
            plan = adapter.plan("demo.test", "127.0.0.77")
            result = adapter.apply(plan)
            target = Path(plan["path"])
            self.assertFalse(result["ok"])
            self.assertFalse(target.exists())
        self.assertEqual([call[0] for call in process.calls], [
            ("dnsmasq", "--test"), ("dnsmasq", "--test"),
            ("reload",), ("reload",),
        ])

    def test_dnsmasq_refuses_foreign_fragment_without_mutation(self):
        from sandbox.network.adapters.dnsmasq import DnsmasqAdapter

        with tempfile.TemporaryDirectory() as tmp:
            include = Path(tmp) / "includes"
            owned = include / "sandbox"
            process = Process()
            adapter = DnsmasqAdapter(
                config_directory=str(include), owned_directory=str(owned), process=process,
            )
            plan = adapter.plan("demo.test", "127.0.0.77")
            target = Path(plan["path"])
            target.parent.mkdir(parents=True)
            target.write_text("address=/foreign.test/127.0.0.1\n")
            result = adapter.apply(plan)
            self.assertFalse(result["ok"])
            self.assertEqual(target.read_text(), "address=/foreign.test/127.0.0.1\n")
            self.assertEqual(process.calls, [])

    def test_hosts_apply_and_rollback_use_only_fixed_helper_verbs(self):
        from sandbox.network.adapters.hosts import HostsAdapter

        process = Process()
        adapter = HostsAdapter(helper="/fixed/helper", process=process)
        plan = adapter.plan("demo.test", "127.0.0.77")
        self.assertTrue(adapter.apply(plan)["ok"])
        self.assertTrue(adapter.rollback(plan)["ok"])
        self.assertEqual(process.calls[0][0], (
            "sudo", "-n", "/fixed/helper", "hosts-apply", "demo.test", "127.0.0.77",
        ))
        self.assertEqual(process.calls[1][0], (
            "sudo", "-n", "/fixed/helper", "hosts-remove", "demo.test", "127.0.0.77",
        ))


if __name__ == "__main__":
    unittest.main()
