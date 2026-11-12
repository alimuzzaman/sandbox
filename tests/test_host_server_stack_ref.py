"""Real temporary Git refs; synthetic retained owner/transport ports only."""
import argparse
from argparse import Namespace
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import tempfile
from types import MappingProxyType
import unittest
from unittest.mock import Mock, patch

from sandbox.application import server_cli as sdk
from sandbox.commands import host_stack
from sandbox.hosting import server_cli_dispatch as dispatch
from tests.subprocess_support import run_test_process


class StackRefDispatchTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.client = self.root / 'client'
        self.client.mkdir()
        self.git(self.client, 'init', '-q')
        self.git(self.client, 'config', 'user.name', 'Synthetic Fixture')
        self.git(self.client, 'config', 'user.email', 'fixture@example.invalid')
        (self.client / 'sandbox.hosting.yml').write_text('version: 1\n')
        self.git(self.client, 'add', '.')
        self.git(self.client, 'commit', '-qm', 'selected dev source')
        self.selected = self.git(self.client, 'rev-parse', 'HEAD')
        self.git(self.client, 'branch', 'dev', self.selected)
        (self.client / 'other.txt').write_text('later current HEAD')
        self.git(self.client, 'add', '.')
        self.git(self.client, 'commit', '-qm', 'different current source')
        self.head = self.git(self.client, 'rev-parse', 'HEAD')

    def git(self, root, *args):
        return run_test_process(['git', '-c', 'core.hooksPath=/dev/null', '-C', str(root), *args],
            timeout=5, check=True, capture_output=True, text=True).stdout.strip()

    def arguments(self, *, ref='dev'):
        return Namespace(action='stack', image_action='up', confirm=True, plan=False,
            remote='box', project_dir=str(self.client), template=str(self.client / 'sandbox.hosting.yml'),
            ref=ref, request_id='original', timeout=None, json=True,
            _server_cli_argv=('host', 'stack', 'up', '--remote', 'box', '--ref', ref,
                '--project-dir', str(self.client), '--template', str(self.client / 'sandbox.hosting.yml'), '--confirm'))

    def test_client_binds_selected_branch_commit_even_when_current_head_differs(self):
        args = self.arguments()
        with patch.object(sdk, 'server_cli_context_if_selected', return_value=None), \
                patch('sandbox.core._remote.get_remote', return_value={'joined': True, 'control_transport': 'tailscale'}), \
                patch.object(sdk, 'dispatch_server_cli', return_value={'ok': True, 'job_id': 'd' * 32}) as submit, \
                redirect_stdout(io.StringIO()):
            self.assertTrue(dispatch.dispatch_host_if_selected(args))
        argv = submit.call_args.args[1]
        packed = next(value.split('=', 1)[1] for value in argv if value.startswith('--server-cli-stack-ref='))
        self.assertEqual(json.loads(packed), {'ref': 'dev', 'commit': self.selected, 'ref_branch': 'dev'})
        self.assertEqual(submit.call_args.kwargs['source_ref'], self.selected)
        self.assertEqual(argv[argv.index('--ref') + 1], 'dev')
        self.assertEqual(self.git(self.client, 'rev-parse', 'HEAD'), self.head)

    def test_unresolved_local_ref_and_user_supplied_binding_refuse_before_submission(self):
        for mode in ('missing', 'forged'):
            args = self.arguments(ref='missing' if mode == 'missing' else 'dev')
            if mode == 'forged':
                args.server_cli_stack_ref = '{}'
                args._server_cli_argv += ('--server-cli-stack-ref={}',)
            with self.subTest(mode=mode), patch.object(sdk, 'server_cli_context_if_selected', return_value=None), \
                    patch('sandbox.core._remote.get_remote', return_value={'joined': True, 'control_transport': 'tailscale'}), \
                    patch.object(sdk, 'dispatch_server_cli') as submit, redirect_stdout(io.StringIO()), \
                    self.assertRaises(SystemExit):
                dispatch.dispatch_host_if_selected(args)
            submit.assert_not_called()

    def receiver(self):
        root = self.root / 'server'
        run_test_process(['git', '-c', 'core.hooksPath=/dev/null', 'clone', '--quiet', '--no-hardlinks',
            str(self.client), str(root)], timeout=5, check=True, capture_output=True, text=True)
        self.git(root, 'checkout', '--quiet', '--detach', self.selected)
        refs = self.git(root, 'for-each-ref', '--format=%(refname)').splitlines()
        for ref in refs:
            self.git(root, 'update-ref', '-d', ref)
        packed = json.dumps({'ref': 'dev', 'commit': self.selected, 'ref_branch': 'dev'}, sort_keys=True, separators=(',', ':'))
        args = Namespace(ref='dev', server_cli_stack_ref=packed)
        owner = sdk.ServerCliOwner(os.geteuid(), str(self.root), str(self.root / 'state'),
            str(self.root / 'sb'), 'a' * 24, 'b' * 24, 'c' * 24)
        context = sdk.ServerCLIContext(1, 'd' * 32, 'original', str(root), 'project', 'source', self.selected,
            None, 'a' * 24, 'c' * 24, (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat(),
            ('host', 'stack', 'up', '--ref', 'dev', '--server-cli-stack-ref=' + packed),
            'box', MappingProxyType({}), owner)
        return root, args, context

    def test_detached_receiver_keeps_branch_lifetime_and_original_commit(self):
        root, args, context = self.receiver()
        with self.assertRaises(host_stack.StackCommandError):
            host_stack.resolve_ref(root, 'dev')
        with patch.object(sdk, 'server_cli_context_if_selected', return_value=context), \
                patch.object(sdk, 'validate_owner_current', return_value=context):
            ref = dispatch.resolve_stack_ref(args, root)
        self.assertEqual(ref, host_stack.StackRef('dev', self.selected, 'dev'))
        lifetime = host_stack._requested_lifetime(Namespace(lifetime='hosted', ttl=None), ref, 'dev')
        self.assertEqual(lifetime[0], 'hosted')
        self.assertEqual(self.git(root, 'rev-parse', 'HEAD'), self.selected)
        self.assertEqual(self.git(root, 'for-each-ref', '--format=%(refname)'), '')

    def test_receiver_refuses_absent_revoked_rebound_or_malformed_binding(self):
        root, args, context = self.receiver()
        cases = [(None, args, None),
            (replace(context, source_commit='f' * 40), args, None),
            (replace(context, project_root=str(self.client)), args, None),
            (replace(context, argv=('host', 'stack', 'up')), args, None),
            (context, Namespace(ref='other', server_cli_stack_ref=args.server_cli_stack_ref), None),
            (context, Namespace(ref='dev', server_cli_stack_ref='{}'), None),
            (context, Namespace(ref='dev', server_cli_stack_ref='{'), None),
            (context, Namespace(ref='dev', server_cli_stack_ref=json.dumps(
                {'ref': 'dev', 'commit': self.selected, 'ref_branch': 'bad\nbranch'})), None),
            (context, Namespace(ref='dev', server_cli_stack_ref=None), None),
            (context, args, sdk.ServerCLIUnavailable('revoked'))]
        for index, (selected, arguments, error) in enumerate(cases):
            with self.subTest(case=index), \
                    patch.object(sdk, 'server_cli_context_if_selected', return_value=selected), \
                    patch.object(sdk, 'validate_owner_current', return_value=selected, side_effect=error), \
                    self.assertRaises((dispatch.StackRefBindingError, sdk.ServerCLIUnavailable, host_stack.StackCommandError)):
                dispatch.resolve_stack_ref(arguments, root)

    def test_malformed_binding_returns_bounded_stack_error_before_store(self):
        root, args, context = self.receiver()
        args.stack_action, args.confirm, args.plan, args.json = 'up', True, False, True
        args.remote, args.template, args.server_cli_stack_ref = 'box', str(root / 'sandbox.hosting.yml'), '{'
        output = io.StringIO()
        with patch.object(sdk, 'server_cli_context_if_selected', return_value=context), \
                patch.object(host_stack, '_entry', return_value={}), \
                patch.object(host_stack, '_store') as store, redirect_stdout(output), \
                self.assertRaises(SystemExit) as caught:
            host_stack.cmd_host_stack(args)
        self.assertEqual(caught.exception.code, 1)
        self.assertEqual(json.loads(output.getvalue()), {'ok': False,
            'code': 'server_cli_stack_ref_unavailable', 'error': 'server_cli_stack_ref_invalid'})
        store.assert_not_called()

    def test_ordinary_controller_ref_resolution_keeps_existing_behavior(self):
        with patch.object(sdk, 'server_cli_context_if_selected', return_value=None):
            self.assertEqual(dispatch.resolve_stack_ref(Namespace(ref='dev'), self.client),
                host_stack.StackRef('dev', self.selected, 'dev'))

    def test_committed_stack_source_excludes_dirty_overlay_without_changing_legacy_policy(self):
        from sandbox.core import _remote
        (self.client / 'other.txt').write_text('uncommitted client bytes')
        with self.assertRaisesRegex(ValueError, 'clean working tree'):
            _remote.resolve_source_ref(self.client, self.selected)
        def reset(remote, target, revision, **kwargs):
            kwargs['content_receipt']['content_digest'] = 'sha256:' + 'a' * 64
            return 0
        with patch.object(_remote, 'capture_uncommitted', side_effect=AssertionError('dirty files must not be captured')), \
                patch.object(_remote, 'remote_network_capacity_admission', return_value={'ok': True}), \
                patch.object(_remote, 'ensure_deploy_repo', return_value='/synthetic/deploy'), \
                patch.object(_remote, 'push_commits', return_value=self.selected) as push, \
                patch.object(_remote, 'update_target_to', side_effect=reset) as publish:
            value = _remote.deploy_exact_working_tree({}, self.client,
                source_ref=self.selected, committed_only=True)
        self.assertFalse(value['dirty'])
        self.assertEqual(value['resolved_commit'], self.selected)
        self.assertEqual(push.call_args.kwargs['resolved_sha'], self.selected)
        self.assertIsNone(publish.call_args.kwargs['project_root'])
        self.assertEqual(publish.call_args.kwargs['diff_text'], '')
        self.assertEqual(publish.call_args.kwargs['untracked'], [])
        self.assertEqual(self.git(self.client, 'rev-parse', 'HEAD'), self.head)
        self.assertEqual((self.client / 'other.txt').read_text(), 'uncommitted client bytes')

    def test_generic_retained_head_without_host_binding_keeps_existing_behavior(self):
        root, args, context = self.receiver()
        with patch.object(sdk, 'server_cli_context_if_selected', return_value=context):
            self.assertEqual(dispatch.resolve_stack_ref(Namespace(ref='HEAD'), root),
                host_stack.StackRef('HEAD', self.selected, ''))


class SourceRefSDKTests(unittest.TestCase):
    def test_capability_requires_actual_installed_host_parser(self):
        from sandbox import cli
        from sandbox.application import command_selection
        with patch.object(command_selection, 'selected_invocation', return_value=None):
            self.assertFalse(dispatch.stack_ref_contract_available())
        previous = command_selection.bind_cli_command_parser(argparse.ArgumentParser(), lambda values: values)
        self.addCleanup(command_selection.reset_cli_command_parser, previous)
        class Captured(Exception):
            pass
        def capture(parser, *args, **kwargs):
            raise Captured()
        with patch.object(argparse.ArgumentParser, 'parse_args', capture), \
                patch.object(cli.sys, 'argv', ['sb']), self.assertRaises(Captured):
            cli.main()
        self.assertTrue(dispatch.stack_ref_contract_available())
        owner = sdk.ServerCliOwner(os.geteuid(), '/synthetic', '/synthetic/sandbox',
            '/synthetic/sb', 'c' * 24, 'a' * 24, 'b' * 24)
        with patch.object(sdk, '_current_owner', return_value=owner), \
                patch.object(sdk, '_receiver_routing_metadata', return_value={}):
            self.assertIn('job.server-cli-stack-ref.v1', sdk.capabilities()['capabilities'])

    def test_noncanonical_source_ref_refuses_before_lookup(self):
        with patch('sandbox.core._remote.get_remote') as lookup:
            for value in ('dev', 'A' * 40, 1):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    sdk.dispatch_server_cli('box', ['host', 'stack', 'up'], project_dir='/synthetic',
                        request_id='original', source_ref=value)
        lookup.assert_not_called()

    def test_exact_source_ref_is_required_in_discovery_and_passed_to_measured_deployer(self):
        from sandbox.core import _remote
        for supported in (False, True):
            record = {'joined': True, 'control_transport': 'tailscale',
                'mcp_service': {'ownership_marker': 'a' * 24, 'runtime_revision': 'b' * 24}}
            caps = {'schema_version': 1, 'target_identity': 'c' * 24, 'ownership_marker': 'a' * 24,
                'runtime_revision': 'b' * 24, 'capabilities': [sdk.CONTRACT, 'job.acceptance-receipt.v1', 'job.source-content.v1']}
            if supported:
                caps['capabilities'].append('job.server-cli-stack-ref.v1')
            with self.subTest(supported=supported), patch.object(_remote, 'get_remote', return_value=record), \
                    patch('sandbox.resources.context.authenticated_target_identity', return_value={'target_identity': 'c' * 24}), \
                    patch.object(_remote, 'deploy_exact_working_tree') as deploy, \
                    patch('sandbox.transports.remote_jobs.RemoteJobTransport') as factory:
                transport = factory.return_value
                transport.control.return_value = caps
                transport.submit_server_cli.return_value = {'ok': True, 'job_id': 'd' * 32}
                if not supported:
                    with self.assertRaises(sdk.ServerCLIUnavailable):
                        sdk.dispatch_server_cli('box', ['host', 'stack', 'up'], project_dir='/synthetic',
                            request_id='original', source_ref='e' * 40)
                    transport.submit_server_cli.assert_not_called()
                    deploy.assert_not_called()
                else:
                    result = sdk.dispatch_server_cli('box', ['host', 'stack', 'up'], project_dir='/synthetic',
                        request_id='original', source_ref='e' * 40)
                    self.assertTrue(result['ok'])
                    factory.call_args.kwargs['deploy']({}, '/synthetic', push_timeout=3)
                    deploy.assert_called_once_with({}, '/synthetic', source_ref='e' * 40,
                        committed_only=True, push_timeout=3)
