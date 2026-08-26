"""CLI integration tests for the exact-release Plugin Check archive path."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import redirect_stdout

import sandbox.commands.plugin_check as plugin_check
from sandbox.plugin_check import PluginCheckPin


ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "plugin_check_archive.py"
spec = importlib.util.spec_from_file_location("plugin_check_archive_cli_fixture", FIXTURE_PATH)
fixtures = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = fixtures
spec.loader.exec_module(fixtures)


_COMPLETE_PLANES = {
    "container": "absent",
    "network": "absent",
    "volume": "absent",
    "runtime": "absent",
    "registry": "absent",
    "extraction": "absent",
    "report": "complete",
}


class TestPluginCheckArchiveCli(unittest.TestCase):
    def _setup(self, directory: str, *, baseline: str | None = None):
        root = Path(directory)
        caller = root / "caller"
        caller.mkdir()
        archive = root / "release.zip"
        archive.write_bytes(fixtures.build_fixture_corpus()["valid"].data)
        if baseline is not None:
            (caller / "plugin-check-baseline.json").write_text(baseline)
        pconf = {
            "root": str(caller),
            "slug": "caller",
            "pluginCheck": {"baselineFile": "plugin-check-baseline.json"},
        }
        fake_core = SimpleNamespace(BASE=root / "sandbox-state")
        fake_core.load_project_config = lambda _project_dir: pconf
        fake_core.ConfigError = Exception
        args = SimpleNamespace(
            project_dir=str(caller), archive=str(archive), update=False, json=True,
        )
        pin = PluginCheckPin(
            "https://downloads.wordpress.org/plugin/plugin-check.2.0.0.zip",
            "2.0.0", "a" * 64,
        )
        result = {
            "ok": True,
            "error": None,
            "findings": [{
                "file": "includes/findings.php", "type": "ERROR", "code": "rule",
                "line": 2, "column": 1, "message": "deterministic",
            }],
            "checker_provenance": {
                "plugin_check": "2.0.0@" + "a" * 64,
                "wordpress": "6.8.2", "php": "8.3",
                "sandbox": "b" * 40,
            },
            "plugin_version": "1.0.0",
            "cleanup": {
                "status": "complete", "receipt": "receipt-1",
                "planes": dict(_COMPLETE_PLANES), "recovery_required": False,
            },
        }
        return caller, archive, pconf, fake_core, args, pin, result

    def _run(self, pconf, fake_core, args, pin, child_result):
        output = io.StringIO()
        with patch.object(plugin_check, "_core", return_value=fake_core), \
             patch.object(plugin_check, "_resolve_plugin_check_config", return_value={
                 "slug": "demo-plugin", "exclude_directories": [],
                 "version_file": "entrypoint.php", "baseline_file": "plugin-check-baseline.json",
             }), \
             patch.object(plugin_check, "resolve_archive_provenance", return_value=(pin, {
                 "plugin_check": "2.0.0@" + "a" * 64,
                 "wordpress": "6.8.2", "php": "8.3", "sandbox": "b" * 40,
             })), \
             patch.object(plugin_check, "launch_archive_runner", return_value=child_result), \
             patch.object(plugin_check, "_archive_run_id", return_value="archive-cli-test"), \
             redirect_stdout(output):
            plugin_check.cmd_plugin_check({}, args)
        return json.loads(output.getvalue())

    def test_archive_check_gates_against_caller_baseline_without_mutating_source(self):
        with tempfile.TemporaryDirectory() as directory:
            caller, _archive, pconf, fake_core, args, pin, child = self._setup(
                directory, baseline='{"includes/findings.php::rule": 1}\n',
            )
            result = self._run(pconf, fake_core, args, pin, child)
            self.assertTrue(result["ok"])
            self.assertEqual(result["input_mode"], "archive")
            self.assertEqual(result["archive_slug"], "demo-plugin")
            self.assertEqual(result["new_count"], 0)
            self.assertEqual(
                (caller / "plugin-check-baseline.json").read_text(),
                '{"includes/findings.php::rule": 1}\n',
            )
            self.assertTrue(Path(result["report_path"]).is_file())

    def test_archive_update_replaces_baseline_only_after_complete_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            caller, _archive, pconf, fake_core, args, pin, child = self._setup(
                directory, baseline='{"old.php::old": 4}\n',
            )
            args.update = True
            result = self._run(pconf, fake_core, args, pin, child)
            self.assertTrue(result["ok"])
            self.assertEqual(result["action"], "update")
            self.assertEqual(
                json.loads((caller / "plugin-check-baseline.json").read_text()),
                {"includes/findings.php::rule": 1},
            )

    def test_cleanup_unknown_never_rewrites_baseline_or_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline_text = '{"old.php::old": 4}\n'
            caller, _archive, pconf, fake_core, args, pin, child = self._setup(
                directory, baseline=baseline_text,
            )
            args.update = True
            child = {**child,
                     "ok": False,
                     "error": "archive_cleanup_unknown",
                     "cleanup": {
                         "status": "unknown", "receipt": "receipt-1",
                         "planes": {**_COMPLETE_PLANES, "network": "unknown"},
                         "recovery_required": True,
                     }}
            output = io.StringIO()
            with patch.object(plugin_check, "_core", return_value=fake_core), \
                 patch.object(plugin_check, "_resolve_plugin_check_config", return_value={
                     "slug": "demo-plugin", "exclude_directories": [],
                     "version_file": "entrypoint.php", "baseline_file": "plugin-check-baseline.json",
                 }), \
                 patch.object(plugin_check, "resolve_archive_provenance", return_value=(pin, {
                     "plugin_check": "2.0.0@" + "a" * 64,
                     "wordpress": "6.8.2", "php": "8.3", "sandbox": "b" * 40,
                 })), \
                 patch.object(plugin_check, "launch_archive_runner", return_value=child), \
                 patch.object(plugin_check, "_archive_run_id", return_value="archive-cli-test"), \
                 redirect_stdout(output):
                with self.assertRaises(SystemExit) as raised:
                    plugin_check.cmd_plugin_check({}, args)
            self.assertEqual(raised.exception.code, 1)
            result = json.loads(output.getvalue())
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"], "archive_cleanup_unknown")
            self.assertEqual((caller / "plugin-check-baseline.json").read_text(), baseline_text)

    def test_preflight_failure_is_typed_json_and_does_not_boot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            caller = root / "caller"
            caller.mkdir()
            args = SimpleNamespace(project_dir=str(caller), archive="missing.zip", update=False, json=True)
            fake_core = SimpleNamespace(BASE=root / "state")
            fake_core.load_project_config = lambda _project_dir: {
                "root": str(caller),
                "slug": "demo",
                "pluginCheck": {"baselineFile": "plugin-check-baseline.json"},
            }
            fake_core.ConfigError = Exception
            output = io.StringIO()
            with patch.object(plugin_check, "_core", return_value=fake_core), \
                 patch.object(plugin_check, "_resolve_plugin_check_config", return_value={
                     "slug": "demo", "exclude_directories": [],
                     "version_file": "demo.php", "baseline_file": "plugin-check-baseline.json",
                 }), \
                 patch.object(plugin_check, "resolve_archive_provenance", side_effect=lambda *_a, **_k: (
                     PluginCheckPin("https://downloads.wordpress.org/plugin/plugin-check.2.0.0.zip", "2.0.0", "a" * 64),
                     {"plugin_check": "2.0.0@" + "a" * 64, "wordpress": "6.8.2", "php": "8.3", "sandbox": "b" * 40},
                 )), \
                 patch.object(plugin_check, "launch_archive_runner") as launch, \
                 redirect_stdout(output):
                with self.assertRaises(SystemExit) as raised:
                    plugin_check.cmd_plugin_check({}, args)
            self.assertEqual(raised.exception.code, 1)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["error"], "archive_preflight_failed")
            launch.assert_not_called()

    def test_missing_provenance_fails_before_archive_open(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            caller = root / "caller"
            caller.mkdir()
            archive = root / "release.zip"
            archive.write_bytes(fixtures.build_fixture_corpus()["valid"].data)
            args = SimpleNamespace(project_dir=str(caller), archive=str(archive), update=False, json=True)
            fake_core = SimpleNamespace(BASE=root / "state")
            fake_core.load_project_config = lambda _project_dir: {
                "root": str(caller),
                "slug": "demo",
                "pluginCheck": {"baselineFile": "plugin-check-baseline.json"},
            }
            fake_core.ConfigError = Exception
            with patch.object(plugin_check, "_core", return_value=fake_core), \
                 patch.object(plugin_check, "_resolve_plugin_check_config", return_value={
                     "slug": "demo", "exclude_directories": [],
                     "version_file": "demo.php", "baseline_file": "plugin-check-baseline.json",
                 }), \
                 patch.object(plugin_check, "resolve_archive_provenance", side_effect=Exception("missing")), \
                 patch.object(plugin_check, "open_archive") as opened:
                with self.assertRaises(SystemExit):
                    plugin_check.cmd_plugin_check({}, args)
            opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()
