"""Shared mismatch refusal and remedy commands that the CLI accepts (spec 061)."""
import argparse
import shlex
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sandbox.remote_runtime import refusal as r  # noqa: E402
from sandbox.remote_runtime import verdict as v  # noqa: E402
from sandbox.remote_runtime.protocol import ControlProtocol  # noqa: E402


class _Parsed(Exception):
    def __init__(self, namespace):
        super().__init__("parsed")
        self.namespace = namespace


def parse_with_cli(command: str) -> argparse.Namespace:
    """Parse a remedy with the real CLI parser, stopping before dispatch."""
    import sandbox.cli as cli

    argv = shlex.split(command)
    if argv[0] != "./sb":
        raise AssertionError(f"remedy does not start with ./sb: {command}")
    real = argparse.ArgumentParser.parse_args

    def capture(self, args=None, namespace=None):
        if self.prog != "sandbox":
            return real(self, args, namespace)
        raise _Parsed(real(self, args, namespace))

    with patch.object(sys, "argv", ["sb", *argv[1:]]), \
            patch.object(argparse.ArgumentParser, "parse_args", capture):
        try:
            cli.main()
        except _Parsed as parsed:
            return parsed.namespace
        except SystemExit as exc:
            raise AssertionError(f"CLI rejected remedy {command!r}: exit {exc.code}") from None
    raise AssertionError("CLI never parsed the remedy")


def _verdicts():
    def make(state_local, state_installed, **extra):
        return v.compatibility(local_revision=extra.get("local_revision", "b" * 24),
                               installed_revision=extra.get("installed_revision", "a" * 24),
                               installed_protocol=state_installed,
                               determinate=extra.get("determinate", True),
                               strict=extra.get("strict", False), local=state_local)
    return [
        make(ControlProtocol(2, 1), ControlProtocol(1, 1)),
        make(ControlProtocol(1, 1), ControlProtocol(3, 2)),
        make(ControlProtocol(1, 1), None),
        make(ControlProtocol(1, 1), ControlProtocol(1, 1), determinate=False),
        make(ControlProtocol(1, 1), ControlProtocol(1, 1), strict=True),
    ]


class RefusalTests(unittest.TestCase):
    def test_every_remedy_parses_and_names_the_remote(self):
        for verdict in _verdicts():
            error = r.refusal(verdict, "xcloud-london")
            self.assertTrue(error["remedies"])
            for remedy in error["remedies"]:
                with self.subTest(state=verdict.state, remedy=remedy):
                    self.assertNotIn("<", remedy)
                    self.assertNotIn("NAME", remedy)
                    namespace = parse_with_cli(remedy)
                    self.assertEqual(namespace.cmd, "remote")
                    self.assertEqual(namespace.action, "service")
                    self.assertEqual(namespace.ssh_url, "xcloud-london")

    def test_parser_check_rejects_the_old_hint(self):
        with self.assertRaises(AssertionError):
            parse_with_cli("./sb remote up --remote xcloud-london")

    def test_refusal_shape_is_complete(self):
        for verdict in _verdicts():
            error = r.refusal(verdict, "xcloud-london")
            with self.subTest(state=verdict.state):
                self.assertEqual(error["code"], r.MISMATCH_CODE)
                self.assertEqual(error["verdict"], verdict.state)
                for side in ("local", "installed"):
                    self.assertIn("revision", error[side])
                    self.assertIn("protocol", error[side])
                self.assertIn(verdict.state, r.refusal_text(error))

    def test_unknown_remedies_are_status_and_diagnostics(self):
        verdict = _verdicts()[3]
        self.assertEqual(r.refusal(verdict, "r1")["remedies"], [
            "./sb remote service status r1", "./sb remote service diagnostics r1"])

    def test_broken_pin_is_reported(self):
        error = r.refusal(_verdicts()[4], "r1", broken_pin={
            "broken_by": "h-0123456789abcdef", "broken_at": "2026-10-09T10:00:00+00:00"})
        self.assertIn("broken by h-0123456789abcdef", r.refusal_text(error))


if __name__ == "__main__":
    unittest.main()
