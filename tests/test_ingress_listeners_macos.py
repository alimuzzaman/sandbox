from __future__ import annotations

import unittest


class TestMacIngressListeners(unittest.TestCase):
    def test_lsof_normalizes_exact_wildcard_and_partial_process_evidence(self):
        from sandbox.ingress.listeners import parse_macos_lsof
        values = parse_macos_lsof(
            "p123\ncnginx\nn127.0.0.1:80\n"
            "p456\nn*:443\n"
        )
        self.assertEqual([(item.address, item.port) for item in values], [
            ("127.0.0.1", 80), ("0.0.0.0", 443),
        ])
        self.assertEqual(values[0].process["command"], "nginx")
        self.assertIsNone(values[1].process["command"])


if __name__ == "__main__":
    unittest.main()
