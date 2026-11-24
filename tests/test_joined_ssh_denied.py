"""A control-admitted joined operator whose Tailscale SSH the server's policy refuses."""
import contextlib
import io
import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest import mock

from sandbox import cli
from sandbox.application import server_cli
from sandbox.commands import jobs_runtime
from sandbox.commands import remote as command
from sandbox.core import _remote as sr
from sandbox.hosting import server_cli_dispatch
from sandbox.hosting.front_door import FrontDoorError, nginx
from sandbox.remote_runtime import join_adoption, tailnet_ssh
from sandbox.remote_runtime.execution_capabilities import ExecutionCapabilityUnavailable, adopt_execution_capabilities
from sandbox.transports.remote_jobs import (
    RemoteJobTransport, RemoteJobTransportError, TailnetSshCheckRequired, TailnetSshDenied)
from tests.test_remote_join import INVENTORY, TAILNET, run
from tests.test_remote_join_identity import evidence

POLICY_REFUSAL = SimpleNamespace(returncode=255, stdout='',
    stderr='tailscale: tailnet policy does not permit you to SSH to this node\n')
CHECK_PROMPT = (b'# Tailscale SSH requires an additional check.\n'
                b'# To authenticate, visit: https://login.tailscale.com/a/0123456789\n')
HOME = SimpleNamespace(returncode=0, stdout='/home/alim/sandbox\n', stderr='')
OK = SimpleNamespace(returncode=0, stdout='', stderr='')
JOINED = {'joined': True, 'provisioned': True, 'control_transport': 'tailscale',
          'control_access': 'tailnet', 'ssh': f'alim@{TAILNET}', 'tailscale_host': TAILNET,
          'control_url': f'http://{TAILNET}:9174', 'mcp_port': 9174,
          'control_identity': {'schema_version': 1, 'target_identity': 'c' * 24},
          'mcp_service': {'ownership_marker': 'a' * 24, 'runtime_revision': 'b' * 24}}
CHECK = f'tailscale ssh alim@{TAILNET} true'
NGINX = {'nginx_installed': True, 'active': True, 'config_valid': True,
         'includes_conf_d': True, 'sandbox_caddy': {'active': False, 'observed': True},
         'listeners': [{'processes': ['nginx']}], 'listeners_observed': True}
CADDY = {'nginx_installed': False, 'active': False, 'config_valid': False, 'includes_conf_d': False,
         'sandbox_caddy': {'installed': True, 'active': True, 'observed': True},
         'listeners': [{'processes': ['caddy']}], 'listeners_observed': True}


class Ssh:
    """A fake `ssh` client: ``home`` answers the $SANDBOX_HOME lookup,
    ``probe`` the bare `true` session, ``command`` everything else."""

    def __init__(self, command=POLICY_REFUSAL, *, home=None, probe=None):
        self.home, self.probe, self.command, self.calls = home, probe, command, []

    def __call__(self, remote, text, timeout=30, **_):
        self.calls.append(text)
        if text.startswith('echo ${SANDBOX_HOME'):
            answer = self.command if self.home is None else self.home
        elif text == tailnet_ssh.PROBE_COMMAND:
            answer = self.command if self.probe is None else self.probe
        else:
            answer = self.command
        if isinstance(answer, BaseException):
            raise answer
        return answer


def check_timeout():
    return subprocess.TimeoutExpired('ssh', 15, output=b'', stderr=CHECK_PROMPT)


