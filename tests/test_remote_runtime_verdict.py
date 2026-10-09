"""Control-protocol declaration and the single compatibility verdict (spec 061)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sandbox.remote_runtime import protocol as protocol_mod  # noqa: E402
from sandbox.remote_runtime.protocol import ControlProtocol, parse_protocol  # noqa: E402
from sandbox.remote_runtime import verdict as v  # noqa: E402

M = "a" * 24
F = "b" * 24


class ProtocolTests(unittest.TestCase):
    def test_local_declaration_is_a_valid_range(self):
        local = protocol_mod.local_protocol()
        self.assertGreaterEqual(local.spoken, local.oldest_served)
        self.assertEqual(parse_protocol(local.encode()), local)

    def test_unit_environment_line_round_trips(self):
        line = protocol_mod.unit_environment_line(ControlProtocol(3, 2))
        self.assertEqual(line, "Environment=SANDBOX_REMOTE_MCP_CONTROL_PROTOCOL=3:2")
        self.assertEqual(parse_protocol(line.split("=", 2)[2]), ControlProtocol(3, 2))

    def test_malformed_values_parse_to_none(self):
        for value in (None, "", "1", "0:0", "2:3", "x:1", "1:1:1", " 1 : 1", "-1:1", 5):
            with self.subTest(value=value):
                self.assertIsNone(parse_protocol(value))

    def test_invalid_ranges_are_rejected(self):
        for spoken, oldest in ((0, 1), (1, 2), (True, 1), (1.0, 1)):
            with self.subTest(spoken=spoken, oldest=oldest), self.assertRaises(ValueError):
                ControlProtocol(spoken, oldest)


class VerdictTests(unittest.TestCase):
    def verdict(self, local, installed, *, installed_revision=M, local_revision=F,
                determinate=True, strict=False):
        return v.compatibility(local_revision=local_revision,
                               installed_revision=installed_revision,
                               installed_protocol=installed, determinate=determinate,
                               strict=strict, local=local)

    def test_fixed_controller_state_set(self):
        cases = [
            ("same protocol, different revision", ControlProtocol(2, 1), ControlProtocol(2, 1), {}, v.COMPATIBLE, True),
            ("older but served", ControlProtocol(1, 1), ControlProtocol(3, 1), {}, v.COMPATIBLE, True),
            ("newer protocol", ControlProtocol(3, 1), ControlProtocol(2, 1), {}, v.PROTOCOL_NEWER, False),
            ("older than minimum", ControlProtocol(1, 1), ControlProtocol(3, 2), {}, v.PROTOCOL_TOO_OLD, False),
            ("undeclared runtime, different revision", ControlProtocol(1, 1), None, {}, v.EXACT_ONLY, False),
            ("undeclared runtime, same revision", ControlProtocol(1, 1), None, {"local_revision": M}, v.EXACT_ONLY, True),
            ("indeterminate status", ControlProtocol(1, 1), ControlProtocol(1, 1), {"determinate": False}, v.UNKNOWN, False),
            ("missing installed revision", ControlProtocol(1, 1), ControlProtocol(1, 1), {"installed_revision": None}, v.UNKNOWN, False),
            ("strict at a different revision", ControlProtocol(1, 1), ControlProtocol(1, 1), {"strict": True}, v.COMPATIBLE, False),
            ("strict at the same revision", ControlProtocol(1, 1), ControlProtocol(1, 1), {"strict": True, "local_revision": M}, v.COMPATIBLE, True),
        ]
        for name, local, installed, extra, state, ok in cases:
            with self.subTest(name):
                result = self.verdict(local, installed, **extra)
                self.assertEqual(result.state, state)
                self.assertIs(result.ok, ok)
                self.assertIn(result.state, v.STATES)

    def test_mapping_carries_both_sides(self):
        result = self.verdict(ControlProtocol(2, 1), ControlProtocol(1, 1))
        mapping = result.as_mapping()
        self.assertEqual(mapping["local"], {"revision": F, "protocol": {"spoken": 2, "oldest_served": 1}})
        self.assertEqual(mapping["installed"]["revision"], M)
        self.assertEqual(mapping["state"], v.PROTOCOL_NEWER)

    def test_strict_requested_by_flag_or_environment(self):
        self.assertTrue(v.strict_requested(True, {}))
        self.assertTrue(v.strict_requested(False, {"SANDBOX_STRICT_RUNTIME": "1"}))
        self.assertFalse(v.strict_requested(False, {"SANDBOX_STRICT_RUNTIME": "true"}))
        self.assertFalse(v.strict_requested(None, {}))


if __name__ == "__main__":
    unittest.main()
