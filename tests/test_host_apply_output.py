"""Synthetic initialized application output; no secret source or host is used."""
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from types import MappingProxyType
import unittest
from unittest.mock import Mock, patch

from sandbox.application import server_cli as sdk
from sandbox.commands import hosting
from sandbox.hosting import apply_output, server_cli_target as targets
from tests.subprocess_support import run_test_process


class ApplyOutputTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.canary = 'opaqueSyntheticApplicationCredentialCanary42'
        self.owner = sdk.ServerCliOwner(os.geteuid(), str(self.root), str(self.root),
            str(Path(__file__).resolve().parents[1] / 'sb'), 'a' * 24, 'b' * 24, 'c' * 24)
        self.context = sdk.ServerCLIContext(1, hashlib.sha256(self.id().encode()).hexdigest()[:32],
            'original-apply', str(self.root), 'project', 'source', 'e' * 40, None,
            'a' * 24, 'c' * 24, (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat(),
            ('host', 'apply'), 'box', MappingProxyType({'front_door': 'nginx'}), self.owner)
        self.current = self.enterContext(patch.object(sdk, 'validate_owner_current', return_value=self.context))
        self.selected = self.enterContext(patch.object(sdk, 'server_cli_context_if_selected', return_value=self.context))
        self.entry = targets.selected_owner_target('box')
        self.environment = {'HOME': str(self.root), 'SANDBOX_HOME': str(self.root),
            'USER': 'fixture', 'LOGNAME': 'fixture', 'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8',
            'PYTHONNOUSERSITE': '1', 'XDG_RUNTIME_DIR': str(self.root),
            'DBUS_SESSION_BUS_ADDRESS': 'unix:path=' + str(self.root / 'bus')}
        self.enterContext(patch.object(sdk, 'owner_environment', return_value=self.environment))
        self.calls = []
        def local(owner, command, *, timeout, input_data=None):
            self.calls.append((command, timeout))
            self.assertIsInstance(input_data, str)
            self.assertIn(self.canary, json.loads(input_data)['exact_values'])
            self.assertNotIn(self.canary, command)
            self.assertNotIn(base64.b64encode(self.canary.encode()).decode(), command)
            return run_test_process(['/bin/sh', '-c', command], input=input_data,
                env=self.environment, capture_output=True, text=True, timeout=min(10, timeout))
        self.process = self.enterContext(patch.object(sdk, 'owner_process', side_effect=local))

    def initializer(self, code, *, split=False):
        # This is synthetic executable fixture source, not a credential file.
        # The actual runner never writes its resolved values to fixture/source.
        path = self.root / ('initializer-' + str(code) + '.py')
        path.write_text('import os,sys\n'
            + ('sys.stdout.write("safe " * 13106);sys.stdout.flush()\n' if split else '')
            + 'sys.stdout.write("public stdout " + ' + repr(self.canary) + ' + "\\n");sys.stdout.flush()\n'
            + 'sys.stderr.write("public stderr " + ' + repr(self.canary) + ' + "\\n");sys.stderr.flush()\n'
            + 'print("original_group=" + str(os.getpgrp()))\n'
            + 'sys.exit(' + str(code) + ')\n')
        return shlex.join([sys.executable, str(path)])

    def test_bare_stdout_stderr_are_scrubbed_before_log_progress_result_error_and_later_read(self):
        for code in (0, 7, 255):
            with self.subTest(exit_code=code):
                self.calls.clear()
                log = self.root / ('apply-' + str(code) + '.log')
                progress = []
                with apply_output.owner_apply_output_scope(self.entry):
                    apply_output.bind_owner_apply_output(self.entry, [self.canary])
                    if code:
                        with self.assertRaises(hosting.HostRemoteCommandFailed) as failure:
                            hosting._remote_checked(self.entry, self.initializer(code), timeout=9,
                                progress=progress.append, log_path=str(log), log_phase='initializer_run')
                        self.assertEqual(failure.exception.exit_code, code)
                        returned = str(failure.exception)
                    else:
                        returned = hosting._remote_checked(self.entry, self.initializer(code), timeout=9,
                            progress=progress.append, log_path=str(log), log_phase='initializer_run')
                retained = log.read_text()
                later = run_test_process(['/bin/sh', '-c', hosting._host_apply_log_read_command(str(log), 100)],
                    env=self.environment, text=True, capture_output=True, timeout=5)
                self.assertEqual(later.returncode, 0)
                for text in (retained, returned, '\n'.join(progress), later.stdout, later.stderr,
                             '\n'.join(command for command, _timeout in self.calls)):
                    self.assertNotIn(self.canary, text)
                    self.assertNotIn(base64.b64encode(self.canary.encode()).decode(), text)
                self.assertIn('public stdout [REDACTED]', retained)
                self.assertIn('public stderr [REDACTED]', retained)
                self.assertIn('public stdout [REDACTED]', '\n'.join(progress))
                self.assertIn('public stderr [REDACTED]', '\n'.join(progress))
                self.assertIn('public stdout [REDACTED]', returned)
                self.assertIn('public stderr [REDACTED]', returned)
                self.assertIn('original_group=' + str(os.getpgrp()), retained)
                self.assertIn('event=started', retained)
                self.assertIn('event=finished', retained)
                self.assertTrue(retained.rstrip().endswith('exit=' + str(code)))
                self.assertEqual(len(self.calls), 1, 'initializer must not be replayed')
                self.assertEqual(self.calls[0][1], 9)
                self.assertNotIn('tee -a', self.calls[0][0])
                self.assertEqual(log.stat().st_mode & 0o777, 0o600)

    def test_exact_filter_withholds_a_value_split_across_pipe_chunks(self):
        log = self.root / 'split.log'
        with apply_output.owner_apply_output_scope(self.entry):
            apply_output.bind_owner_apply_output(self.entry, [self.canary])
            result = hosting._remote_checked(self.entry, self.initializer(0, split=True), timeout=9,
                log_path=str(log), log_phase='initializer_run')
        self.assertNotIn(self.canary, log.read_text())
        self.assertNotIn(self.canary, result)
        self.assertIn('public stdout [REDACTED]', result)
        self.assertIn('public stderr [REDACTED]', result)

    def test_missing_resolved_scope_refuses_before_command_dispatch(self):
        for scope in (False, True):
            with self.subTest(scope=scope):
                manager = apply_output.owner_apply_output_scope(self.entry)
                if scope:
                    manager.__enter__()
                try:
                    with self.assertRaises(apply_output.OutputUnavailable):
                        hosting._remote_checked(self.entry, 'true', log_path=str(self.root / 'missing.log'))
                finally:
                    if scope:
                        manager.__exit__(None, None, None)
        self.process.assert_not_called()

    def test_value_or_reversible_encoding_in_argv_refuses_before_dispatch(self):
        with apply_output.owner_apply_output_scope(self.entry):
            apply_output.bind_owner_apply_output(self.entry, [self.canary])
            for value in (self.canary, base64.b64encode(self.canary.encode()).decode()):
                with self.subTest(encoding=len(value)), self.assertRaises(apply_output.OutputUnavailable) as failure:
                    hosting._remote_checked(self.entry, 'printf %s ' + shlex.quote(value),
                        log_path=str(self.root / 'unsafe.log'))
                self.assertNotIn(self.canary, str(failure.exception))
        self.process.assert_not_called()

    def test_unknown_receipt_keeps_original_fence_and_withholds_apply_lease_retirement(self):
        self.process.side_effect = None
        self.process.return_value = subprocess.CompletedProcess([], 0, stdout='malformed ' + self.canary, stderr='')
        def unleased(*args, **kwargs):
            apply_output.bind_owner_apply_output(self.entry, [self.canary])
            return hosting._remote_checked(self.entry, 'true', log_path=str(self.root / 'unknown.log'))
        with patch.object(hosting, 'guard_server_execution', create=True), \
                patch.object(hosting, '_acquire_host_apply_lease', return_value={'fixture': True}), \
                patch.object(hosting, '_apply_host_unleased', side_effect=unleased), \
                patch.object(hosting, '_release_host_apply_lease') as release, \
                self.assertRaises(sdk.ServerCLIEffectUnknown) as failure:
            hosting._apply_host({}, self.entry, 'box', {})
        self.assertNotIn(self.canary, str(failure.exception))
        self.assertTrue(targets.owner_effect_unknown(self.entry))
        self.process.assert_called_once()
        release.assert_not_called()

    def test_log_symlink_refuses_before_initializer_and_never_modifies_target(self):
        destination = self.root / 'unrelated-public.txt'
        destination.write_text('keep this')
        log = self.root / 'unsafe.log'
        log.symlink_to(destination)
        with apply_output.owner_apply_output_scope(self.entry):
            apply_output.bind_owner_apply_output(self.entry, [self.canary])
            with self.assertRaises(apply_output.OutputUnavailable):
                hosting._remote_checked(self.entry, self.initializer(0),
                    log_path=str(log), log_phase='initializer_run')
        self.assertEqual(destination.read_text(), 'keep this')
        self.assertFalse(targets.owner_effect_unknown(self.entry))

    def test_post_child_log_sync_failure_is_unknown_not_pre_effect_refusal(self):
        process = Mock(stdout=io.BytesIO(('public ' + self.canary + '\n').encode()))
        process.wait.return_value = 0
        with patch.object(apply_output.subprocess, 'Popen', return_value=process), \
                patch.object(apply_output.os, 'fsync', side_effect=OSError('synthetic sync failure')):
            result = apply_output._run('synthetic', 'initializer_run', str(self.root / 'sync.log'),
                                       [self.canary], self.environment)
        self.assertEqual(result, {'schema_version': 1, 'status': 'unknown'})
        self.assertNotIn(self.canary, (self.root / 'sync.log').read_text())

    def test_scope_reset_does_not_reuse_another_original_jobs_exact_set(self):
        with apply_output.owner_apply_output_scope(self.entry):
            apply_output.bind_owner_apply_output(self.entry, [self.canary])
            outer = apply_output._CURRENT.get()
            other = replace(self.context, job_id='f' * 32, request_id='other')
            self.current.return_value = self.selected.return_value = other
            other_entry = targets.selected_owner_target('box')
            with apply_output.owner_apply_output_scope(other_entry):
                apply_output.bind_owner_apply_output(other_entry, ['OtherSyntheticValue'])
                self.assertEqual(apply_output._CURRENT.get().values, ('OtherSyntheticValue',))
            self.current.return_value = self.selected.return_value = self.context
            self.assertIs(apply_output._CURRENT.get(), outer)
        self.assertIsNone(apply_output._CURRENT.get())