class JoinWithoutSshTest(unittest.TestCase):
    def join(self, front_door, login='denied'):
        self.put = mock.Mock(side_effect=lambda name, **fields: fields)
        return run(command._cmd_join, SimpleNamespace(name='box', ssh_url=f'alim@{TAILNET}',
            front_door=front_door), {(sr, 'get_remote'): mock.Mock(return_value=None),
                (sr, '_remote_control_request'): mock.Mock(return_value=INVENTORY),
                (sr, 'remote_host_memory_request'): mock.Mock(return_value=evidence()),
                (command, '_tailnet_address'): mock.Mock(return_value=TAILNET),
                (command, '_first_ssh_login'): mock.Mock(return_value=login),
                (command, '_joined_profile_evidence'): mock.Mock(side_effect=AssertionError('SSH probe')),
                (sr, 'put_remote'): self.put})

    def test_control_admitted_join_succeeds_when_ssh_is_denied(self):
        code, result = self.join('nginx')
        self.assertEqual((code, result['status']), (0, 'joined'))
        self.assertEqual(result['ssh_access'], {'state': 'denied', 'transport': 'tailscale'})
        self.assertIsNone(result['front_door'])
        self.assertEqual(result['front_door_requested'], 'nginx')
        self.assertEqual(result['ssh_check'], CHECK)
        self.assertIn('job-start', result['next_step'])
        self.assertIn('remote service status box', result['next_step'])
        fields = self.put.call_args.kwargs
        self.assertEqual((fields['joined'], fields['control_access']), (True, 'tailnet'))
        # The request is kept apart from the proven profile.
        self.assertNotIn('front_door', fields)
        self.assertEqual(fields['front_door_requested'], 'nginx')
        self.assertEqual(fields['control_identity']['target_identity'], 'c' * 24)

    def test_check_mode_join_is_its_own_state(self):
        code, result = self.join(None, login='check_required')
        self.assertEqual(code, 0)
        self.assertEqual(result['ssh_access']['state'], 'check_required')
        self.assertIn('Tailscale check', result['next_step'])
        self.assertNotIn('front_door_requested', self.put.call_args.kwargs)

    def test_an_unsupported_profile_still_refuses_before_any_write(self):
        code, _ = self.join('caddy')
        self.assertNotEqual(code, 0)
        self.put.assert_not_called()

    def test_first_login_probe_names_denial_check_mode_and_unclear_results(self):
        cases = ((SimpleNamespace(returncode=0, stderr=''), 'ok'),
                 (POLICY_REFUSAL, 'denied'),
                 (SimpleNamespace(returncode=255, stderr=CHECK_PROMPT.decode()), 'check_required'),
                 (check_timeout(), 'check_required'),
                 (subprocess.TimeoutExpired('ssh', 30), 'unknown'),
                 (SimpleNamespace(returncode=1, stderr='Permission denied\n'), 'unknown'))
        for outcome, expected in cases:
            with self.subTest(expected=expected), mock.patch.object(command.subprocess, 'run',
                    **({'side_effect': outcome} if isinstance(outcome, BaseException)
                       else {'return_value': outcome})):
                self.assertEqual(command._first_ssh_login(f'alim@{TAILNET}', None), expected)


class DeferredNginxProfileTest(unittest.TestCase):
    """The join's --front-door nginx is proven on the first lookup once SSH works."""

    def joined_record(self):
        join = JoinWithoutSshTest()
        code, _ = join.join('nginx')
        self.assertEqual(code, 0)
        return {k: v for k, v in join.put.call_args.kwargs.items() if not k.startswith('_')}

    def adopt(self, record, *, facts, identity='c' * 24):
        self.put = put = mock.Mock(side_effect=lambda name, **fields: {
            **{k: v for k, v in record.items() if k not in fields.get('_remove', ())},
            **{k: v for k, v in fields.items() if not k.startswith('_')}})
        machine = SimpleNamespace(returncode=0, stdout=identity + '\n', stderr='')
        with mock.patch.object(sr, 'remote_host_memory_request', return_value=evidence()), \
                mock.patch.object(sr, 'ssh_run', return_value=machine), \
                mock.patch.object(nginx, 'probe', return_value=facts), \
                mock.patch.object(sr, 'put_remote', put):
            return put, join_adoption.adopt_missing('box', record)

    def test_recovery_proves_the_requested_nginx_and_clears_the_request(self):
        put, updated = self.adopt(self.joined_record(), facts=NGINX)
        kwargs = put.call_args.kwargs
        self.assertEqual(kwargs['front_door'], 'nginx')
        self.assertEqual(kwargs['_remove'], ('front_door_requested',))
        self.assertEqual(updated['front_door'], 'nginx')
        self.assertNotIn('front_door_requested', updated)

    def test_a_caddy_incumbent_is_never_pinned_over_the_nginx_request(self):
        record = self.joined_record()
        put, updated = self.adopt(record, facts=CADDY)
        for call in put.call_args_list:
            self.assertNotIn('front_door', call.kwargs)
            self.assertNotIn('_remove', call.kwargs)
        self.assertIsNone(updated.get('front_door'))
        self.assertEqual(updated['front_door_requested'], 'nginx')
        # Without a request, the same observation adopts Caddy (the old path).
        without = {k: v for k, v in record.items() if k != 'front_door_requested'}
        put, _ = self.adopt(without, facts=CADDY)
        self.assertEqual(put.call_args.kwargs['front_door'], 'caddy')

    def test_an_ssh_machine_identity_mismatch_refuses_and_writes_nothing(self):
        with self.assertRaises(FrontDoorError) as failure:
            self.adopt(self.joined_record(), facts=NGINX, identity='d' * 24)
        self.assertEqual(failure.exception.code, 'joined_profile_identity_mismatch')
        self.put.assert_not_called()

    def test_explicit_refresh_clears_the_request_after_proof(self):
        record = self.joined_record()
        put = mock.Mock()
        machine = SimpleNamespace(returncode=0, stdout='c' * 24, stderr='')
        with mock.patch.object(nginx, 'probe', return_value=NGINX), \
                mock.patch.object(sr, 'ssh_run', return_value=machine):
            code, result = run(command._cmd_service, SimpleNamespace(name='refresh', ssh_url='box',
                front_door='nginx'), {(sr, 'get_remote'): mock.Mock(return_value=record),
                    (sr, 'remote_host_memory_request'): mock.Mock(return_value=evidence()),
                    (sr, 'put_remote'): put})
        self.assertEqual((code, result['data']['front_door']), (0, 'nginx'))
        self.assertEqual(put.call_args.kwargs['_remove'], ('front_door_requested',))


