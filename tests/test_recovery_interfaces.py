import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.commands.recovery import cmd_recovery


class TestRecoveryInterfaces(unittest.TestCase):
    def _args(self, action, **extra):
        return SimpleNamespace(action=action, remote=None, profile=[], backup_id=None,
                               confirm=False, json=True, **extra)

    def test_cli_create_needs_confirmation_before_secret_or_capture(self):
        with self.assertRaises(SystemExit), patch.dict(os.environ, {}, clear=True):
            cmd_recovery(None, self._args("create"))

    def test_cli_verify_needs_a_backup_id(self):
        with self.assertRaises(SystemExit):
            cmd_recovery(None, self._args("verify"))

    def test_cli_restore_needs_a_backup_id_and_never_applies_by_default(self):
        with self.assertRaises(SystemExit):
            cmd_recovery(None, self._args("restore"))
