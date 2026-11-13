"""Project-dir apply through server CLI: the controller binds the deploy branch.

Real temporary Git checkouts; synthetic retained owner/transport ports only.
"""
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
from unittest.mock import patch

from sandbox.application import server_cli as sdk
from sandbox.commands import hosting as host_commands
from sandbox.core import _hosting as hosting
from sandbox.hosting import server_cli_dispatch as dispatch
from tests.subprocess_support import run_test_process


class SourceBranchTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.client = self.root / 'client'
        self.client.mkdir()
        self.git(self.client, 'init', '-q', '-b', 'main')
        self.git(self.client, 'config', 'user.name', 'Synthetic Fixture')
        self.git(self.client, 'config', 'user.email', 'fixture@example.invalid')
        (self.client / 'sandbox.hosting.yml').write_text('version: 1\n')
        self.git(self.client, 'add', '.')
        self.git(self.client, 'commit', '-qm', 'source')
        self.head = self.git(self.client, 'rev-parse', 'HEAD')

    def git(self, root, *args):
        return run_test_process(['git', '-c', 'core.hooksPath=/dev/null', '-C', str(root), *args],
            timeout=5, check=True, capture_output=True, text=True).stdout.strip()

    # Controller side -----------------------------------------------------

    def arguments(self, *extra, **fields):
        values = dict(action='apply', confirm=True, plan=False, remote='box', project_dir=str(self.client),
            template=None, ref=None, request_id='original', timeout=None, json=True,
            server_cli_stack_ref=None, server_cli_source_branch=None,
            _server_cli_argv=('host', 'apply', '--remote', 'box', '--project-dir', str(self.client),
                '--environment', 'sandbox', '--confirm', *extra))
        values.update(fields)
        return Namespace(**values)

    def submit(self, args):
        with patch.object(sdk, 'server_cli_context_if_selected', return_value=None), \
                patch('sandbox.core._remote.get_remote', return_value={'joined': True, 'control_transport': 'tailscale'}), \
                patch.object(sdk, 'dispatch_server_cli', return_value={'ok': True, 'job_id': 'd' * 32}) as submit, \
                redirect_stdout(io.StringIO()):
            self.assertTrue(dispatch.dispatch_host_if_selected(args))
        return submit

    def test_controller_binds_its_branch_and_head_and_requires_the_contract(self):
        submit = self.submit(self.arguments())
        argv = submit.call_args.args[1]
        bound = [value for value in argv if value.startswith('--server-cli-source-branch=')]
        self.assertEqual(len(bound), 1)
        self.assertEqual(json.loads(bound[0].split('=', 1)[1]), {'branch': 'main', 'commit': self.head})
        self.assertIs(submit.call_args.kwargs['source_branch_bound'], True)
        self.assertNotIn('source_ref', submit.call_args.kwargs)

    def test_detached_controller_binds_nothing(self):
        self.git(self.client, 'checkout', '-q', '--detach')
        submit = self.submit(self.arguments())
        self.assertFalse(any(value.startswith('--server-cli-source-branch') for value in submit.call_args.args[1]))
        self.assertNotIn('source_branch_bound', submit.call_args.kwargs)

    def test_user_supplied_binding_is_reserved_and_refused_before_submission(self):
        forged = json.dumps({'branch': 'main', 'commit': self.head})
        for args in (self.arguments(server_cli_source_branch=forged),
                     self.arguments('--server-cli-source-branch=' + forged),
                     self.arguments('--server-cli-source-b=' + forged, server_cli_source_branch=forged)):
            with self.subTest(argv=args._server_cli_argv[-1]), \
                    patch.object(sdk, 'server_cli_context_if_selected', return_value=None), \
                    patch('sandbox.core._remote.get_remote', return_value={'joined': True, 'control_transport': 'tailscale'}), \
                    patch.object(sdk, 'dispatch_server_cli') as submit, redirect_stdout(io.StringIO()), \
                    self.assertRaises(SystemExit):
                dispatch.dispatch_host_if_selected(args)
            submit.assert_not_called()

    def test_server_without_the_contract_refuses_before_submission(self):
        from sandbox.core import _remote
        record = {'joined': True, 'control_transport': 'tailscale',
            'mcp_service': {'ownership_marker': 'a' * 24, 'runtime_revision': 'b' * 24}}
        base = {'schema_version': 1, 'target_identity': 'c' * 24, 'ownership_marker': 'a' * 24,
            'runtime_revision': 'b' * 24,
            'capabilities': [sdk.CONTRACT, 'job.acceptance-receipt.v1', 'job.source-content.v1']}
        for apply_caps, accepted in ((None, False), ([], False), ('x', False), ([{}], False),
                                     ([dispatch.SOURCE_BRANCH_CONTRACT], False),
                                     (['job.server-cli-source-branch.v1', 'job.server-cli-project-dir-apply.v1'], True)):
            caps = dict(base)
            if apply_caps is not None:
                caps['server_cli_apply_capabilities'] = apply_caps
            with self.subTest(apply_caps=apply_caps), patch.object(_remote, 'get_remote', return_value=record), \
                    patch('sandbox.resources.context.authenticated_target_identity', return_value={'target_identity': 'c' * 24}), \
                    patch('sandbox.transports.remote_jobs.RemoteJobTransport') as factory:
                transport = factory.return_value
                transport.control.return_value = caps
                transport.submit_server_cli.return_value = {'ok': True, 'job_id': 'd' * 32}
                call = lambda: sdk.dispatch_server_cli('box', ['host', 'apply'], project_dir=str(self.client),
                    request_id='original', source_branch_bound=True, nested_apply=True)
                if accepted:
                    self.assertEqual(call(), {'ok': True, 'job_id': 'd' * 32})
                else:
                    with self.assertRaises(sdk.ServerCLIUnavailable):
                        call()
                    transport.submit_server_cli.assert_not_called()

    def test_bound_recover_needs_only_the_branch_contract(self):
        from sandbox.core import _remote
        record = {'joined': True, 'control_transport': 'tailscale',
            'mcp_service': {'ownership_marker': 'a' * 24, 'runtime_revision': 'b' * 24}}
        caps = {'schema_version': 1, 'target_identity': 'c' * 24, 'ownership_marker': 'a' * 24,
            'runtime_revision': 'b' * 24,
            'capabilities': [sdk.CONTRACT, 'job.acceptance-receipt.v1', 'job.source-content.v1'],
            'server_cli_apply_capabilities': [dispatch.SOURCE_BRANCH_CONTRACT]}
        for command, nested, accepted in ((['host', 'recover'], False, True),
                                          (['host', 'apply'], True, False)):
            with self.subTest(command=command), patch.object(_remote, 'get_remote', return_value=record), \
                    patch('sandbox.resources.context.authenticated_target_identity', return_value={'target_identity': 'c' * 24}), \
                    patch('sandbox.transports.remote_jobs.RemoteJobTransport') as factory:
                transport = factory.return_value
                transport.control.return_value = caps
                transport.submit_server_cli.return_value = {'ok': True, 'job_id': 'd' * 32}
                call = lambda: sdk.dispatch_server_cli('box', command, project_dir=str(self.client),
                    request_id='original', source_branch_bound=True, nested_apply=nested)
                if accepted:
                    self.assertEqual(call(), {'ok': True, 'job_id': 'd' * 32})
                else:
                    with self.assertRaises(sdk.ServerCLIUnavailable):
                        call()
                    transport.submit_server_cli.assert_not_called()

    def test_dispatch_requires_nested_apply_only_for_apply(self):
        for action, expected in (('apply', True), ('recover', None)):
            args = self.arguments(action=action)
            args._server_cli_argv = ('host', action, *args._server_cli_argv[2:])
            submit = self.submit(args)
            self.assertIs(submit.call_args.kwargs['source_branch_bound'], True)
            self.assertIs(submit.call_args.kwargs.get('nested_apply'), expected)

    def test_contract_is_advertised_outside_the_strict_job_capability_list(self):
        owner = sdk.ServerCliOwner(os.geteuid(), '/synthetic', '/synthetic/sandbox',
            '/synthetic/sb', 'c' * 24, 'a' * 24, 'b' * 24)
        with patch.object(sdk, '_current_owner', return_value=owner), \
                patch.object(sdk, '_receiver_routing_metadata', return_value={}), \
                patch.object(dispatch, 'source_branch_contract_available', return_value=True):
            caps = sdk.capabilities()
        self.assertEqual(caps['server_cli_apply_capabilities'], [dispatch.SOURCE_BRANCH_CONTRACT, 'job.server-cli-project-dir-apply.v1'])
        # Older controllers refuse unknown entries here; keep them working.
        self.assertNotIn(dispatch.SOURCE_BRANCH_CONTRACT, caps['capabilities'])

    # Receiver side -------------------------------------------------------

    def receiver(self, branch='main', commit=None):
        staged = self.root / 'staged'
        run_test_process(['git', '-c', 'core.hooksPath=/dev/null', 'clone', '--quiet', '--no-hardlinks',
            str(self.client), str(staged)], timeout=5, check=True, capture_output=True, text=True)
        self.git(staged, 'checkout', '--quiet', '--detach', self.head)
        packed = json.dumps({'branch': branch, 'commit': commit or self.head}, sort_keys=True, separators=(',', ':'))
        owner = sdk.ServerCliOwner(os.geteuid(), str(self.root), str(self.root / 'state'),
            str(self.root / 'sb'), 'a' * 24, 'b' * 24, 'c' * 24)
        context = sdk.ServerCLIContext(1, 'd' * 32, 'original', str(staged), 'project', 'source', self.head,
            None, 'a' * 24, 'c' * 24, (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat(),
            ('host', 'apply', '--project-dir', '.', '--confirm', '--server-cli-source-branch=' + packed),
            'box', MappingProxyType({}), owner)
        return staged, context

    def validate(self, staged, context, allowed=('main',)):
        validated = {'project_root': str(staged), 'environment': 'sandbox',
                     'deploy': {'allowed_branches': list(allowed), 'require_clean': True}}
        with patch.object(sdk, 'server_cli_context_if_selected', return_value=context), \
                patch.object(sdk, 'validate_owner_current', return_value=context):
            return host_commands._validate_apply_source(validated)

    def nested_submission(self, staged, context, *, stack=None):
        from unittest.mock import Mock
        target = Mock()
        target.require_current.return_value = context
        validated = {'project_root': str(staged), 'environment': 'sandbox',
                     'deploy': {'allowed_branches': ['main'], 'require_clean': True}}
        if stack is not None:
            validated['stack'] = stack
        self.enterContext(patch('sandbox.hosting.server_cli_target.selected_owner_target', return_value=target))
        self.enterContext(patch.object(sdk, 'server_cli_context_if_selected', return_value=context))
        self.enterContext(patch.object(sdk, 'validate_owner_current', return_value=context))
        self.enterContext(patch.object(sdk, 'installed_cli', return_value=Path(context.owner.executable)))
        submit = self.enterContext(patch('sandbox.commands.host_stack._submit', return_value='e' * 32))
        return validated, submit

    def test_outer_project_dir_submits_one_source_bound_local_child(self):
        staged, context = self.receiver()
        validated, submit = self.nested_submission(staged, context)
        args = self.arguments(server_cli_source_branch='untrusted-user-value')
        with redirect_stdout(io.StringIO()):
            self.assertTrue(host_commands._submit_existing_owner_apply(validated, args))
        submit.assert_called_once()
        argv = submit.call_args.args[0]
        self.assertIn('--local', argv)
        self.assertIn('--server-cli', argv)
        for option, value in (('--project-dir', str(staged)), ('--source-commit', self.head),
                              ('--parent-job-id', context.job_id)):
            self.assertEqual(argv[argv.index(option) + 1], value)
        self.assertEqual(argv.count(context.argv[-1]), 1)
        self.assertNotIn('untrusted-user-value', argv)
        # Revalidate the forwarded token against the child's own context.
        child = replace(context, job_id='e' * 32, argv=tuple(argv[argv.index('--') + 1:]))
        object.__setattr__(child, 'parent_job_id', context.job_id)
        with patch.object(sdk, 'server_cli_context_if_selected', return_value=child), \
                patch.object(sdk, 'validate_owner_current', return_value=child):
            self.assertEqual(host_commands._validate_apply_source(validated), 'main')
            self.assertTrue(self.recovery(staged, child))

    def test_project_dir_child_requires_admission_and_never_resubmits(self):
        staged, context = self.receiver()
        object.__setattr__(context, 'parent_job_id', 'f' * 32)
        validated, submit = self.nested_submission(staged, context)
        with patch.object(host_commands, '_durable_host_context', return_value={'job_id': context.job_id}) as admit:
            self.assertFalse(host_commands._submit_existing_owner_apply(validated, self.arguments()))
        admit.assert_called_once_with(str(staged))
        from sandbox.delivery.admission import AdmissionError
        with patch.object(host_commands, '_durable_host_context', side_effect=AdmissionError('recovery_source_mismatch')):
            with self.assertRaises(AdmissionError):
                host_commands._submit_existing_owner_apply(validated, self.arguments())
        submit.assert_not_called()

    def test_outer_project_dir_refuses_dirty_missing_invalid_or_changed_source(self):
        staged, original = self.receiver()
        cases = [replace(original, argv=('host', 'apply')),
                 replace(original, argv=('host', 'apply', '--server-cli-source-branch={')),
                 replace(original, source_commit='f' * 40),
                 replace(original, project_root=str(self.client))]
        for context in cases:
            with self.subTest(context=context):
                validated, submit = self.nested_submission(staged, context)
                with self.assertRaises((hosting.HostingError, dispatch.SourceBranchBindingError)):
                    host_commands._submit_existing_owner_apply(validated, self.arguments())
                submit.assert_not_called()
        validated, submit = self.nested_submission(staged, original)
        (staged / 'dirty').write_text('dirty')
        with self.assertRaisesRegex(hosting.HostingError, 'recovery_source_dirty'):
            host_commands._submit_existing_owner_apply(validated, self.arguments())
        submit.assert_not_called()

    def test_stack_nested_submission_keeps_rendered_source_and_no_branch_token(self):
        staged, context = self.receiver()
        # Stack source is a separately owned rendered root, unlike project-dir.
        context = replace(context, project_root=str(self.client))
        validated, submit = self.nested_submission(staged, context,
            stack={'commit': self.head, 'ref_branch': 'main'})
        with redirect_stdout(io.StringIO()):
            self.assertTrue(host_commands._submit_existing_owner_apply(validated, self.arguments()))
        argv = submit.call_args.args[0]
        self.assertEqual(argv[argv.index('--project-dir') + 1], str(staged))
        self.assertEqual(argv[argv.index('--source-commit') + 1], self.head)
        self.assertFalse(any(value.startswith(dispatch.SOURCE_BRANCH_OPTION) for value in argv))
        context = replace(context, project_root=str(staged))
        validated, submit = self.nested_submission(staged, context,
            stack={'commit': self.head, 'ref_branch': 'main'})
        self.assertFalse(host_commands._submit_existing_owner_apply(validated, self.arguments()))
        submit.assert_not_called()

    def test_detached_staged_checkout_uses_the_bound_branch(self):
        staged, context = self.receiver()
        self.assertEqual(self.validate(staged, context), 'main')

    def test_unbound_detached_staged_checkout_is_still_refused(self):
        staged, context = self.receiver()
        with self.assertRaisesRegex(RuntimeError, 'detached HEAD'):
            self.validate(staged, replace(context, argv=('host', 'apply', '--confirm')))

    def test_bound_branch_outside_allowed_branches_is_refused(self):
        staged, context = self.receiver(branch='feature/x')
        with self.assertRaisesRegex(hosting.HostingError, "branch 'feature/x' is not allowed"):
            self.validate(staged, context)

    def test_dirty_staged_checkout_still_fails_require_clean(self):
        staged, context = self.receiver()
        (staged / 'stray.txt').write_text('dirty')
        with self.assertRaisesRegex(hosting.HostingError, 'requires a clean working tree'):
            self.validate(staged, context)

    def test_rebound_malformed_or_stale_bindings_are_refused(self):
        staged, context = self.receiver()
        token = context.argv[-1]
        def argv(*tokens):
            return replace(context, argv=('host', 'apply', '--confirm', *tokens))
        cases = {
            'other commit': replace(context, source_commit='f' * 40),
            'other root': replace(context, project_root=str(self.client)),
            'duplicate': argv(token, token),
            'no value': argv('--server-cli-source-branch'),
            'not json': argv('--server-cli-source-branch={'),
            'extra key': argv('--server-cli-source-branch=' + json.dumps(
                {'branch': 'main', 'commit': self.head, 'ref_branch': 'main'})),
            'bad name': argv('--server-cli-source-branch=' + json.dumps(
                {'branch': 'ma..in', 'commit': self.head})),
            'HEAD': argv('--server-cli-source-branch=' + json.dumps({'branch': 'HEAD', 'commit': self.head})),
            'control char': argv('--server-cli-source-branch=' + json.dumps(
                {'branch': 'main\nx', 'commit': self.head})),
        }
        for name, selected in cases.items():
            with self.subTest(case=name), self.assertRaises(hosting.HostingRefusal) as raised:
                self.validate(staged, selected)
            self.assertTrue(raised.exception.code.startswith('server_cli_source_branch_'))

    def test_staged_head_moved_is_refused(self):
        staged, context = self.receiver()
        (staged / 'later.txt').write_text('later')
        self.git(staged, 'add', '.')
        self.git(staged, '-c', 'user.name=x', '-c', 'user.email=x@example.invalid', 'commit', '-qm', 'later')
        with self.assertRaises(hosting.HostingRefusal) as raised:
            self.validate(staged, context)
        self.assertEqual(raised.exception.code, 'server_cli_source_branch_source_changed')

    def test_owner_revalidation_mismatch_is_refused(self):
        staged, context = self.receiver()
        validated = {'project_root': str(staged), 'environment': 'sandbox',
                     'deploy': {'allowed_branches': ['main'], 'require_clean': True}}
        with patch.object(sdk, 'server_cli_context_if_selected', return_value=context), \
                patch.object(sdk, 'validate_owner_current', return_value=replace(context, job_id='e' * 32)), \
                self.assertRaises(hosting.HostingRefusal) as raised:
            host_commands._validate_apply_source(validated)
        self.assertEqual(raised.exception.code, 'server_cli_source_branch_owner_changed')

    def recovery(self, staged, context, branch='main', commit=None, owner=None):
        validated = {'project_root': str(staged), 'environment': 'sandbox'}
        operation = {'source': {'clean': True, 'commit': commit or self.head},
                     'evidence': {'source_branch': branch}}
        with patch.object(sdk, 'server_cli_context_if_selected', return_value=context), \
                patch.object(sdk, 'validate_owner_current', return_value=owner or context):
            return host_commands._recovery_source_check(validated, operation)

    def test_detached_staged_recovery_uses_the_bound_branch(self):
        staged, context = self.receiver()
        self.assertTrue(self.recovery(staged, context))

    def test_detached_staged_recovery_refuses_mismatches(self):
        staged, context = self.receiver()
        self.assertFalse(self.recovery(staged, context, branch='dev'))
        self.assertFalse(self.recovery(staged, context, commit='f' * 40))
        self.assertFalse(self.recovery(staged, context, owner=replace(context, job_id='e' * 32)))
        self.assertFalse(self.recovery(staged, replace(context, source_commit='f' * 40)))
        self.assertFalse(self.recovery(staged, replace(context, argv=('host', 'recover'))))
        self.assertFalse(self.recovery(staged, None))
        staged_stack = {'project_root': str(staged), 'environment': 'sandbox', 'stack': {'commit': self.head}}
        with patch.object(sdk, 'server_cli_context_if_selected', return_value=context), \
                patch.object(sdk, 'validate_owner_current', return_value=context):
            self.assertFalse(host_commands._recovery_source_check(staged_stack, {
                'source': {'clean': True, 'commit': self.head}, 'evidence': {'source_branch': 'main'}}))

    def test_attached_recovery_reads_git_as_before(self):
        validated = {'project_root': str(self.client), 'environment': 'sandbox'}
        operation = {'source': {'clean': True, 'commit': self.head}, 'evidence': {'source_branch': 'main'}}
        with patch.object(sdk, 'server_cli_context_if_selected', return_value=None):
            self.assertTrue(host_commands._recovery_source_check(validated, operation))
            operation['evidence']['source_branch'] = 'dev'
            self.assertFalse(host_commands._recovery_source_check(validated, operation))

    def test_outside_a_server_job_git_state_decides_as_before(self):
        validated = {'project_root': str(self.client), 'environment': 'sandbox',
                     'deploy': {'allowed_branches': ['main'], 'require_clean': True}}
        with patch.object(sdk, 'server_cli_context_if_selected', return_value=None):
            self.assertEqual(host_commands._validate_apply_source(validated), 'main')
            self.git(self.client, 'checkout', '-q', '--detach')
            with self.assertRaisesRegex(RuntimeError, 'detached HEAD'):
                host_commands._validate_apply_source(validated)


if __name__ == '__main__':
    unittest.main()
