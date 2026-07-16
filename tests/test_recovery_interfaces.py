import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from io import StringIO
from contextlib import redirect_stdout

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

    def test_cli_retention_human_output_shows_reviewable_plan(self):
        service = SimpleNamespace(retention_plan=lambda *args, **kwargs: {
            "action": "retention", "ok": True, "status": "planned", "data": {
                "protected_sets": ("new",), "candidates": ("old",),
                "unclassified": ({"id": "legacy", "reason": "invalid_manifest"},),
            }
        })
        output = StringIO()
        with patch("sandbox.commands.recovery.recovery_service", return_value=service), \
                redirect_stdout(output):
            cmd_recovery(None, self._args("retention", json=False))
        self.assertIn("protected: new", output.getvalue())
        self.assertIn("candidates: old", output.getvalue())
        self.assertIn("unclassified: legacy (invalid_manifest)", output.getvalue())

    def test_cli_list_human_output_shows_categorized_paths(self):
        service = SimpleNamespace(list=lambda *args, **kwargs: {
            "action": "list", "ok": True, "status": "listed", "data": {
                "complete_manifests": ({"Path": "sets/new/manifest.json"},),
                "incomplete": ({"Path": "sets/pending/archive.bin"},),
                "legacy": ({"Path": "legacy.tar"},),
                "unverifiable": ({"Path": "sets/broken/manifest.json"},),
                "locally_pending": ({"Path": "/tmp/retry.archive.tar.gpg"},),
            }
        })
        output = StringIO()
        with patch("sandbox.commands.recovery.recovery_service", return_value=service), \
                redirect_stdout(output):
            cmd_recovery(None, self._args("list", json=False))
        rendered = output.getvalue()
        self.assertIn("complete: 1", rendered)
        self.assertIn("sets/pending/archive.bin", rendered)
        self.assertIn("legacy.tar", rendered)
        self.assertIn("/tmp/retry.archive.tar.gpg", rendered)

    def test_cli_verify_human_output_shows_non_secret_integrity_summary(self):
        service = SimpleNamespace(verify=lambda *args, **kwargs: {
            "action": "verify", "ok": True, "status": "verified", "data": {
                "id": "set-1", "manifest": {
                    "ciphertext_object": "sets/set-1/archive.bin",
                    "ciphertext_sha256": "a" * 64, "ciphertext_size": 128,
                    "provenance": {"secret": "must-not-print"},
                },
            }
        })
        output = StringIO()
        with patch("sandbox.commands.recovery.recovery_service", return_value=service), \
                redirect_stdout(output):
            cmd_recovery(None, self._args("verify", backup_id="set-1", json=False))
        rendered = output.getvalue()
        self.assertIn("id: set-1", rendered)
        self.assertIn("ciphertext_sha256: " + "a" * 64, rendered)
        self.assertNotIn("must-not-print", rendered)

    def test_cli_restore_human_output_shows_reviewable_plan(self):
        service = SimpleNamespace(restore_plan=lambda *args, **kwargs: {
            "action": "restore", "ok": True, "status": "planned", "data": {
                "set_id": "set-1", "profiles": ("control-plane",),
                "actions": ("verify", "swap"), "checkpoints": ("state",),
                "rollback": ("restore state",),
            }
        })
        output = StringIO()
        with patch("sandbox.commands.recovery.recovery_service", return_value=service), \
                redirect_stdout(output):
            cmd_recovery(None, self._args("restore", backup_id="set-1", json=False))
        rendered = output.getvalue()
        self.assertIn("set_id: set-1", rendered)
        self.assertIn("actions: verify, swap", rendered)
        self.assertIn("rollback: restore state", rendered)
