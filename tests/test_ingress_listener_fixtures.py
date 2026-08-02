"""The shared listener topology fixtures remain valid public observations."""

import json
from pathlib import Path
import unittest


class TestIngressListenerFixtures(unittest.TestCase):
    def test_documented_topologies_normalize_without_host_access(self):
        from sandbox.ingress.models import ListenerEndpoint

        root = Path(__file__).parent / "host_fixtures" / "ingress"
        expected = {
            "free.json", "exact-loopback.json", "dedicated-loopback.json",
            "wildcard-ipv4.json", "wildcard-ipv6-dual-stack.json", "split-owner.json",
            "sandbox-owned.json",
        }
        self.assertEqual({path.name for path in root.glob("*.json")}, expected)
        for path in sorted(root.glob("*.json")):
            with self.subTest(path=path.name):
                fixture = json.loads(path.read_text())
                self.assertIsInstance(fixture["description"], str)
                endpoints = tuple(ListenerEndpoint(**item) for item in fixture["listeners"])
                self.assertTrue(all(endpoint.protocol == "tcp" for endpoint in endpoints))


if __name__ == "__main__": unittest.main()
