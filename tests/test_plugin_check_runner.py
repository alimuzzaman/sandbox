"""Tests for archive provenance and child-process result boundaries."""

from __future__ import annotations

import json
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.plugin_check.runner import (  # noqa: E402
    ArchiveRunnerError,
    RESULT_PREFIX,
    _isolated_project_config,
    launch_archive_runner,
    resolve_archive_provenance,
)


ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "plugin_check_archive.py"
fixture_spec = importlib.util.spec_from_file_location("plugin_check_runner_fixture", FIXTURE_PATH)
fixtures = importlib.util.module_from_spec(fixture_spec)
assert fixture_spec.loader is not None
sys.modules[fixture_spec.name] = fixtures
fixture_spec.loader.exec_module(fixtures)


def _config(**archive):
    return {
        "pluginCheck": {"archive": archive},
        "plugins_resolved": {
            "plugin-check": {
                "source": {
                    "kind": "zip",
                    "value": "https://downloads.wordpress.org/plugin/plugin-check.2.0.0.zip",
                }
            }
        },
    }


class TestArchiveProvenance(unittest.TestCase):
    def test_explicit_provenance_is_normalized_and_returned(self):
        pin, provenance = resolve_archive_provenance(
            _config(**{
                "sha256": "A" * 64,
                "wordpressVersion": "6.8.2",
                "phpVersion": "8.3",
                "sandboxRevision": "B" * 40,
            }),
            sandbox_root=".",
        )
        self.assertEqual(pin.version, "2.0.0")
        self.assertEqual(pin.sha256, "a" * 64)
        self.assertEqual(provenance, {
            "plugin_check": "2.0.0@" + "a" * 64,
            "wordpress": "6.8.2",
            "php": "8.3",
            "sandbox": "b" * 40,
        })

    def test_missing_digest_or_runtime_pin_fails_before_runtime(self):
        cases = (
            ({"wordpressVersion": "6.8.2", "phpVersion": "8.3", "sandboxRevision": "b" * 40}, "digest"),
            ({"sha256": "a" * 64, "phpVersion": "8.3", "sandboxRevision": "b" * 40}, "WordPress"),
            ({"sha256": "a" * 64, "wordpressVersion": "6.8.2", "sandboxRevision": "b" * 40}, "PHP"),
        )
        for archive, label in cases:
            with self.subTest(label=label), self.assertRaises(ArchiveRunnerError) as raised:
                resolve_archive_provenance(_config(**archive), sandbox_root=".")
            self.assertEqual(raised.exception.code, "archive_provenance_missing")

    def test_invalid_revision_is_not_treated_as_a_version(self):
        with self.assertRaises(ArchiveRunnerError) as raised:
            resolve_archive_provenance(
                _config(**{
                    "sha256": "a" * 64,
                    "wordpressVersion": "6.8.2",
                    "phpVersion": "8.3",
                    "sandboxRevision": "latest",
                }),
                sandbox_root=".",
            )
        self.assertEqual(raised.exception.code, "archive_provenance_missing")


