"""The ordinary identity port must enforce the pin recorded by join/refresh."""
import unittest
from types import SimpleNamespace
from unittest import mock

from sandbox.core import _remote as sr
from sandbox.resources.context import authenticated_target_identity
from sandbox.resources.host_memory.remote import RemoteProtocolError
from tests.test_remote_join_identity import evidence
from tests.test_remote_join import run


class JoinedMachinePinTest(unittest.TestCase):
    def observe(self, pin, identity="c" * 24):
        record = {"mcp_service": {"ownership_marker": "a" * 24, "runtime_revision": "b" * 24},
                  "control_identity": pin}
        with mock.patch.object(sr, "get_remote", return_value=record), \
                mock.patch.object(sr, "_remote_mcp_runtime_revision", return_value="b" * 24), \
                mock.patch("sandbox.remote_runtime.verdict.strict_requested", return_value=False), \
                mock.patch.object(sr, "remote_host_memory_request", return_value=evidence(identity=identity)):
            return authenticated_target_identity("box")

    def test_matching_pin_returns_authenticated_identity(self):
        self.assertEqual(self.observe({"schema_version": 1, "target_identity": "c" * 24})[
            "target_identity"], "c" * 24)

    def test_changed_machine_refuses_ordinary_identity_lookup(self):
        with self.assertRaises(RemoteProtocolError) as failure:
            self.observe({"schema_version": 1, "target_identity": "c" * 24}, identity="d" * 24)
        self.assertEqual(failure.exception.code, "remote_target_identity_mismatch")

    def test_malformed_pin_cannot_downgrade_to_unpinned_lookup(self):
        for pin in (None, {}, "c" * 24, {"schema_version": 1, "target_identity": "bad"},
                    {"schema_version": True, "target_identity": "c" * 24}):
            with self.subTest(pin=pin), self.assertRaises(RemoteProtocolError):
                self.observe(pin)

    def test_legacy_owner_without_control_identity_keeps_existing_behavior(self):
        record = {"mcp_service": {"ownership_marker": "a" * 24, "runtime_revision": "b" * 24}}
        with mock.patch.object(sr, "get_remote", return_value=record), \
                mock.patch.object(sr, "_remote_mcp_runtime_revision", return_value="b" * 24), \
                mock.patch("sandbox.remote_runtime.verdict.strict_requested", return_value=False), \
                mock.patch.object(sr, "remote_host_memory_request", return_value=evidence()):
            self.assertEqual(authenticated_target_identity("box")["target_identity"], "c" * 24)

    def test_refreshed_join_uses_existing_compatible_status_when_hashes_differ(self):
        from sandbox.commands import remote as command
        from sandbox.remote_runtime.protocol import local_protocol
        from sandbox.remote_runtime.verdict import compatibility
        record = {"joined": True, "control_access": "tailnet"}
        verdict = compatibility(local_revision="d" * 24, installed_revision="b" * 24,
                                installed_protocol=local_protocol()).as_mapping()
        def put(_name, *, _expected, **fields):
            record.update(fields)
        with mock.patch.object(sr, "get_remote", side_effect=lambda _name: dict(record)), \
                mock.patch.object(sr, "put_remote", side_effect=put), \
                mock.patch.object(sr, "_remote_mcp_runtime_revision", return_value="d" * 24), \
                mock.patch("sandbox.remote_runtime.verdict.strict_requested", return_value=False), \
                mock.patch.object(sr, "remote_host_memory_request", side_effect=lambda *a: evidence()), \
                mock.patch.object(sr, "remote_mcp_service_status", return_value={
                    "runtime_revision_state": "mismatch", "compatibility": verdict}) as status:
            code, result = run(command._cmd_service, SimpleNamespace(name="refresh", ssh_url="box"), {})
            self.assertEqual(code, 0)
            self.assertEqual(result["status"], "refreshed")
            self.assertEqual(authenticated_target_identity("box")["target_identity"], "c" * 24)
            status.assert_called_once()
