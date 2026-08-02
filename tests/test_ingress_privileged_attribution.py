"""Root-owned incumbents need privileged, read-only listener attribution.

An unprivileged process cannot read `/proc/<pid>/fd` for a listener owned by
another user, so system Caddy under systemd -- 037's documented conformance
target -- was permanently `unidentified` and could never be selected (FR-001,
FR-002, FR-010).
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace


class Process:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode
        self.calls = []

    def run(self, argv, **_kwargs):
        self.calls.append(tuple(argv))
        return SimpleNamespace(stdout=self.stdout, stderr="", returncode=self.returncode)


HELPER_OUTPUT = """0.0.0.0 80 4242 caddy /usr/bin/caddy
:: 443 4242 caddy /usr/bin/caddy
127.0.0.1 9120 771 hermes -
"""


class TestHelperOutputParsing(unittest.TestCase):
    def test_fixed_shape_lines_become_evidence(self):
        from sandbox.ingress.listeners import parse_helper_listeners

        found = parse_helper_listeners(HELPER_OUTPUT)
        self.assertEqual(found[("0.0.0.0", 80)]["command"], "caddy")
        self.assertEqual(found[("0.0.0.0", 80)]["executable"], "/usr/bin/caddy")
        self.assertIsNone(found[("127.0.0.1", 9120)]["executable"])

    def test_malformed_lines_are_ignored(self):
        from sandbox.ingress.listeners import parse_helper_listeners

        self.assertEqual(parse_helper_listeners("garbage\n1 2 3\n"), {})


class TestPrivilegedAttribution(unittest.TestCase):
    @staticmethod
    def _endpoints():
        from sandbox.ingress.models import ListenerEndpoint

        return (ListenerEndpoint("0.0.0.0", 80, socket_id="1"),
                ListenerEndpoint("127.0.0.1", 9120, socket_id="2"))

    def _observer(self, process):
        from sandbox.ingress.listeners import ListenerObserver

        return ListenerObserver(platform="linux", process=process)

    def test_unattributed_endpoints_gain_process_evidence(self):
        process = Process(HELPER_OUTPUT)
        attributed = self._observer(process)._attribute_privileged(self._endpoints())

        by_port = {item.port: item for item in attributed}
        self.assertEqual(by_port[80].process["command"], "caddy")
        self.assertEqual(by_port[80].owner_confidence, "proven")
        self.assertEqual(by_port[9120].owner_confidence, "probable")
        self.assertEqual(process.calls[0][:2], ("sudo", "-n"))
        self.assertTrue(process.calls[0][-1] == "listeners")

    def test_already_attributed_endpoints_skip_the_helper(self):
        from sandbox.ingress.models import ListenerEndpoint

        process = Process(HELPER_OUTPUT)
        endpoints = (ListenerEndpoint("0.0.0.0", 80, socket_id="1",
                                      process={"pid": 1, "command": "nginx"},
                                      owner_confidence="probable"),)
        self._observer(process)._attribute_privileged(endpoints)
        self.assertEqual(process.calls, [])

    def test_helper_failure_leaves_observation_untouched(self):
        process = Process("", returncode=1)
        attributed = self._observer(process)._attribute_privileged(self._endpoints())
        self.assertTrue(all(item.process is None for item in attributed))

    def test_attribution_is_read_only(self):
        process = Process(HELPER_OUTPUT)
        self._observer(process)._attribute_privileged(self._endpoints())
        for call in process.calls:
            self.assertIn("listeners", call)
            for mutating in ("prepare", "activate", "cleanup", "rollback"):
                self.assertNotIn(mutating, call)


if __name__ == "__main__":
    unittest.main()
