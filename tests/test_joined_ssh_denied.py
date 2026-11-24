"""A control-admitted joined operator whose Tailscale SSH the server's policy refuses."""
import contextlib
import io
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from sandbox.commands import remote as command
from sandbox.core import _remote as sr
from sandbox.hosting import server_cli_dispatch
from sandbox.remote_runtime.execution_capabilities import ExecutionCapabilityUnavailable, adopt_execution_capabilities
from sandbox.transports.remote_jobs import RemoteJobTransport, TailnetSshDenied
from tests.test_remote_join import INVENTORY, TAILNET, run
from tests.test_remote_join_identity import evidence

POLICY_REFUSAL = SimpleNamespace(returncode=255, stdout='',
    stderr='tailscale: tailnet policy does not permit you to SSH to this node\n')
JOINED = {'joined': True, 'provisioned': True, 'control_transport': 'tailscale',
          'control_access': 'tailnet', 'ssh': f'alim@{TAILNET}', 'tailscale_host': TAILNET,
          'control_identity': {'schema_version': 1, 'target_identity': 'c' * 24},
          'mcp_service': {'ownership_marker': 'a' * 24, 'runtime_revision': 'b' * 24}}


class JoinWithoutSshTest(unittest.TestCase):
    def join(self, front_door):
        self.put = mock.Mock(side_effect=lambda name, **fields: fields)
        return run(command._cmd_join, SimpleNamespace(name='box', ssh_url=f'alim@{TAILNET}',
            front_door=front_door), {(sr, 'get_remote'): mock.Mock(return_value=None),
                (sr, '_remote_control_request'): mock.Mock(return_value=INVENTORY),
                (sr, 'remote_host_memory_request'): mock.Mock(return_value=evidence()),
                (command, '_tailnet_address'): mock.Mock(return_value=TAILNET),
                (command, '_first_ssh_login'): mock.Mock(return_value='denied'),
                (command, '_joined_profile_evidence'): mock.Mock(side_effect=AssertionError('SSH probe')),
                (sr, 'put_remote'): self.put})

    def test_control_admitted_join_succeeds_when_ssh_is_denied(self):
        code, result = self.join('nginx')
        self.assertEqual((code, result['status']), (0, 'joined'))
        self.assertEqual(result['ssh_access'], {'state': 'denied', 'transport': 'tailscale'})
        self.assertIsNone(result['front_door'])
        self.assertEqual(result['ssh_check'], f'tailscale ssh alim@{TAILNET} true')
        self.assertIn('job-start', result['next_step'])
        self.assertIn('remote service status box', result['next_step'])
        fields = self.put.call_args.kwargs
        self.assertEqual((fields['joined'], fields['control_access']), (True, 'tailnet'))
        self.assertNotIn('front_door', fields)
        self.assertEqual(fields['control_identity']['target_identity'], 'c' * 24)

    def test_an_unsupported_profile_still_refuses_before_any_write(self):
        code, _ = self.join('caddy')
        self.assertNotEqual(code, 0)
        self.put.assert_not_called()


class SshOnlyCommandRefusalTest(unittest.TestCase):
    def transport(self, result):
        return RemoteJobTransport(deploy=mock.Mock(side_effect=AssertionError('no deploy')),
            ssh_run=mock.Mock(return_value=result), remote_lookup=lambda name: dict(JOINED),
            remote_sb_path=lambda remote: 'sb')

    def test_job_control_names_the_ssh_refusal_and_the_check(self):
        with self.assertRaises(TailnetSshDenied) as failure:
            self.transport(POLICY_REFUSAL).control('box', ['job-status', 'f' * 16])
        error = failure.exception
        self.assertEqual(error.code, 'tailnet_ssh_denied')
        self.assertFalse(error.retryable)
        self.assertIn(f'tailscale ssh alim@{TAILNET} true', str(error))
        self.assertIn('remote service status', str(error))

    def test_job_status_reports_the_refusal_code_not_unknown_transport(self):
        payload = self.transport(POLICY_REFUSAL).status('box', 'f' * 16)
        self.assertEqual((payload['code'], payload['retryable']), ('tailnet_ssh_denied', False))
        self.assertIn('tailnet SSH policy', payload['error'])

    def test_a_remote_command_exit_is_never_mistaken_for_an_ssh_refusal(self):
        executed = SimpleNamespace(returncode=1, stdout='', stderr='Permission denied: /x\n')
        with self.assertRaises(Exception) as failure:
            self.transport(executed).control('box', ['job-status', 'f' * 16])
        self.assertNotIsInstance(failure.exception, TailnetSshDenied)

    def test_job_start_capability_discovery_keeps_the_refusal(self):
        transport = self.transport(POLICY_REFUSAL)
        with mock.patch('sandbox.core._remote.put_remote') as writer, \
                self.assertRaises(ExecutionCapabilityUnavailable) as failure:
            adopt_execution_capabilities('box', dict(JOINED), lambda argv: transport.control('box', argv))
        self.assertEqual(failure.exception.code, 'tailnet_ssh_denied')
        self.assertIn('tailscale ssh', str(failure.exception))
        writer.assert_not_called()

    def test_host_stack_dispatch_reports_not_accepted_refusal(self):
        args = SimpleNamespace(json=True, request_id='req-1', _server_cli_submission_started=True)
        out = io.StringIO()
        refusal = TailnetSshDenied('Tailscale SSH to alim@100.64.0.9 is refused')
        with mock.patch.object(server_cli_dispatch, '_dispatch_host_if_selected', side_effect=refusal), \
                mock.patch('sandbox.hosting.server_cli_here.claims', return_value=False), \
                mock.patch('sandbox.application.server_cli_source_repair.dispatch_if_selected', return_value=False), \
                contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            server_cli_dispatch.dispatch_host_if_selected(args)
        payload = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual((payload['code'], payload['acceptance']), ('tailnet_ssh_denied', 'not_accepted'))
        self.assertIn('refused', payload['error'])


if __name__ == '__main__':
    unittest.main()
