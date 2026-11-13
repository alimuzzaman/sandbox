"""Canonical CLI selection preserves option ordering without sensitive bypasses."""
import argparse
import contextlib
import io
from pathlib import Path
import unittest
from unittest import mock

from sandbox.application import command_selection, server_cli
from sandbox import cli
from sandbox.commands import secrets


class CanonicalSelectionTests(unittest.TestCase):
    def setUp(self):
        parser = argparse.ArgumentParser()
        parser.add_argument('--label')
        parser.add_argument('--instance')
        commands = parser.add_subparsers(dest='cmd', required=True)
        secret_parser = commands.add_parser('secrets')
        secrets.configure_parser(secret_parser)
        secret_parser.add_argument('--label')
        host = commands.add_parser('host')
        host.add_argument('action', choices=['stack', 'exec'])
        host.add_argument('--remote')
        host.add_argument('--label')
        host.add_argument('stack_action', nargs='?')
        self.token = command_selection.bind_cli_command_parser(parser, cli._selection_argv)
        self.addCleanup(command_selection.reset_cli_command_parser, self.token)

    def test_valid_options_before_host_action_keep_original_argv(self):
        values = ('--strict-runtime', 'host', '--remote', 'box', 'stack', 'list')
        with mock.patch.object(server_cli, 'installed_cli', return_value=Path('/fixed/sb')):
            self.assertEqual(server_cli.fixed_argv(values), ('/fixed/sb', *values))

    def test_real_secret_parser_refuses_global_and_common_option_variants(self):
        for prefix in ((), ('--lab', 'fixture'), ('--instance=fixture',)):
            for common in ((), ('--lab', 'fixture')):
                for action, suffix in (('run', ('--source', 'personal', '--key', 'FIXTURE', '--', 'true')),
                                       ('reveal', ('--source', 'personal', '--key', 'FIXTURE'))):
                    values = (*prefix, 'secrets', *common, action, *suffix)
                    with self.subTest(values=values), mock.patch.object(server_cli, 'installed_cli') as installed:
                        self.assertEqual(command_selection.selected_command(values), ('secrets', action))
                        with self.assertRaises(server_cli.ServerCLIUnavailable):
                            server_cli.fixed_argv(values)
                        installed.assert_not_called()

    def test_host_exec_with_options_before_action_still_refuses_missing_stdin(self):
        for prefix in ((), ('--lab', 'fixture')):
            with self.subTest(prefix=prefix), self.assertRaises(server_cli.ServerCLIStdinUnavailable):
                server_cli.fixed_argv((*prefix, 'host', '--remote', 'box', 'exec', '--', 'cat'))

    def test_passthrough_is_not_silently_removed_from_another_host_action(self):
        with self.assertRaises(server_cli.ServerCLIUnavailable):
            server_cli.fixed_argv(('host', 'stack', 'list', '--', 'ignored-command'))

    def test_invalid_parser_input_is_silent_and_never_dispatches(self):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            with self.assertRaises(server_cli.ServerCLIUnavailable):
                server_cli.fixed_argv(('host', '--not-installed', 'fixture', 'stack'))
        self.assertEqual((output.getvalue(), errors.getvalue()), ('', ''))

    def test_selection_does_not_modify_parent_environment_or_original_parser(self):
        with mock.patch.object(cli.os, 'environ', {}) as environment:
            self.assertEqual(command_selection.selected_command(['--strict-runtime', 'host', 'stack', 'list']),
                             ('host', 'stack'))
            self.assertEqual(environment, {})
        self.assertEqual(command_selection.selected_command(['host', '--remote', 'box', 'stack', 'list']),
                         ('host', 'stack'))


class StrictForwardingTests(unittest.TestCase):
    def test_explicit_strict_flag_survives_forwarding_once(self):
        values = ['host', '--strict-runtime', 'stack', 'list']
        self.assertEqual(cli._forwarded_cli_argv(values, strict=True), tuple(values))
        self.assertNotIn('--strict-runtime', cli._selection_argv(values))

    def test_current_strict_intent_is_forwarded_without_environment_values(self):
        self.assertEqual(cli._forwarded_cli_argv(['host', 'stack', 'list'], strict=True),
                         ('--strict-runtime', 'host', 'stack', 'list'))

    def test_passthrough_flag_is_not_controller_strict_selection(self):
        values = ['host', 'exec', '--', 'echo', '--strict-runtime']
        self.assertEqual(cli._forwarded_cli_argv(values, strict=False), tuple(values))
        self.assertEqual(cli._forwarded_cli_argv(values, strict=True), ('--strict-runtime', *values))


class InstalledInputSelectionTests(unittest.TestCase):
    def setUp(self):
        # Build the actual installed parser, stopping before its first parse or
        # any configuration, update, credential lookup or command dispatch.
        previous = command_selection.bind_cli_command_parser(argparse.ArgumentParser(), lambda x: x)
        self.addCleanup(command_selection.reset_cli_command_parser, previous)
        class ParserCaptured(Exception):
            pass
        def capture(parser, *args, **kwargs):
            self.parser = parser
            raise ParserCaptured()
        with mock.patch.object(argparse.ArgumentParser, 'parse_args', capture), \
             mock.patch.object(cli.sys, 'argv', ['sb']), \
             self.assertRaises(ParserCaptured):
            cli.main()

    def test_installed_stdin_spellings_refuse_before_lookup_or_acceptance(self):
        commands = []
        for flag in ('--stdin', '--std', '--stdi'):
            commands.extend([
                ('secrets', 'credential-set', '--source', 'personal', '--create-only', flag),
                ('secrets', 'set', '--source', 'personal', 'FIXTURE_KEY', flag),
                ('secrets', 'ownership', 'credential-set', '--profile', 'fixture',
                 '--capability', 'read', '--create-only', flag),
            ])
        for flag in ('--token-stdin', '--token-st', '--passphrase-stdin', '--passphrase-st'):
            commands.append(('hermes', 'drive', 'backup', '--remote', 'fixture', flag))
        for values in commands:
            with self.subTest(values=values), \
                 mock.patch('sandbox.core._remote.get_remote') as lookup, \
                 mock.patch('sandbox.resources.context.authenticated_target_identity') as identity, \
                 mock.patch('sandbox.transports.remote_jobs.RemoteJobTransport.control') as control, \
                 mock.patch('sandbox.transports.remote_jobs.RemoteJobTransport.submit_server_cli') as submit:
                with self.assertRaises(server_cli.ServerCLIStdinUnavailable):
                    server_cli.dispatch_server_cli('fixture', values, project_dir='.',
                                                   request_id='stdin-selection-fixture')
                for operation in (lookup, identity, control, submit):
                    operation.assert_not_called()

    def test_option_value_matching_stdin_spelling_is_not_an_input_mode(self):
        values = ('secrets', 'source-info', '--source=--std')
        with mock.patch.object(server_cli, 'installed_cli', return_value=Path('/fixed/sb')):
            self.assertEqual(server_cli.fixed_argv(values), ('/fixed/sb', *values))
