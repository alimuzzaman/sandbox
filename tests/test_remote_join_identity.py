"""Joined-controller discovery uses authenticated evidence, never owner credentials."""
import contextlib
import io
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from sandbox.commands import remote as command
from sandbox.core import _remote as sr
from tests.host_memory_fixtures import status_state
from tests.test_remote_join import INVENTORY, TAILNET, run


def evidence(marker="a" * 24, revision="b" * 24, identity="c" * 24):
    return {"resource_schema": 1, "host_memory_schema": 1, "transport": "control",
            "service": {"ownership_marker": marker, "runtime_revision": revision},
            "result": status_state(target_identity=identity)}


class JoinedIdentityTest(unittest.TestCase):
    def invoke(self, *, refresh=False, previous=None, observed=None):
        entry = {"ssh": f"user@{TAILNET}", "control_transport": "tailscale",
                 "control_url": f"http://{TAILNET}:9174", "tailscale_host": TAILNET,
                 "mcp_port": 9174, "control_access": "tailnet", "joined": True,
                 "provisioned": True}
        entry.update(previous or {})
        self.put = mock.Mock()
        self.probe = mock.Mock(return_value=observed if observed is not None else evidence())
        patches = {(sr, "get_remote"): mock.Mock(return_value=entry if refresh else None),
                   (sr, "put_remote"): self.put,
                   (sr, "registered_remote_lock"): lambda *a, **k: contextlib.nullcontext(),
                   (sr, "_remote_control_request"): mock.Mock(return_value=INVENTORY),
                   (sr, "remote_host_memory_request"): self.probe,
                   (command, "_first_ssh_login"): mock.Mock(return_value="ok"),
                   (command, "_tailnet_address"): mock.Mock(return_value=TAILNET)}
        args = SimpleNamespace(name="refresh" if refresh else "box",
                               ssh_url="box" if refresh else f"user@{TAILNET}")
        return run(command._cmd_service if refresh else command._cmd_join, args, patches)

    def test_join_records_service_and_machine_evidence(self):
        code, result = self.invoke()
        self.assertEqual(code, 0)
        fields = self.put.call_args.kwargs
        self.assertEqual(fields["mcp_service"]["ownership_marker"], "a" * 24)
        self.assertEqual(fields["mcp_service"]["runtime_revision"], "b" * 24)
        self.assertEqual(fields["control_identity"]["target_identity"], "c" * 24)
        self.assertNotIn("bearer_token", fields)
        self.assertEqual(self.probe.call_args.args[1]["action"], "host_memory_status")

    def test_existing_join_can_refresh_missing_evidence(self):
        code, result = self.invoke(refresh=True)
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "refreshed")
        self.assertEqual(self.put.call_args.kwargs["control_identity"]["target_identity"], "c" * 24)

    def test_refresh_never_replaces_an_existing_pin(self):
        for previous in ({"mcp_service": {"ownership_marker": "d" * 24}},
                         {"mcp_service": {"runtime_revision": "d" * 24}},
                         {"control_identity": {"schema_version": 1, "target_identity": "d" * 24}}):
            with self.subTest(previous=previous):
                code, _ = self.invoke(refresh=True, previous=previous)
                self.assertNotEqual(code, 0)
                self.put.assert_not_called()

    def test_invalid_service_or_machine_evidence_does_not_register(self):
        for observed in (evidence(marker="unknown"), evidence(revision="unknown"),
                         evidence(identity="not-a-machine-id"), {"ok": True}):
            with self.subTest(observed=observed):
                code, _ = self.invoke(observed=observed)
                self.assertNotEqual(code, 0)
                self.put.assert_not_called()

    def test_refresh_preserves_other_registration_fields(self):
        code, _ = self.invoke(refresh=True, previous={"custom": "keep", "runtime_policy": "strict"})
        self.assertEqual(code, 0)
        self.assertNotIn("custom", self.put.call_args.kwargs)
        self.assertNotIn("ssh", self.put.call_args.kwargs)

    def test_matching_pins_and_service_metadata_survive_refresh(self):
        previous = {"mcp_service": {"ownership_marker": "a" * 24,
                                    "runtime_revision": "b" * 24, "bind": TAILNET},
                    "control_identity": {"schema_version": 1, "target_identity": "c" * 24}}
        code, _ = self.invoke(refresh=True, previous=previous)
        self.assertEqual(code, 0)
        self.assertEqual(self.put.call_args.kwargs["mcp_service"], previous["mcp_service"])

    def test_refresh_refuses_nonjoined_or_non_tailnet(self):
        for previous in ({"joined": False}, {"control_access": "bearer"}):
            code, _ = self.invoke(refresh=True, previous=previous)
            self.assertNotEqual(code, 0)
            self.probe.assert_not_called()
            self.put.assert_not_called()

    def test_partial_unmanaged_evidence_and_invalid_digest(self):
        for state in ("partial", "unmanaged"):
            observed = evidence()
            observed["result"] = status_state(target_identity="c" * 24, evidence_state=state)
            self.assertEqual(self.invoke(observed=observed)[0], 0)
        observed = evidence()
        observed["result"]["observation_digest"] = "0" * 64
        self.assertNotEqual(self.invoke(observed=observed)[0], 0)
        self.put.assert_not_called()

    def test_refresh_reports_concurrent_change_without_success(self):
        entry = {"joined": True, "control_access": "tailnet"}
        with mock.patch.object(command, "_first_ssh_login", side_effect=AssertionError("SSH forbidden")):
            code, result = run(command._cmd_service, SimpleNamespace(name="refresh", ssh_url="box"), {
                (sr, "get_remote"): mock.Mock(return_value=entry),
                (sr, "remote_host_memory_request"): mock.Mock(return_value=evidence()),
                (sr, "put_remote"): mock.Mock(side_effect=RuntimeError("remote_registration_changed"))})
        self.assertNotEqual(code, 0)
        self.assertFalse(result["ok"])

    def test_actual_refresh_transport_is_tokenless_status_only(self):
        entry = {"joined": True, "control_access": "tailnet", "control_transport": "tailscale",
                 "control_url": f"http://{TAILNET}:9174", "tailscale_host": TAILNET}
        response = io.BytesIO(json.dumps(evidence()).encode())
        response.status = 200
        opener = mock.Mock(return_value=response)
        with mock.patch.object(sr, "_control_urlopen", opener), \
                mock.patch.object(sr, "ssh_run", side_effect=AssertionError("SSH forbidden")):
            code, result = run(command._cmd_service, SimpleNamespace(name="refresh", ssh_url="box"), {
                (sr, "get_remote"): mock.Mock(return_value=entry),
                (sr, "put_remote"): mock.Mock()})
        self.assertEqual(code, 0)
        self.assertTrue(result["ok"])
        request = opener.call_args.args[1]
        self.assertEqual(request.full_url, f"http://{TAILNET}:9174/resources")
        self.assertEqual(request.method, "POST")
        self.assertNotIn("Authorization", request.headers)
        self.assertEqual(json.loads(request.data)["action"], "host_memory_status")

    def test_adoption_does_not_bypass_downstream_compatibility(self):
        from sandbox.resources import context
        from sandbox.resources.host_memory.remote import RemoteProtocolError
        entry = {"mcp_service": {"runtime_revision": "b" * 24}}
        with mock.patch.object(sr, "_remote_mcp_runtime_revision", return_value="d" * 24), \
                mock.patch.object(sr, "remote_runtime_status_for_operation", return_value=None), \
                mock.patch("sandbox.remote_runtime.verdict.strict_requested", return_value=False):
            with self.assertRaises(RemoteProtocolError):
                context._require_compatible_runtime("box", entry)