class TestArchiveRunnerLaunch(unittest.TestCase):
    def test_isolated_project_config_has_only_descriptor_plugins(self):
        descriptor = {
            "plugins": {"attacker": {"path": "/outside"}},
            "pluginCheck": {"excludeDirectories": ["tests"], "versionFile": "entry.php", "baselineFile": "baseline.json"},
            "archiveReview": {
                "provenance": {"wordpress": "6.8.2", "php": "8.3"},
                "pluginCheck": {"source": "https://example.test/plugin-check.2.0.0.zip"},
            },
        }
        config = _isolated_project_config(
            descriptor,
            review_root=Path("/tmp/review"),
            plugin_path=Path("/tmp/review/extracted/demo-plugin"),
            plugin_slug="demo-plugin",
        )
        self.assertEqual(set(config["plugins_resolved"]), {"demo-plugin", "plugin-check"})
        self.assertFalse(config["plugins_resolved"]["demo-plugin"]["active"])
        self.assertTrue(config["plugins_resolved"]["plugin-check"]["active"])
        self.assertEqual(config["wpVersion"], "6.8.2")
        self.assertEqual(config["phpVersion"], "8.3")

    def test_launch_parses_only_the_structured_result_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = SimpleNamespace(
                descriptor_path=root / "descriptor.json",
                environment={"SANDBOX_HOME": str(root / "state")},
            )
            target.descriptor_path.write_text("{}")
            payload = {
                "ok": True,
                "input_mode": "archive",
                "error": None,
            }
            completed = SimpleNamespace(
                returncode=0,
                stdout="log with fake JSON {not a result}\n"
                + RESULT_PREFIX + json.dumps(payload) + "\n",
                stderr="private path should not be surfaced",
            )
            with patch("sandbox.plugin_check.runner.subprocess.run", return_value=completed) as run:
                result = launch_archive_runner(
                    target,
                    root / "journal.json",
                    timeout=10,
                    root=root,
                )
            self.assertEqual(result, payload)
            command = run.call_args.args[0]
            self.assertEqual(command[0], __import__("sys").executable)
            self.assertIn("--descriptor", command)
            self.assertEqual(run.call_args.kwargs["env"]["SANDBOX_HOME"], str(root / "state"))

    def test_launch_without_a_marker_is_cleanup_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = SimpleNamespace(
                descriptor_path=root / "descriptor.json",
                environment={},
            )
            target.descriptor_path.write_text("{}")
            completed = SimpleNamespace(returncode=1, stdout="child crashed", stderr="")
            with patch("sandbox.plugin_check.runner.subprocess.run", return_value=completed):
                result = launch_archive_runner(
                    target,
                    root / "journal.json",
                    timeout=10,
                    root=root,
                )
            self.assertEqual(result["error"], "archive_cleanup_unknown")
            self.assertTrue(result["cleanup"]["recovery_required"])

    def test_child_checks_inactive_target_and_returns_complete_cleanup(self):
        """A fake runtime proves the child ordering without starting Docker."""
        from sandbox.plugin_check import ArchiveReviewJournal, PluginCheckPin, build_archive_review_target, preflight_archive
        from sandbox.plugin_check.runner import run_archive_child

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            caller = root / "caller"
            caller.mkdir()
            archive = root / "release.zip"
            archive.write_bytes(fixtures.build_fixture_corpus()["valid"].data)
            preflight = preflight_archive(archive)
            target = build_archive_review_target(
                caller,
                preflight,
                run_id="run-child",
                sandbox_home=root / "sandbox-state",
                plugin_check=PluginCheckPin(
                    "https://downloads.wordpress.org/plugin/plugin-check.2.0.0.zip",
                    "2.0.0",
                    "a" * 64,
                ),
                wordpress_version="6.8.2",
                php_version="8.3",
                sandbox_revision="b" * 40,
            )
            # Use the normal session path so the child sees the same extracted
            # tree shape as the CLI parent.
            from sandbox.plugin_check import open_archive
            with open_archive(archive) as session:
                session.extract_to(target.extraction_root, preflight)
            journal = ArchiveReviewJournal.create(
                target.sandbox_home.parent / "archive-journal.json",
                run_id="run-child",
                target=target.contract_dict(),
            )

            import sandbox.core as core
            import sandbox_core as legacy_core
            import sandbox.commands.plugin_check as command
            original_loader = legacy_core.load_project_config
            inactive = SimpleNamespace(returncode=1, stdout="", stderr="")
            finding_output = SimpleNamespace(returncode=0, stdout="[]", stderr="")
            with patch.object(core, "ensure_instance", return_value={"instance": target.review_instance}), \
                    patch.object(core, "wpcli", side_effect=[inactive]), \
                    patch.object(core, "compose_file", return_value=root / "missing-compose.yml"), \
                    patch.object(legacy_core, "registry_remove"), \
                    patch.object(command, "wpcli", return_value=finding_output):
                result = run_archive_child(target.descriptor_path, journal.path)

            self.assertTrue(result["ok"])
            self.assertIsNone(result["error"])
            self.assertEqual(result["findings"], [])
            self.assertEqual(result["cleanup"]["status"], "complete")
            self.assertFalse((target.extraction_root).exists())
            self.assertTrue((target.artifact_dir / "plugin-check-report.html").is_file())
            self.assertIs(legacy_core.load_project_config, original_loader)


if __name__ == "__main__":
    unittest.main()
