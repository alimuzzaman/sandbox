"""The ordinary identity port must enforce the pin recorded by join/refresh."""
import unittest
from types import SimpleNamespace
from unittest import mock

from sandbox.core import _remote as sr
from sandbox.resources.context import authenticated_target_identity
from sandbox.resources.host_memory.remote import HostMemoryRemote, RemoteProtocolError
from tests.test_remote_join_identity import evidence
from tests.test_remote_join import run


class JoinedMachinePinTest(unittest.TestCase):
    def adapter(self, request, *, pinned=True):
        record = {'mcp_service': {'ownership_marker': 'a' * 24, 'runtime_revision': 'b' * 24}}
        if pinned:
            record['control_identity'] = {'schema_version': 1, 'target_identity': 'c' * 24}
        return HostMemoryRemote('box', record, request)

    def history(self):
        value = evidence()
        value['result'] = {'requested_range': {}, 'observed_range': None, 'samples': [],
            'counts': {key: 0 for key in ('returned', 'valid', 'partial', 'failed', 'malformed', 'missing')},
            'freshness': 'missing', 'complete': True, 'truncated': False}
        return value

    def plan(self, target='c' * 24):
        return {'plan_id': 'a' * 64, 'operation': 'enable', 'target_identity': target,
            'service_ownership_marker': 'a' * 24, 'runtime_revision': 'b' * 24,
            'expires_at': '2026-11-11T00:00:00Z', 'observation_digest': 'd' * 64,
            'effective_policy': {'size_gib': 4}, 'intended_artifact_digests': [], 'rollback_scope': []}

    def test_pinned_history_refuses_changed_machine_before_history_dispatch(self):
        request = mock.Mock(return_value=evidence(identity='d' * 24))
        with self.assertRaises(RemoteProtocolError) as failure:
            self.adapter(request).call('host_memory_history', budget_seconds=15)
        self.assertEqual(failure.exception.code, 'remote_target_identity_mismatch')
        self.assertEqual([call.args[1]['action'] for call in request.call_args_list], ['host_memory_status'])

    def test_pinned_history_accepts_matching_machine_with_one_total_budget(self):
        request = mock.Mock(side_effect=[evidence(), self.history()])
        with mock.patch('sandbox.resources.host_memory.remote.time.monotonic', side_effect=[100, 104]):
            result = self.adapter(request).call('host_memory_history', budget_seconds=15)
        self.assertEqual(result['samples'], [])
        self.assertEqual(request.call_args_list[1].args[1]['budget_seconds'], 11)

    def test_pin_validation_budget_exhaustion_never_dispatches_apply(self):
        request = mock.Mock(return_value=evidence())
        with mock.patch('sandbox.resources.host_memory.remote.time.monotonic', side_effect=[100, 116]):
            with self.assertRaises(RemoteProtocolError) as failure:
                self.adapter(request).call('host_memory_apply', budget_seconds=15,
                    operation_id='e' * 64, plan=self.plan(), confirmed=True)
        self.assertEqual(failure.exception.code, 'remote_budget_exhausted')
        self.assertEqual(request.call_count, 1)

    def test_pinned_apply_rejects_foreign_plan_without_any_request(self):
        request = mock.Mock()
        with self.assertRaises(RemoteProtocolError) as failure:
            self.adapter(request).call('host_memory_apply', budget_seconds=15,
                operation_id='e' * 64, plan=self.plan('d' * 24), confirmed=True)
        self.assertEqual(failure.exception.code, 'remote_target_identity_mismatch')
        request.assert_not_called()

    def test_pinned_apply_checks_actual_machine_before_mutation(self):
        request = mock.Mock(return_value=evidence(identity='d' * 24))
        with self.assertRaises(RemoteProtocolError):
            self.adapter(request).call('host_memory_apply', budget_seconds=15,
                operation_id='e' * 64, plan=self.plan(), confirmed=True)
        self.assertEqual([call.args[1]['action'] for call in request.call_args_list], ['host_memory_status'])

    def test_matching_pinned_apply_dispatches_once_after_status(self):
        result = {**evidence(), 'result': {'status': 'already_current', 'operation_id': 'e' * 64}}
        request = mock.Mock(side_effect=[evidence(), result])
        self.assertEqual(self.adapter(request).call('host_memory_apply', budget_seconds=15,
            operation_id='e' * 64, plan=self.plan(), confirmed=True)['status'], 'already_current')
        self.assertEqual([call.args[1]['action'] for call in request.call_args_list], ['host_memory_status', 'host_memory_apply'])

    def test_legacy_unpinned_history_keeps_single_request(self):
        request = mock.Mock(return_value=self.history())
        self.adapter(request, pinned=False).call('host_memory_history', budget_seconds=15)
        self.assertEqual([call.args[1]['action'] for call in request.call_args_list], ['host_memory_history'])

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
