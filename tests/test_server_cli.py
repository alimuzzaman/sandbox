"""Fixed server CLI ports: retained process authority and no controller secrets."""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
from datetime import datetime, timezone

from sandbox.application import server_cli as cli
from sandbox.core import _paths, _remote
from sandbox.jobs.supervisor import _child_identity_context


OWNER = cli.ServerCliOwner(1001, '/home/fixture', '/home/fixture/sandbox',
    '/home/fixture/sandbox/sb-src/sb', 'c' * 24, 'a' * 24, 'b' * 24)


class ServerCLIContractTest(unittest.TestCase):
    def test_selected_nonsecret_unit_proves_fixed_executable_without_reading_environment_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            root = home / 'sandbox/sb-src'
            root.mkdir(parents=True)
            executable = root / 'sb'
            executable.write_text('#!/bin/sh\nexit 0\n')
            executable.chmod(0o700)
            unit = home / '.config/systemd/user/sandbox-remote-mcp.service'
            unit.parent.mkdir(parents=True)
            unit.write_text('EnvironmentFile=/synthetic/never-open-owner-credentials\n'
                'Environment=SANDBOX_REMOTE_MCP_MARKER=' + 'a' * 24 + '\n'
                'Environment=SANDBOX_REMOTE_MCP_RUNTIME_REVISION=' + 'b' * 24 + '\n'
                'WorkingDirectory=%h/sandbox/sb-src\nExecStart=%h/sandbox/sb-src/sb mcp\n')
            unit.chmod(0o600)
            with mock.patch.object(cli.pwd, 'getpwuid', return_value=SimpleNamespace(pw_dir=str(home))), \
                    mock.patch.object(_paths, 'ROOT', root), \
                    mock.patch.object(_paths, 'RUNTIME_DIR', home / 'sandbox/runtime'), \
                    mock.patch.object(_remote, '_remote_mcp_runtime_revision', return_value='b' * 24), \
                    mock.patch.object(cli, 'machine_identity', return_value='c' * 24):
                owner = cli._current_owner()
            self.assertEqual(owner.executable, str(executable.resolve()))
            self.assertEqual(owner.ownership_marker, 'a' * 24)
            self.assertFalse(Path('/synthetic/never-open-owner-credentials').exists())

    def test_installed_capability_is_actual_parser_plus_owner_binding(self):
        with mock.patch.object(cli, '_current_owner', return_value=OWNER):
            value = cli.capabilities()
        self.assertIn(cli.CONTRACT, value['capabilities'])
        self.assertEqual(value['target_identity'], OWNER.target_identity)
        self.assertEqual(value['ownership_marker'], OWNER.ownership_marker)

    def test_fixed_argv_refuses_executable_override_and_secret_output(self):
        with mock.patch.object(cli, 'installed_cli', return_value=Path(OWNER.executable)):
            self.assertEqual(cli.fixed_argv(['host', 'stack', 'list']),
                             (OWNER.executable, 'host', 'stack', 'list'))
            for argv in (['/tmp/project-script'], ['sb', 'status'], ['secrets', 'reveal'], ['secrets', 'run']):
                with self.subTest(argv=argv), self.assertRaises(cli.ServerCLIUnavailable):
                    cli.fixed_argv(argv)

    def test_child_environment_is_explicit_and_never_enumerates_provider_environment(self):
        environ = mock.MagicMock()
        environ.__iter__.side_effect = AssertionError('parent environment enumeration forbidden')
        with mock.patch.object(cli.os, 'environ', environ), \
                mock.patch.object(cli.pwd, 'getpwuid', return_value=SimpleNamespace(pw_name='fixture')):
            value = cli.owner_environment(OWNER)
            with _child_identity_context({'job_id': 'd' * 32, 'request_id': 'original'}, value) as child:
                self.assertEqual(child['HOME'], OWNER.home)
                self.assertEqual(child['SANDBOX_DURABLE_JOB_ID'], 'd' * 32)
                self.assertEqual(child['SANDBOX_DURABLE_REQUEST_ID'], 'original')
                self.assertNotIn('CLOUDFLARE_API_TOKEN', child)
                self.assertNotIn('SSH_AUTH_SOCK', child)
        environ.__iter__.assert_not_called()

    def test_typed_owner_value_alone_cannot_authorize_local_dispatch(self):
        with mock.patch.object(cli, 'current_server_cli_context', side_effect=cli.ServerCLIUnavailable('missing job')), \
                mock.patch('sandbox.services.process.BoundedProcessRunner.run') as run:
            with self.assertRaises(cli.ServerCLIUnavailable):
                cli.owner_process(OWNER, 'single-effect')
        run.assert_not_called()


