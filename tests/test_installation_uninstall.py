"""Legacy teardown cannot cross the managed boundary when identity is missing."""
import contextlib
import io
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock
from sandbox.commands import uninstall
from sandbox.installation import binding


class ManagedUninstallGuardTest(unittest.TestCase):
    def test_mode_or_fixed_managed_path_refuses_before_workload_resolution(self):
        for environment, base in (({'SANDBOX_MANAGED': '1'}, Path('/private/fixture-state')),
                                  ({}, Path('/var/lib/sandbox'))):
            with self.subTest(environment=environment, base=base), \
                    mock.patch.dict(os.environ, environment, clear=True), \
                    mock.patch.object(uninstall, 'BASE', base), \
                    mock.patch.object(binding, 'is_managed_layout', return_value=False), \
                    mock.patch.object(uninstall, 'resolve_instances', side_effect=AssertionError('workload touched')) as resolve, \
                    contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit):
                    uninstall.cmd_uninstall({}, SimpleNamespace())
                resolve.assert_not_called()
