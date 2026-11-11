"""Exercise real status/compatibility producer with synthetic SSH unit metadata."""
import contextlib
import io
import shlex
from types import SimpleNamespace
import unittest
from unittest import mock

from sandbox.core import _remote as remote
from sandbox.remote_runtime.protocol import local_protocol


class JoinedCompatibilityProducerTest(unittest.TestCase):
    def observe(self, declarations):
        def ssh(_record, command, **_kwargs):
            words = shlex.split(command)
            programs = [words[index + 1] for index, word in enumerate(words[:-1])
                        if word == '-c' and "pprefix='Environment=SANDBOX_REMOTE_MCP_CONTROL_PROTOCOL='" in words[index + 1]]
            self.assertEqual(len(programs), 1)
            output = io.StringIO()
            with mock.patch('pathlib.Path.read_text', side_effect=declarations if isinstance(declarations, Exception) else None,
                            return_value=declarations if isinstance(declarations, str) else ''), \
                    mock.patch('sys.argv', ['probe', '/synthetic/user-unit']), contextlib.redirect_stdout(output):
                try:
                    exec(compile(programs[0], '<synthetic-unit-producer>', 'exec'), {})
                except SystemExit:
                    pass
            metadata = ('enabled=enabled\nactive=active\npid=1\nlinger=yes\n'
                        'probe_state=complete\nprobe_error=none\n'
                        'ownership=ambiguous\npid_ownership=proven\n'
                        'listener=unknown\nauth=unknown\nlegacy_pidfile=absent\n')
            return SimpleNamespace(returncode=0, stdout=metadata + output.getvalue())
        with mock.patch.object(remote, 'ssh_run', side_effect=ssh), \
                mock.patch.object(remote, '_remote_mcp_runtime_revision', return_value='a' * 24), \
                mock.patch('sandbox.remote_runtime.verdict.strict_requested', return_value=False):
            return remote.remote_mcp_service_status({'ssh': 'owner@synthetic',
                'mcp_service': {'ownership_marker': 'b' * 24, 'runtime_revision': 'c' * 24}})

    def unit(self, protocol=None):
        return ('Environment=SANDBOX_REMOTE_MCP_RUNTIME_REVISION=' + 'c' * 24 + '\n'
                'Environment=SANDBOX_REMOTE_MCP_CONTROL_PROTOCOL=' + (protocol or local_protocol().encode()))

    def test_partial_registration_can_report_compatible_actual_user_unit(self):
        status = self.observe(self.unit())
        self.assertEqual(status['compatibility']['state'], 'compatible')
        self.assertEqual(status['runtime_revision_state'], 'mismatch')
        self.assertFalse(status['authenticated'])
        self.assertEqual(status['auth_state'], 'unknown')
        self.assertNotEqual(status['ownership'], 'proven')

    def test_missing_actual_user_unit_cannot_authorize_compatibility(self):
        self.assertNotEqual(self.observe(FileNotFoundError())['compatibility']['state'], 'compatible')

    def test_malformed_duplicate_or_incompatible_declarations_refuse(self):
        for unit in (self.unit('malformed'), self.unit() + '\n' + self.unit(),
                     self.unit('1:1'), self.unit('1:2')):
            with self.subTest(unit=unit):
                self.assertNotEqual(self.observe(unit)['compatibility']['state'], 'compatible')
