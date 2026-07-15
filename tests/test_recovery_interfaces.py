import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.commands.recovery import cmd_recovery


class TestRecoveryInterfaces(unittest.TestCase):
    def _args(self, action, **extra):
        values = {"action": action, "remote": None, "profile": [], "backup_id": None,
                  "artifact": [], "keep_count": 1, "minimum_age_days": 0,
                  "confirm": False, "json": True}
        values.update(extra)
        return SimpleNamespace(**values)

    def test_cli_create_needs_confirmation_before_secret_or_capture(self):
        with self.assertRaises(SystemExit), patch.dict(os.environ, {}, clear=True):
            cmd_recovery(None, self._args("create"))

    def test_cli_verify_needs_a_backup_id(self):
        with self.assertRaises(SystemExit):
            cmd_recovery(None, self._args("verify"))

    def test_cli_restore_needs_a_backup_id_and_never_applies_by_default(self):
        with self.assertRaises(SystemExit):
            cmd_recovery(None, self._args("restore"))

    def test_cli_schedule_and_retention_default_to_non_mutating_plans(self):
        cmd_recovery(None, self._args("schedule"))
        with self.assertRaises(SystemExit):
            cmd_recovery(None, self._args("retention"))

    def test_cli_create_routes_explicit_materialized_inputs_to_service(self):
        service = SimpleNamespace(create=lambda *args, **kwargs: {
            "action": "create", "ok": True, "status": "complete", "data": {}
        })
        args = self._args("create", profile=["fixture"], backup_id="set-1",
                          artifact=["archive=/tmp/archive"], confirm=True)
        with patch.dict(os.environ, {"RECOVERY_PASSPHRASE": "fixture-secret"}, clear=True), \
                patch("sandbox.commands.recovery.recovery_service", return_value=service):
            cmd_recovery(None, args)

    def test_cli_create_rejects_malformed_artifact_declaration(self):
        args = self._args("create", profile=["fixture"], backup_id="set-1", artifact=["malformed"])
        with self.assertRaises(SystemExit), patch.dict(os.environ, {"RECOVERY_PASSPHRASE": "fixture-secret"}, clear=True):
            cmd_recovery(None, args)

    def test_cli_retention_routes_policy_inputs_to_service(self):
        calls = []
        service = SimpleNamespace(retention_plan=lambda *args, **kwargs: calls.append((args, kwargs)) or {
            "action": "retention", "ok": True, "status": "planned", "data": {}
        })
        with patch("sandbox.commands.recovery.recovery_service", return_value=service):
            cmd_recovery(None, self._args("retention", keep_count=3, minimum_age_days=7))
        self.assertEqual(calls, [((None,), {"keep_count": 3, "minimum_age_days": 7})])