class SshOnlyCommandRefusalTest(unittest.TestCase):
    """Through the real `$SANDBOX_HOME` resolver: a cold lookup is the first SSH call."""

    def transport(self, ssh):
        self.ssh = ssh
        return RemoteJobTransport(deploy=mock.Mock(side_effect=AssertionError('no deploy')),
            ssh_run=ssh, remote_lookup=lambda name: dict(JOINED), remote_sb_path=sr.remote_sb_path)

    def control(self, ssh, argv=('job-status', 'f' * 16)):
        with mock.patch.object(sr, 'ssh_run', ssh):
            return self.transport(ssh).control('box', list(argv))

    def test_cold_home_lookup_names_the_ssh_refusal_and_the_check(self):
        with self.assertRaises(TailnetSshDenied) as failure:
            self.control(Ssh())
        error = failure.exception
        self.assertEqual((error.code, error.retryable), ('tailnet_ssh_denied', False))
        self.assertIn(CHECK, str(error))
        self.assertIn('remote service status', str(error))
        # Classified at the home lookup, confirmed by a bare `true` session.
        self.assertTrue(self.ssh.calls[0].startswith('echo ${SANDBOX_HOME'))
        self.assertEqual(self.ssh.calls[1:], ['true'])

    def test_job_status_reports_the_refusal_code_not_unknown_transport(self):
        ssh = Ssh()
        with mock.patch.object(sr, 'ssh_run', ssh):
            payload = self.transport(ssh).status('box', 'f' * 16)
        self.assertEqual((payload['code'], payload['retryable']), ('tailnet_ssh_denied', False))
        self.assertIn('tailnet SSH policy', payload['error'])

    def test_exit_255_after_the_remote_command_ran_is_not_a_refusal(self):
        # OpenSSH passes on the command's own exit status; `true` still logs in.
        ran = SimpleNamespace(returncode=255, stdout='', stderr='Permission denied: /srv/x\n')
        ssh = Ssh(ran, home=HOME, probe=OK)
        with self.assertRaises(RemoteJobTransportError) as failure:
            self.control(ssh)
        self.assertNotIsInstance(failure.exception, (TailnetSshDenied, TailnetSshCheckRequired))
        self.assertEqual(ssh.calls[-1], 'true')
        # The same at the home lookup itself.
        with mock.patch.object(sr, 'ssh_run', Ssh(ran, probe=OK)), \
                self.assertRaisesRegex(RuntimeError, 'could not resolve') as home:
            sr.resolve_sandbox_home(dict(JOINED))
        self.assertNotIsInstance(home.exception, RemoteJobTransportError)

    def test_an_unconfirmed_refusal_stays_ambiguous(self):
        ssh = Ssh(home=HOME, probe=subprocess.TimeoutExpired('ssh', 15))
        with self.assertRaises(RemoteJobTransportError) as failure:
            self.control(ssh)
        self.assertNotIsInstance(failure.exception, (TailnetSshDenied, TailnetSshCheckRequired))
        self.assertEqual(tailnet_ssh.classify(JOINED, POLICY_REFUSAL,
                                              lambda: SimpleNamespace(returncode=1, stderr='')),
                         tailnet_ssh.AMBIGUOUS)

    def test_check_mode_prompt_is_check_required_not_denied(self):
        for ssh in (Ssh(check_timeout()), Ssh(check_timeout(), home=HOME),
                    Ssh(SimpleNamespace(returncode=255, stdout='', stderr=CHECK_PROMPT.decode()))):
            with self.subTest(calls=ssh.calls), self.assertRaises(TailnetSshCheckRequired) as failure:
                self.control(ssh)
            self.assertEqual(failure.exception.code, 'tailnet_ssh_check_required')
            self.assertIn(CHECK, str(failure.exception))
            self.assertIn('unknown', str(failure.exception))
            self.assertNotIn('true', ssh.calls)  # a check would hang the probe too

    def test_job_start_capability_discovery_keeps_the_refusal(self):
        ssh = Ssh()
        transport = self.transport(ssh)
        with mock.patch.object(sr, 'ssh_run', ssh), \
                mock.patch('sandbox.core._remote.put_remote') as writer, \
                self.assertRaises(ExecutionCapabilityUnavailable) as failure:
            transport._execution_remote('box')
        self.assertEqual(failure.exception.code, 'tailnet_ssh_denied')
        writer.assert_not_called()
        mapped = cli._transient_remote_error(failure.exception, SimpleNamespace(remote='box'))
        self.assertEqual((mapped.code, mapped.retryable), ('tailnet_ssh_denied', False))
        self.assertIn(CHECK, str(mapped))

    def test_job_start_check_mode_keeps_its_own_code(self):
        ssh = Ssh(check_timeout())
        with mock.patch.object(sr, 'ssh_run', ssh), self.assertRaises(ExecutionCapabilityUnavailable) as failure:
            adopt_execution_capabilities('box', dict(JOINED),
                lambda argv: self.transport(ssh).control('box', argv))
        self.assertEqual(failure.exception.code, 'tailnet_ssh_check_required')

    def test_job_output_cold_lookup_reports_the_refusal(self):
        args = SimpleNamespace(job_id='f' * 16, job_id_option=None, remote='box', json=True,
                               max_bytes=65536, wait_seconds=0, follow=False, cursor=None,
                               stream='combined', tail_bytes=None, encoding='utf8')
        out = io.StringIO()
        with mock.patch.object(sr, 'get_remote', return_value=dict(JOINED)), \
                mock.patch.object(sr, 'ssh_run', Ssh()), contextlib.redirect_stdout(out), \
                self.assertRaises(SystemExit) as exit_:
            jobs_runtime.cmd_job_output(None, args)
        self.assertEqual(exit_.exception.code, 1)
        payload = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual(payload['code'], 'tailnet_ssh_denied')
        self.assertIn(CHECK, payload['error'])


