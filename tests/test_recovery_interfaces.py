import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from io import StringIO
from contextlib import redirect_stdout
from pathlib import Path

from sandbox.commands.recovery import cmd_recovery


class TestRecoveryInterfaces(unittest.TestCase):
    def _args(self, action, **extra):
        values = {"action": action, "remote": None, "profile": [], "backup_id": None,
                  "artifact": [], "keep_count": 1, "minimum_age_days": 0,
                  "confirm": False, "scheduled": False, "json": True,
                  "postgres_operation": None, "reopen_plan": None,
                  "restore_plan": None, "source_binding": None, "target_volume": None,
                  "request_id": None, "project_dir": None, "resume_capture": False,
                  "destination": None}
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

    def test_cli_create_routes_symbolic_profiles_to_controller_materializer(self):
        calls = []
        service = SimpleNamespace(create_materialized=lambda *args, **kwargs: calls.append((args, kwargs)) or {
            "action": "create", "ok": True, "status": "complete", "data": {}
        })
        args = self._args("create", profile=["fixture"], backup_id="set-1", confirm=True)
        with patch.dict(os.environ, {"RECOVERY_PASSPHRASE": "fixture-secret"}, clear=True), \
                patch("sandbox.commands.recovery.recovery_service", return_value=service):
            cmd_recovery(None, args)
        self.assertEqual(calls, [(("set-1", ("fixture",)),
                                  {"confirm": True, "remote": None})])

    def test_cli_create_rejects_malformed_artifact_declaration(self):
        args = self._args("create", profile=["fixture"], backup_id="set-1", artifact=["malformed"])
        with self.assertRaises(SystemExit), patch.dict(os.environ, {"RECOVERY_PASSPHRASE": "fixture-secret"}, clear=True):
            cmd_recovery(None, args)

    def test_cli_scheduled_create_is_reserved_until_owned_materialization_exists(self):
        args = self._args("create", profile=["fixture"], confirm=True, scheduled=True)
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
                "legacy_candidates": (), "legacy_candidate_status": "blocked_no_current_verified_set",
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
        self.assertIn("legacy status: blocked_no_current_verified_set", output.getvalue())

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

    def test_cli_reopen_restore_passes_original_plan_confirmation_and_exact_reopen_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            restore_plan = {
                "remote": "scaleway-sandbox",
                "profile": "lenzora-dev",
                "native_request_id": "a" * 64,
                "target": "sandbox-recovery-restore-" + "b" * 24,
            }
            reopen_plan = {"schema_version": 1, "generation": 1, "target": restore_plan["target"]}
            restore_path = root / "restore-plan.json"
            reopen_path = root / "reopen-plan.json"
            restore_path.write_text(json.dumps(restore_plan))
            reopen_path.write_text(json.dumps(reopen_plan))
            restore_path.chmod(0o600)
            reopen_path.chmod(0o600)

            calls = []

            class FakePostgres:
                def __init__(self, *_args):
                    pass

                def restore(self, plan, **kwargs):
                    calls.append((plan, kwargs))
                    return {"code": "restore_reopened"}

            service = SimpleNamespace(capture=object(), catalog=None)
            args = self._args(
                "postgres", remote="scaleway-sandbox", profile=["lenzora-dev"],
                postgres_operation="reopen-restore", restore_plan=str(restore_path),
                reopen_plan=str(reopen_path), confirm=True,
            )
            with patch("sandbox.commands.recovery.recovery_service", return_value=service), \
                    patch("sandbox.recovery.postgres.PostgresRecovery", FakePostgres), \
                    patch("sandbox.transports.remote_postgres_recovery.RegisteredPostgresRecoveryTransport",
                          return_value=object()), redirect_stdout(StringIO()):
                cmd_recovery(None, args)
            self.assertEqual(calls, [(restore_plan, {
                "confirm": True, "inspect": False, "verify": False,
                "reopen": True, "reopen_plan": reopen_plan,
            })])

    def test_cli_reopen_restore_requires_plan_confirmation_and_matching_selectors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = {
                "remote": "scaleway-sandbox", "profile": "lenzora-dev",
                "native_request_id": "a" * 64,
            }
            restore_path = root / "restore.json"
            reopen_path = root / "reopen.json"
            restore_path.write_text(json.dumps(plan))
            reopen_path.write_text(json.dumps({"generation": 1}))
            restore_path.chmod(0o600)
            reopen_path.chmod(0o600)
            service = SimpleNamespace(capture=object(), catalog=None)

            for options in (
                {"restore_plan": None, "reopen_plan": str(reopen_path), "confirm": True},
                {"restore_plan": str(restore_path), "reopen_plan": str(reopen_path), "confirm": False},
                {"restore_plan": str(restore_path), "reopen_plan": str(reopen_path), "confirm": True,
                 "remote": "other-remote"},
                {"restore_plan": str(restore_path), "reopen_plan": str(reopen_path), "confirm": True,
                 "profile": ["lenzora-prod"]},
            ):
                args = self._args("postgres", remote=options.pop("remote", "scaleway-sandbox"),
                                  profile=options.pop("profile", ["lenzora-dev"]),
                                  postgres_operation="reopen-restore", **options)
                output = StringIO()
                with self.subTest(options=options), patch("sandbox.commands.recovery.recovery_service",
                        return_value=service), redirect_stdout(output), self.assertRaises(SystemExit):
                    cmd_recovery(None, args)
                payload = json.loads(output.getvalue())
                self.assertFalse(payload["ok"])

    def test_cli_reopen_plan_is_rejected_on_other_operations_before_transport(self):
        with tempfile.TemporaryDirectory() as directory:
            reopen_path = Path(directory) / "reopen.json"
            reopen_path.write_text(json.dumps({"generation": 1}))
            reopen_path.chmod(0o600)
            service = SimpleNamespace(capture=object(), catalog=None)
            for reopen_plan in (str(reopen_path), ""):
                with self.subTest(reopen_plan=reopen_plan):
                    args = self._args("postgres", postgres_operation="observe", remote="scaleway-sandbox",
                                      profile=["lenzora-dev"], request_id="request-a",
                                      reopen_plan=reopen_plan)
                    transport = Mock()
                    output = StringIO()
                    with patch("sandbox.commands.recovery.recovery_service", return_value=service), \
                            patch("sandbox.transports.remote_postgres_recovery.RegisteredPostgresRecoveryTransport",
                                  return_value=transport), redirect_stdout(output), self.assertRaises(SystemExit):
                        cmd_recovery(None, args)
                    payload = json.loads(output.getvalue())
                    self.assertFalse(payload["ok"])
                    self.assertEqual(payload["error"]["code"], "request_invalid")
                    transport.assert_not_called()

    def test_cli_schedule_human_output_shows_disabled_units(self):
        output = StringIO()
        payload = {"action": "schedule", "ok": True, "status": "planned", "data": {
            "units": {"enabled": "false", "service": "[Service]\nExecStart=sb recovery create",
                      "timer": "[Timer]\nOnCalendar=daily"},
        }}
        from sandbox.commands.recovery import _emit
        with redirect_stdout(output):
            _emit(payload, False)
        rendered = output.getvalue()
        self.assertIn("enabled: false", rendered)
        self.assertIn("ExecStart=sb recovery create", rendered)
        self.assertIn("OnCalendar=daily", rendered)
