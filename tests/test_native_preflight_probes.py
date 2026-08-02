"""Isolation preflight probes must not fail on kernel bookkeeping (039 T047)."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


def _helper():
    path = (Path(__file__).resolve().parents[1] / "tools" / "native-helper"
            / "native-helper.py")
    spec = importlib.util.spec_from_file_location("native_helper_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestIpv6DefaultRouteDetection(unittest.TestCase):
    UNREACHABLE = ("0" * 32 + " 00 " + "0" * 32 + " 00 " + "0" * 32
                   + " ffffffff 00000001 00000000 00200200       lo")
    REAL_DEFAULT = ("0" * 32 + " 00 " + "0" * 32 + " 00 "
                    + "fe800000000000000000000000000001"
                    + " 00000400 00000001 00000000 00000003       eth0")
    LOOPBACK_HOST = ("0" * 31 + "1 80 " + "0" * 32 + " 00 " + "0" * 32
                     + " 00000000 00000002 00000000 80200001       lo")

    def test_kernel_unreachable_default_is_not_connectivity(self):
        self.assertFalse(_helper().ipv6_default_route(self.UNREACHABLE))

    def test_a_usable_default_route_is_detected(self):
        self.assertTrue(_helper().ipv6_default_route(self.REAL_DEFAULT))

    def test_non_default_prefixes_are_ignored(self):
        self.assertFalse(_helper().ipv6_default_route(self.LOOPBACK_HOST))

    def test_malformed_rows_are_ignored(self):
        for row in ("", "garbage", "0" * 32 + " 00"):
            with self.subTest(row=row):
                self.assertFalse(_helper().ipv6_default_route(row))


if __name__ == "__main__":
    unittest.main()


class TestScopeProbeCommandShape(unittest.TestCase):
    """systemd refuses `--wait` together with `--scope`, so the
    cgroup-delegation gate could never pass while both were passed."""

    def test_scope_probe_does_not_pass_wait(self):
        from pathlib import Path

        source = (Path(__file__).resolve().parents[1] / "tools" / "native-helper"
                  / "native-helper.py").read_text()
        block = source.split("if probe == \"private-network\":", 1)[1].split("command.extend((str(", 1)[0]
        scope_branch = block.split("else:", 1)[1]
        arguments = [line for line in scope_branch.splitlines()
                     if "command.extend" in line]
        self.assertTrue(arguments)
        self.assertIn("--scope", arguments[0])
        self.assertNotIn("--wait", arguments[0])

    def test_waiting_probes_still_wait(self):
        from pathlib import Path

        source = (Path(__file__).resolve().parents[1] / "tools" / "native-helper"
                  / "native-helper.py").read_text()
        block = source.split("if probe == \"private-network\":", 1)[1].split("else:", 1)[0]
        self.assertIn("--wait", block)