class StatusWhenSshRefusedTest(unittest.TestCase):
    """HTTP control admitted, Tailscale SSH refused."""

    def job_status(self, as_json):
        args = SimpleNamespace(job_id='f' * 16, job_id_option=None, remote='box', json=as_json)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sr, 'get_remote', return_value=dict(JOINED)), \
                mock.patch.object(sr, 'ssh_run', Ssh()), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as exit_:
            jobs_runtime.cmd_job_status(None, args)
        return exit_.exception.code, out.getvalue(), err.getvalue()

    def test_job_status_json_fails_and_names_the_refusal(self):
        code, out, _ = self.job_status(True)
        payload = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(code, 1)
        self.assertEqual((payload['ok'], payload['code'], payload['lifecycle']),
                         (False, 'tailnet_ssh_denied', 'unknown'))
        self.assertIn(CHECK, payload['error'])

    def test_job_status_human_fails_and_names_the_refusal(self):
        code, out, err = self.job_status(False)
        self.assertEqual(code, 1)
        self.assertEqual(out, '')
        self.assertIn('tailnet_ssh_denied', err)
        self.assertIn('refused', err)
        self.assertIn(CHECK, err)

    def service_status(self, as_json):
        out = io.StringIO()
        with mock.patch.object(sr, 'get_remote', return_value=dict(JOINED)), \
                mock.patch.object(sr, 'remote_host_memory_request', return_value=evidence()), \
                mock.patch.object(sr, '_remote_mcp_runtime_revision', return_value='d' * 24), \
                mock.patch.object(sr, 'ssh_run', return_value=POLICY_REFUSAL), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            try:
                command._cmd_service(SimpleNamespace(name='status', ssh_url='box'), as_json)
                code = 0
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue()

    def test_service_status_json_is_control_only_with_the_check(self):
        code, out = self.service_status(True)
        payload = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(code, 0)
        self.assertEqual((payload['ok'], payload['status'], payload['error']), (True, 'control_only', None))
        self.assertEqual(payload['data']['control_admission']['state'], 'admitted')
        self.assertEqual(payload['data']['ssh_access'],
                         {'state': 'denied', 'transport': 'tailscale', 'check': CHECK})
        self.assertIn('refused', payload['next_step'])
        self.assertIn(CHECK, payload['next_step'])

    def test_service_status_human_says_ssh_is_refused(self):
        code, out = self.service_status(False)
        self.assertEqual(code, 0)
        self.assertIn('remote service box: control_only', out)
        self.assertIn('refused', out)
        self.assertIn(CHECK, out)


