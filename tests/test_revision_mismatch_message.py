"""Revision mismatch errors name both revisions so a matching checkout is findable."""

import unittest
from unittest.mock import patch

from sandbox.resources.context import _revision_mismatch_message, _require_compatible_runtime
from sandbox.resources.host_memory.remote import RemoteProtocolError

_RECORD = {"mcp_service": {"runtime_revision": "a" * 24}}


class RevisionMismatchMessageTests(unittest.TestCase):
    def test_names_short_revisions_and_inspection_command(self):
        message = _revision_mismatch_message("a" * 40, "b" * 40)
        self.assertIn("a" * 12, message)
        self.assertIn("b" * 12, message)
        self.assertNotIn("a" * 13, message)
        self.assertIn("sb remote service status", message)

    def test_non_hex_values_are_not_echoed(self):
        message = _revision_mismatch_message("token=secret", None)
        self.assertNotIn("secret", message)
        self.assertIn("unknown", message)

    def test_named_remote_gets_runnable_remedies_and_the_verdict(self):
        message = _revision_mismatch_message("a" * 40, "b" * 40, "vps", "protocol_newer")
        self.assertIn("./sb remote service status vps", message)
        self.assertIn("./sb remote service migrate vps --plan", message)
        self.assertIn("protocol_newer", message)
        self.assertNotIn("remote up", message)


class RequireCompatibleRuntimeTests(unittest.TestCase):
    def _run(self, local, status):
        probe = patch("sandbox.core._remote.remote_mcp_service_status",
                      side_effect=status if isinstance(status, Exception) else None,
                      return_value=None if isinstance(status, Exception) else status)
        with patch("sandbox.core._remote._remote_mcp_runtime_revision", return_value=local), \
                probe as probed:
            _require_compatible_runtime("vps", _RECORD)
        return probed

    def test_same_revision_skips_the_live_probe(self):
        self.assertFalse(self._run("a" * 24, {}).called)

    def test_compatible_different_revision_is_admitted(self):
        probed = self._run("b" * 24, {"runtime_revision_state": "mismatch",
                                      "compatibility": {"state": "compatible", "ok": True}})
        self.assertTrue(probed.called)

    def test_incompatible_or_unreachable_runtime_is_refused(self):
        for status in ({"runtime_revision_state": "mismatch",
                        "compatibility": {"state": "protocol_newer", "ok": False}},
                       RuntimeError("ssh token=secret")):
            with self.subTest(status=status), self.assertRaises(RemoteProtocolError) as caught:
                self._run("b" * 24, status)
            self.assertEqual(caught.exception.code, "remote_runtime_revision_mismatch")
            self.assertNotIn("secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
