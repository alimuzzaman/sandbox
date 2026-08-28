"""Tests for the archive review target/config isolation boundary."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

from sandbox.plugin_check import (  # noqa: E402
    ArchiveTargetError,
    PluginCheckPin,
    build_archive_review_target,
    preflight_archive,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "plugin_check_archive.py"
spec = importlib.util.spec_from_file_location("plugin_check_archive_fixture_for_target", FIXTURE_PATH)
fixtures = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = fixtures
spec.loader.exec_module(fixtures)


class TestArchiveReviewTarget(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.valid = fixtures.build_fixture_corpus()["valid"]
        cls.pin = PluginCheckPin(
            source="https://downloads.wordpress.org/plugin/plugin-check.2.0.0.zip",
            version="2.0.0",
            sha256="A" * 64,
        )

    def _make_target(self, directory: str, *, baseline: Path | None = None):
        root = Path(directory)
        caller = root / "caller"
        caller.mkdir()
        archive = root / "release.zip"
        archive.write_bytes(self.valid.data)
        preflight = preflight_archive(archive)
        return caller, build_archive_review_target(
            caller,
            preflight,
            run_id="run-001",
            sandbox_home=root / "sandbox-state",
            plugin_check=self.pin,
            baseline_path=baseline,
        )

    def test_builder_creates_owner_only_run_state_and_descriptor(self):
        with tempfile.TemporaryDirectory() as directory:
            caller, target = self._make_target(directory)
            for path in (
                target.sandbox_home,
                target.review_project_root,
                target.extraction_root,
                target.artifact_dir,
            ):
                self.assertTrue(path.is_dir())
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
                self.assertEqual(path.stat().st_uid, os.getuid())
            self.assertEqual(stat.S_IMODE(target.descriptor_path.stat().st_mode), 0o600)
            self.assertEqual(target.descriptor_path.stat().st_uid, os.getuid())
            self.assertNotEqual(target.review_project_root, caller)
            target.review_project_root.relative_to(target.sandbox_home.parent)

    def test_descriptor_has_only_inactive_read_only_target_and_active_pinned_checker(self):
        with tempfile.TemporaryDirectory() as directory:
            _caller, target = self._make_target(directory)
            descriptor = json.loads(target.descriptor_path.read_text())
            self.assertEqual(set(descriptor["plugins"]), {"demo-plugin", "plugin-check"})
            self.assertFalse(descriptor["plugins"]["demo-plugin"]["active"])
            self.assertEqual(descriptor["plugins"]["demo-plugin"]["path"], str(target.plugin_path))
            self.assertTrue(descriptor["plugins"]["plugin-check"]["active"])
            self.assertEqual(descriptor["archiveReview"]["runtime"], {
                "kind": "compose", "scope": "local", "remote": False,
            })
            self.assertEqual(descriptor["archiveReview"]["target"], {
                "active": False, "readOnly": True,
            })
            self.assertEqual(descriptor["archiveReview"]["pluginCheck"]["sha256"], "a" * 64)
            self.assertEqual(descriptor["archiveReview"]["pluginCheck"]["version"], "2.0.0")
            for key in ("aliases", "domains", "hooks", "credentials", "proPlugins"):
                self.assertNotIn(key, descriptor)
            self.assertEqual(descriptor["themes"], [])
            self.assertEqual(descriptor["mappings"], {})
            self.assertEqual(descriptor["mappings_inactive"], {})

    def test_environment_allowlist_is_run_local_only(self):
        with tempfile.TemporaryDirectory() as directory:
            _caller, target = self._make_target(directory)
            self.assertEqual(target.environment, {
                "SANDBOX_HOME": str(target.sandbox_home),
                "SANDBOX_PROJECT_ROOTS": str(target.review_project_root),
            })
            self.assertEqual(target.project_roots, (target.review_project_root,))
            self.assertEqual(set(target.environment), {"SANDBOX_HOME", "SANDBOX_PROJECT_ROOTS"})

    def test_caller_baseline_is_preserved_as_a_relative_project_path(self):
        with tempfile.TemporaryDirectory() as directory:
            caller = Path(directory) / "caller"
            caller.mkdir()
            archive = Path(directory) / "release.zip"
            archive.write_bytes(self.valid.data)
            baseline = caller / "ci" / "baseline.json"
            baseline.parent.mkdir()
            baseline.write_text('{"demo-plugin/entrypoint.php::rule": 1}\n')
            preflight = preflight_archive(archive)
            target = build_archive_review_target(
                caller,
                preflight,
                run_id="run-002",
                sandbox_home=Path(directory) / "sandbox-state",
                plugin_check=self.pin,
                baseline_path="ci/baseline.json",
            )
            self.assertEqual(target.baseline_path, baseline.resolve())
            self.assertEqual(json.loads(baseline.read_text())["demo-plugin/entrypoint.php::rule"], 1)

    def test_rejects_reused_run_id_and_state_that_escapes_base(self):
        with tempfile.TemporaryDirectory() as directory:
            caller, target = self._make_target(directory)
            preflight = preflight_archive(target.archive_path)
            with self.assertRaises(ArchiveTargetError) as reused:
                build_archive_review_target(
                    caller,
                    preflight,
                    run_id="run-001",
                    sandbox_home=Path(directory) / "sandbox-state",
                    plugin_check=self.pin,
                )
            self.assertEqual(reused.exception.code, "archive_isolation_failed")
            outside = Path(directory) / "outside"
            outside.mkdir()
            state = Path(directory) / "state"
            state.mkdir()
            (state / "runtime").symlink_to(outside, target_is_directory=True)
            archive = Path(directory) / "other.zip"
            archive.write_bytes(self.valid.data)
            preflight = preflight_archive(archive)
            with self.assertRaises(ArchiveTargetError) as escaped:
                build_archive_review_target(
                    caller,
                    preflight,
                    run_id="run-003",
                    sandbox_home=state,
                    plugin_check=self.pin,
                )
            self.assertEqual(escaped.exception.code, "archive_isolation_failed")

    def test_rejects_unpinned_checker_or_baseline_outside_caller(self):
        with tempfile.TemporaryDirectory() as directory:
            caller = Path(directory) / "caller"
            caller.mkdir()
            archive = Path(directory) / "release.zip"
            archive.write_bytes(self.valid.data)
            preflight = preflight_archive(archive)
            with self.assertRaises(ArchiveTargetError) as pin_error:
                PluginCheckPin(
                    source="http://example.test/plugin-check.zip",
                    version="latest",
                    sha256="bad",
                )
            self.assertEqual(pin_error.exception.code, "archive_provenance_missing")
            with self.assertRaises(ArchiveTargetError) as baseline_error:
                build_archive_review_target(
                    caller,
                    preflight,
                    run_id="run-004",
                    sandbox_home=Path(directory) / "state",
                    plugin_check=self.pin,
                    baseline_path=Path(directory) / "outside-baseline.json",
                )
            self.assertEqual(baseline_error.exception.code, "archive_isolation_failed")


if __name__ == "__main__":
    unittest.main()