class HostDispatchAcceptanceTest(unittest.TestCase):
    """`host stack`/`host apply` through the real server-CLI selection and transport."""

    def dispatch(self, ssh, *, request_id=None, prior=None):
        args = SimpleNamespace(json=True, request_id=request_id)

        def selected(selected_args):
            selected_args._server_cli_request_id = request_id or 'host-cli-' + '0' * 24
            selected_args._server_cli_submission_started = True
            if prior is not None:
                raise prior
            return server_cli._dispatch_server_cli_selected('box', dict(JOINED), None,
                clock=SimpleNamespace(lap=lambda *_: None), source_ref=None,
                required_capabilities=(), source_branch_bound=False, nested_apply=False)

        out = io.StringIO()
        with mock.patch.object(server_cli_dispatch, '_dispatch_host_if_selected', side_effect=selected), \
                mock.patch('sandbox.hosting.server_cli_here.claims', return_value=False), \
                mock.patch('sandbox.application.server_cli_source_repair.dispatch_if_selected', return_value=False), \
                mock.patch('sandbox.resources.context.authenticated_target_identity',
                           return_value={'target_identity': 'c' * 24}), \
                mock.patch.object(sr, 'get_remote', return_value=dict(JOINED)), \
                mock.patch.object(sr, 'ssh_run', ssh), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit):
            server_cli_dispatch.dispatch_host_if_selected(args)
        return json.loads(out.getvalue().strip().splitlines()[-1])

    def test_a_fresh_request_refused_at_login_is_not_accepted(self):
        payload = self.dispatch(Ssh())
        self.assertEqual((payload['code'], payload['acceptance']), ('tailnet_ssh_denied', 'not_accepted'))
        self.assertIn('Not submitted', payload['error'])
        self.assertIn(CHECK, payload['error'])

    def test_a_replay_after_a_lost_receipt_keeps_acceptance_unknown(self):
        # First attempt: submitted, then the receipt was lost.
        lost = self.dispatch(Ssh(), request_id='req-1', prior=RemoteJobTransportError('receipt lost'))
        self.assertEqual((lost['code'], lost['acceptance']), ('server_cli_acceptance_unknown', 'unknown'))
        # The replay with that id is refused at login: the ledger cannot be read.
        replay = self.dispatch(Ssh(), request_id='req-1')
        self.assertEqual((replay['code'], replay['acceptance'], replay['request_id']),
                         ('tailnet_ssh_denied', 'unknown', 'req-1'))
        self.assertIn('earlier attempt may already be accepted', replay['error'])
        self.assertIn(CHECK, replay['error'])

    def test_check_mode_never_claims_not_accepted(self):
        payload = self.dispatch(Ssh(check_timeout()))
        self.assertEqual((payload['code'], payload['acceptance']), ('tailnet_ssh_check_required', 'unknown'))


if __name__ == '__main__':
    unittest.main()
