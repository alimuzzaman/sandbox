"""Existing supported owner install initializes identity before restarting service."""
import unittest
from unittest import mock
from sandbox.core import _remote as sr
from tests.test_remote import _completed, _staged_source


class OwnerIdentityLifecycleTest(unittest.TestCase):
    def test_supported_owner_install_initializes_without_claiming_capabilities(self):
        with mock.patch.object(sr, 'ssh_run', return_value=_completed(returncode=0)) as run, \
                mock.patch.object(sr, '_record_controller_hint'):
            result = sr.start_remote_mcp_server({'ssh': 'fixture'}, '127.0.0.1', 9174,
                'z' * 64, source_revision='f' * 40, staged_source=_staged_source())
        command = run.call_args.args[1]
        self.assertIn('installation initialize --home "$HOME/sandbox"', command)
        self.assertIn('--layout owner --ownership-marker ' + result['service']['ownership_marker'], command)
        self.assertLess(command.index('installation initialize'), command.index('systemctl --user restart'))
        self.assertNotIn('hosting.baseline.worker', command)