class ServerCLIDispatchTest(unittest.TestCase):
    def dispatch(self, *, capability=None):
        record = {'joined': True, 'provisioned': True, 'control_transport': 'tailscale',
            'mcp_service': {'ownership_marker': 'a' * 24, 'runtime_revision': 'b' * 24}}
        observed = {'schema_version': 1, 'ok': True, 'target_identity': 'c' * 24,
            'ownership_marker': 'a' * 24, 'runtime_revision': 'b' * 24,
            'capabilities': [cli.CONTRACT]}
        if capability:
            observed.update(capability)
        with mock.patch.object(_remote, 'get_remote', return_value=record), \
                mock.patch('sandbox.resources.context.authenticated_target_identity',
                           return_value={'target_identity': 'c' * 24}), \
                mock.patch('sandbox.transports.remote_jobs.RemoteJobTransport') as factory:
            transport = factory.return_value
            transport.control.return_value = observed
            transport.submit_server_cli.return_value = {'ok': True, 'job_id': 'd' * 32}
            try:
                result = cli.dispatch_server_cli('box', ['host', 'stack', 'list'],
                    project_dir='/synthetic/project', request_id='original-request', timeout_seconds=90)
            except cli.ServerCLIUnavailable as error:
                result = error
            return result, transport

    def test_original_request_source_and_deadline_reach_real_durable_adapter(self):
        result, transport = self.dispatch()
        self.assertEqual(result['job_id'], 'd' * 32)
        submission = transport.submit_server_cli.call_args.args[0]
        self.assertEqual(submission.request_id, 'original-request')
        self.assertEqual(submission.deadline_seconds, 90)
        self.assertEqual(submission.argv, ('host', 'stack', 'list'))
        self.assertNotEqual(submission.project_identity, 'server-cli')
        transport.control.assert_called_once_with('box', ['job-capabilities'])

    def test_missing_contract_or_real_target_pin_change_cannot_submit(self):
        for fields in ({'capabilities': []}, {'target_identity': 'e' * 24},
                       {'ownership_marker': 'f' * 24}, {'runtime_revision': 'e' * 24},
                       {'schema_version': True}):
            with self.subTest(fields=fields):
                result, transport = self.dispatch(capability=fields)
                self.assertIsInstance(result, cli.ServerCLIUnavailable)
                transport.submit_server_cli.assert_not_called()


class RetainedServerCLIContextTest(unittest.TestCase):
    def observe(self, *, row_fields=None, process_fields=None, dispatch=False):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            registry = runtime / 'jobs/registry.sqlite3'
            registry.parent.mkdir()
            registry.write_bytes(b'synthetic-placeholder-only')
            process = {'child_pid': 1234, 'supervisor_pid': 1000, 'host_boot_id': 'boot',
                       'child_start_identity': 'child-start', 'supervisor_start_identity': 'parent-start'}
            process.update(process_fields or {})
            row = {'kind': cli.KIND, 'target_kind': 'local', 'lifecycle': 'running',
                'request_id': 'original', 'command_json': json.dumps([OWNER.executable, 'host', 'stack', 'list']),
                'started_at': datetime.now(timezone.utc).isoformat(), 'deadline_seconds': 90,
                'project_root': '/synthetic/source', 'project_identity': 'canonical-project',
                'source_identity': 'source-tree', 'source_commit': 'f' * 40, 'source_dirty_digest': None,
                'execution_policy_provenance_json': json.dumps({
                    'server_cli.target_identity': OWNER.target_identity,
                    'server_cli.runtime_revision': OWNER.runtime_revision,
                    'server_cli.ownership_marker': OWNER.ownership_marker,
                    'server_cli.remote_name': 'origin-box'}), 'process': process}
            row.update(row_fields or {})
            def captured(pid):
                return SimpleNamespace(host_boot_id='boot',
                    start_identity='child-start' if pid == 1234 else 'parent-start')
            with mock.patch.object(_paths, 'RUNTIME_DIR', runtime), \
                    mock.patch.dict(os.environ, {'SANDBOX_DURABLE_JOB_ID': 'd' * 32}), \
                    mock.patch.object(cli.os, 'getpid', return_value=1234), \
                    mock.patch.object(cli.os, 'getppid', return_value=1000), \
                    mock.patch.object(cli, '_current_owner', return_value=OWNER), \
                    mock.patch.object(cli, 'installed_cli', return_value=Path(OWNER.executable)), \
                    mock.patch.object(cli.pwd, 'getpwuid', return_value=SimpleNamespace(pw_name='fixture')), \
                    mock.patch('sandbox.jobs.registry.JobRepository') as repository, \
                    mock.patch('sandbox.jobs.process.capture_process_identity', side_effect=captured), \
                    mock.patch('sandbox.services.process.BoundedProcessRunner.run', return_value=SimpleNamespace(
                        returncode=0, argv=('/bin/sh', '-c', 'single-effect'), stdout='', stderr='',
                        stdout_truncated=False, stderr_truncated=False, termination_reason=None)) as run:
                repository.return_value.snapshot.return_value = row
                try:
                    context = cli.current_server_cli_context()
                    if dispatch:
                        cli.owner_process(context.owner, 'single-effect', timeout=2)
                    return context, run
                except cli.ServerCLIUnavailable as exc:
                    return exc, run

    def test_original_retained_child_exposes_typed_target_and_local_process_channel(self):
        context, run = self.observe(dispatch=True)
        self.assertEqual(context.owner, OWNER)
        self.assertEqual(context.request_id, 'original')
        self.assertEqual(context.origin_remote_name, 'origin-box')
        self.assertEqual(context.source_commit, 'f' * 40)
        self.assertEqual(run.call_args.args[0], ['/bin/sh', '-c', 'single-effect'])
        self.assertNotIn('SSH_AUTH_SOCK', run.call_args.kwargs['env'])
        self.assertEqual(run.call_args.kwargs['env']['HOME'], OWNER.home)
        self.assertEqual(run.call_count, 1)

    def test_client_environment_key_cannot_replace_original_process_source_or_target(self):
        for rows, processes in (({'kind': 'exec'}, {}),
                ({'command_json': '["/synthetic/pr-script"]'}, {}),
                ({'lifecycle': 'cancelled'}, {}), ({'request_id': None}, {}),
                ({'execution_policy_provenance_json': '{}'}, {}),
                ({}, {'child_pid': 4321}), ({}, {'supervisor_pid': 999}),
                ({}, {'host_boot_id': 'foreign-boot'}), ({}, {'child_start_identity': 'reused-pid'}),
                ({}, {'supervisor_start_identity': 'reused-parent'})):
            with self.subTest(rows=rows, processes=processes):
                result, run = self.observe(row_fields=rows, process_fields=processes, dispatch=True)
                self.assertIsInstance(result, cli.ServerCLIUnavailable)
                run.assert_not_called()