class RegistrationCompareTest(unittest.TestCase):
    def write(self, existing, expected):
        self.writer = mock.Mock()
        with mock.patch.object(sr, "registered_remote_lock", return_value=contextlib.nullcontext()), \
                mock.patch.object(sr, "_registry_write_lock", return_value=contextlib.nullcontext()), \
                mock.patch.object(sr, "_remote_block", return_value={"box": existing} if existing else {}), \
                mock.patch.object(sr, "_write_remote_block", self.writer):
            return sr.put_remote("box", _expected=expected, control_identity={"test": "new"})

    def test_registration_changed_during_probe_is_not_overwritten(self):
        with self.assertRaisesRegex(RuntimeError, "remote_registration_changed"):
            self.write({"ssh": "changed"}, {"ssh": "original"})
        self.writer.assert_not_called()

    def test_new_join_cannot_overwrite_a_concurrent_join(self):
        with self.assertRaisesRegex(RuntimeError, "remote_registration_changed"):
            self.write({"ssh": "newly-joined"}, None)
        self.writer.assert_not_called()

    def test_internal_record_name_is_not_a_persisted_comparison_field(self):
        result = self.write({"ssh": "original", "policy": "keep"},
                            {"ssh": "original", "policy": "keep", "_remote_name": "box"})
        self.assertEqual(result["policy"], "keep")
        self.writer.assert_called_once()


if __name__ == "__main__":
    unittest.main()
