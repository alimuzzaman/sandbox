"""Regression coverage for owner-only machine-local YAML persistence."""

from __future__ import annotations

import copy
import io
import json
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import yaml

import sandbox.core._config as config


class TestLocalConfigSafety(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.path = self.root / "sandbox.local.yml"
        self.config_path_patch = mock.patch.object(config, "CONFIG_LOCAL", self.path)
        self.config_path_patch.start()

    def tearDown(self):
        self.config_path_patch.stop()
        self.temporary.cleanup()

    def test_interrupted_serialization_preserves_the_last_good_file(self):
        original = b"existing: valid\n"
        self.path.write_bytes(original)
        self.path.chmod(0o600)

        def interrupt_after_partial_write(_value, stream, **_kwargs):
            stream.write("partial: [\n")
            raise RuntimeError("injected serialization interruption")

        with mock.patch.object(yaml, "safe_dump", side_effect=interrupt_after_partial_write):
            with self.assertRaises(config.ConfigWriteError):
                config._write_local_yaml({"replacement": "valid"})

        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(yaml.safe_load(self.path.read_text(encoding="utf-8")),
                         {"existing": "valid"})
        self.assertFalse(config._local_config_backup_path().exists())

    def test_successful_write_keeps_one_private_last_good_backup(self):
        original = b"existing: valid\n"
        self.path.write_bytes(original)
        self.path.chmod(0o600)

        config._write_local_yaml({"replacement": "valid"})

        backup = config._local_config_backup_path()
        self.assertEqual(backup.read_bytes(), original)
        self.assertEqual(yaml.safe_load(self.path.read_text(encoding="utf-8")),
                         {"replacement": "valid"})
        self.assertEqual(len(list(self.root.glob("sandbox.local.yml.bak*"))), 1)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(
                self.path.with_name(self.path.name + ".lock").stat().st_mode
            ), 0o600)

    def test_stale_independent_nested_updates_are_merged(self):
        self.path.write_text(
            "service:\n  php: '8.3'\n  server: nginx\n",
            encoding="utf-8",
        )
        self.path.chmod(0o600)
        first = config._local_yaml()
        second = config._local_yaml()

        first["service"]["php"] = "8.4"
        second["service"]["server"] = "caddy"
        config._write_local_yaml(first)
        config._write_local_yaml(second)

        self.assertEqual(
            yaml.safe_load(self.path.read_text(encoding="utf-8")),
            {"service": {"php": "8.4", "server": "caddy"}},
        )

    def test_stale_conflicting_updates_fail_without_overwriting_newer_value(self):
        self.path.write_text("service:\n  server: nginx\n", encoding="utf-8")
        self.path.chmod(0o600)
        first = config._local_yaml()
        second = config._local_yaml()
        first["service"]["server"] = "caddy"
        second["service"]["server"] = "apache"

        config._write_local_yaml(first)
        with self.assertRaisesRegex(config.ConfigWriteError, "changed concurrently"):
            config._write_local_yaml(second)

        self.assertEqual(
            yaml.safe_load(self.path.read_text(encoding="utf-8")),
            {"service": {"server": "caddy"}},
        )

    def test_deep_copied_snapshot_keeps_its_merge_baseline(self):
        self.path.write_text(
            "instances:\n  one:\n    enabled: false\n  two:\n    enabled: false\n",
            encoding="utf-8",
        )
        self.path.chmod(0o600)
        rollback_snapshot = copy.deepcopy(config._local_yaml())
        concurrent = config._local_yaml()

        concurrent["instances"]["one"]["enabled"] = True
        rollback_snapshot["instances"]["two"]["enabled"] = True
        config._write_local_yaml(concurrent)
        config._write_local_yaml(rollback_snapshot)

        self.assertEqual(
            yaml.safe_load(self.path.read_text(encoding="utf-8")),
            {"instances": {
                "one": {"enabled": True},
                "two": {"enabled": True},
            }},
        )

    def test_parse_error_reports_location_and_backup_without_source_text(self):
        self.path.write_text(
            "credential: do-not-display\ninvalid: scalar: mapping\n",
            encoding="utf-8",
        )
        self.path.chmod(0o600)

        with self.assertRaises(config.ConfigParseError) as raised:
            config._local_yaml()

        error = raised.exception
        self.assertEqual((error.line, error.column), (2, 16))
        self.assertEqual(error.backup_path, config._local_config_backup_path())
        self.assertNotIn("do-not-display", str(error))
        self.assertNotIn("scalar", str(error))

    def test_feedback_dispatch_does_not_load_machine_config(self):
        import sandbox.cli as cli

        handler = mock.Mock()
        commands = dict(cli.COMMANDS)
        commands["feedback"] = handler
        with mock.patch.object(cli, "COMMANDS", commands), \
                mock.patch.object(cli, "load_config", side_effect=config.ConfigParseError(
                    self.path, 9, 4, config._local_config_backup_path(),
                )) as load_config, \
                mock.patch.object(cli.sys, "argv", [
                    "sb", "feedback", "submit", "--summary", "safe test record",
                ]):
            cli.main()

        load_config.assert_not_called()
        handler.assert_called_once()
        self.assertEqual(handler.call_args.args[0], {})
        self.assertEqual(handler.call_args.args[1].action, "submit")

    def test_config_write_conflict_returns_typed_json_error(self):
        import sandbox.cli as cli

        handler = mock.Mock(side_effect=config.ConfigWriteError(
            "Local Sandbox config changed concurrently; reload it and retry safely"
        ))
        commands = dict(cli.COMMANDS)
        commands["feedback"] = handler
        output = io.StringIO()
        with mock.patch.object(cli, "COMMANDS", commands), \
                mock.patch.object(cli.sys, "argv", [
                    "sb", "feedback", "submit", "--json",
                ]), redirect_stdout(output):
            with self.assertRaises(SystemExit) as raised:
                cli.main()

        self.assertEqual(raised.exception.code, 2)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["error"]["code"], "config_write_error")
        self.assertFalse(payload["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
