"""Legacy Herd route operations remain in A's compatibility facade."""

from pathlib import Path
import subprocess
import importlib
import unittest
from unittest import mock


class TestHerdIngressHandoff(unittest.TestCase):
    def test_core_facade_preserves_cwd_and_documented_route_command_parity(self):
        herd = importlib.import_module("sandbox.core._herd")

        calls = []
        def run(*args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0, "", "")

        with mock.patch.object(herd, "_herd_cli", return_value="/usr/local/bin/herd"), \
                mock.patch.object(herd, "_herd", side_effect=run):
            facade = herd._herd_ingress_facade(cwd=Path("/tmp/site"))
            provisioned = facade.provision("demo", secure=True)
            cleaned = facade.cleanup("demo")
        self.assertTrue(provisioned["ok"]); self.assertTrue(cleaned["ok"])
        self.assertEqual(calls, [
            (("link", "demo"), {"cwd": Path("/tmp/site")}),
            (("secure", "demo"), {"cwd": Path("/tmp/site")}),
            (("unsecure", "demo"), {"cwd": Path("/tmp/site")}),
            (("unlink", "demo"), {"cwd": Path("/tmp/site")}),
        ])

    def test_incumbent_runtime_adapter_cannot_mutate_herd_routes(self):
        from sandbox.runtimes.incumbent.herd import HerdAdapter

        source = HerdAdapter.invoke.__code__.co_names
        self.assertNotIn("link", source)
        self.assertNotIn("secure", source)
        self.assertNotIn("unlink", source)


if __name__ == "__main__": unittest.main()
