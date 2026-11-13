"""Actual installer shell with synthetic files and fake service/tool ports."""
import base64
from pathlib import Path
import os
import shlex
import sys
import tempfile
import unittest
from unittest.mock import patch

from sandbox.core import _remote as remote
from tests.subprocess_support import run_test_process
from tests.test_remote import _completed, _staged_source


class JoinedOwnerPreservationShellTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory(dir=Path.home()))).resolve()
        self.bind, self.port = '100.64.1.2', 9174
        self.old_revision = 'a' * 24
        self.record = remote.remote_mcp_service_record(self.bind, self.port)
        old_record = {**self.record, 'runtime_revision': self.old_revision}
        self.old_unit = remote.render_remote_mcp_unit(self.bind, self.port, bound_record=old_record)
        self.entry = {'joined': True, 'control_transport': 'tailscale', 'control_access': 'tailnet',
            'ssh': 'alim@100.64.1.2', 'tailscale_host': self.bind,
            'mcp_service': {'ownership_marker': self.record['ownership_marker'],
                            'runtime_revision': self.old_revision}}
        with patch.object(remote, 'ssh_run', return_value=_completed()) as transport, \
             patch.object(remote, '_record_controller_hint'):
            remote.migrate_remote_mcp_service(self.entry, self.bind, self.port, None,
                confirm=True, preserve_credentials=True, source_revision='f' * 40,
                staged_source=_staged_source())
        self.command = transport.call_args.args[1]
        self.assertIsNone(transport.call_args.kwargs['input_data'])
        self.runtime = self.home / 'sandbox/sb-src'
        self.runtime.mkdir(parents=True)
        (self.runtime / 'identity').write_text('old')
        self.stage = self.home / 'sandbox' / _staged_source()['stage_name']
        self.tree = self.stage / 'tree'
        (self.tree / 'scripts').mkdir(parents=True)
        (self.tree / 'identity').write_text('new')
        (self.tree / 'sandbox/hermes').mkdir(parents=True)
        (self.tree / 'sandbox/hermes/cron-catalog.json').write_text('{}')
        self.executable(self.tree / 'sb', '#!/bin/sh\nexit 0\n')
        self.stage.chmod(0o700); self.tree.chmod(0o700)
        receipt = self.stage / 'receipt.json'
        receipt.write_text(_staged_source()['receipt']); receipt.chmod(0o600)
        self.unit = self.home / '.config/systemd/user' / remote.REMOTE_MCP_SERVICE
        self.unit.parent.mkdir(parents=True)
        self.unit.write_text(self.old_unit); self.unit.chmod(0o600)
        self.credential = self.home / '.sandbox/mcp-remote.env'
        self.credential.parent.mkdir(mode=0o700)
        self.canary = b'SYNTHETIC_OWNER_CREDENTIAL=fixture-only-recipient\n'
        self.credential.write_bytes(self.canary); self.credential.chmod(0o600)
        self.bin = self.home / 'bin'; self.bin.mkdir()
        self.log = self.home / 'service.log'
        self.executable(self.bin / 'systemctl', '#!/bin/sh\nprintf "%s\\n" "$*" >> "$SERVICE_LOG"\n'
            'case "$*" in *"show --property=LoadState --property=ActiveState --value"*) '
            'printf "loaded\\nactive\\n";; *) exit 0;; esac\n')
        self.executable(self.bin / 'loginctl', '#!/bin/sh\nexit 0\n')
        # Execute the real metadata/activation/rollback Python programs. Only
        # the optional tool-venv provisioning port is replaced with a no-op.
        tools = 'from sandbox.core._config import ensure_tools_venv; ensure_tools_venv()'
        self.executable(self.bin / 'python3', '#!' + sys.executable + '\nimport os,sys\n'
            'if len(sys.argv)>2 and sys.argv[1]=="-c" and sys.argv[2]==' + repr(tools) + ':\n'
            ' raise SystemExit(0)\n'
            'os.execv(sys.executable,[sys.executable,*sys.argv[1:]])\n')
        self.executable(self.bin / 'stat', '#!' + sys.executable + '\nimport os,stat,sys\n'
            'i=os.stat(sys.argv[3]);print(sys.argv[2].replace("%u",str(i.st_uid))'
            '.replace("%a",oct(stat.S_IMODE(i.st_mode))[2:]))\n')
        (self.tree / '.cli-venv/bin').mkdir(parents=True)
        (self.tree / '.cli-venv/bin/python').symlink_to(self.bin / 'python3')
        (self.tree / 'scripts/provision_image_stage_helper.py').write_text(
            'import os\nraise SystemExit(1 if os.environ.get("FAIL_HELPER")=="1" else 0)\n')

    def executable(self, path, text):
        path.write_text(text); path.chmod(0o700)

    def invoke(self, *, fail=False):
        self.assertNotIn(self.canary.decode().strip(), self.command)
        self.assertNotIn(base64.b64encode(self.canary).decode(), self.command)
        result = run_test_process(('/bin/sh', '-c', self.command), input=b'',
            capture_output=True, timeout=15, env={'HOME': str(self.home), 'USER': 'fixture',
                'PATH': str(self.bin) + ':/usr/bin:/bin', 'SERVICE_LOG': str(self.log),
                'FAIL_HELPER': '1' if fail else '0'})
        self.assertNotIn(self.canary, result.stdout + result.stderr)
        self.assertNotIn(base64.b64encode(self.canary), result.stdout + result.stderr)
        return result

    def test_success_preserves_existing_recipient_bytes_mode_and_inode_without_input(self):
        inode = self.credential.stat().st_ino
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.credential.read_bytes(), self.canary)
        self.assertEqual(self.credential.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.credential.stat().st_ino, inode)
        self.assertEqual((self.runtime / 'identity').read_text(), 'new')
        self.assertIn(self.record['runtime_revision'], self.unit.read_text())
        self.assertFalse(self.stage.exists())

    def test_failed_install_restores_old_source_unit_and_preserves_recipient(self):
        result = self.invoke(fail=True)
        self.assertEqual(result.returncode, 1, result.stderr.decode())
        self.assertEqual((self.runtime / 'identity').read_text(), 'old')
        self.assertEqual(self.unit.read_text(), self.old_unit)
        self.assertEqual(self.credential.read_bytes(), self.canary)
        self.assertEqual(self.credential.stat().st_mode & 0o777, 0o600)
        self.assertIn('restart ' + remote.REMOTE_MCP_SERVICE, self.log.read_text())

    def test_changed_unit_revision_refuses_before_exchange(self):
        self.unit.write_text(self.old_unit.replace(self.old_revision, 'c' * 24))
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.runtime / 'identity').read_text(), 'old')
        self.assertEqual(self.credential.read_bytes(), self.canary)
        self.assertFalse(self.log.exists())

    def test_symlink_stage_root_refuses_before_exchange_or_service_mutation(self):
        backing = self.home / 'synthetic-stage-backing'
        self.stage.rename(backing)
        self.stage.symlink_to(backing, target_is_directory=True)
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.runtime / 'identity').read_text(), 'old')
        self.assertEqual(self.credential.read_bytes(), self.canary)
        self.assertNotIn('restart ' + remote.REMOTE_MCP_SERVICE, self.log.read_text())
        self.assertTrue(self.stage.is_symlink())

    def test_symlink_runtime_refuses_without_replacing_its_original_target(self):
        backing = self.home / 'synthetic-old-runtime'
        self.runtime.rename(backing)
        self.runtime.symlink_to(backing, target_is_directory=True)
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.runtime.is_symlink())
        self.assertEqual((backing / 'identity').read_text(), 'old')
        self.assertEqual((self.tree / 'identity').read_text(), 'new')
        self.assertNotIn('restart ' + remote.REMOTE_MCP_SERVICE, self.log.read_text())

    def test_credential_symlink_or_nonprivate_mode_refuses_before_exchange(self):
        for linked in (False, True):
            with self.subTest(linked=linked):
                if linked:
                    backing = self.home / 'synthetic-recipient'
                    self.credential.rename(backing)
                    backing.chmod(0o600)
                    self.credential.symlink_to(backing)
                else:
                    self.credential.chmod(0o644)
                result = self.invoke()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((self.runtime / 'identity').read_text(), 'old')
                self.assertFalse(self.log.exists())
