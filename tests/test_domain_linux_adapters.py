from __future__ import annotations

import unittest


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


if __name__ == "__main__":
    unittest.main()
