"""Tests for archive findings, baseline ordering, and retained artifacts."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sandbox.commands import plugin_check as source_plugin_check  # noqa: E402
from sandbox.plugin_check.result import (  # noqa: E402
    PLANE_ORDER,
    ArchiveResultError,
    archive_error_counts,
    cleanup_receipt_complete,
    load_archive_baseline,
    normalize_archive_findings,
    persist_archive_artifact,
    prune_archive_artifacts,
    update_caller_baseline_atomic,
)
from sandbox.core._plugin_check_report import render_report  # noqa: E402


def _complete_receipt():
    return {
        "status": "complete",
        "receipt": "receipt-001",
        "recovery_required": False,
        "planes": {
            **{name: "absent" for name in PLANE_ORDER if name != "report"},
            "report": "complete",
        },
    }


class TestArchiveFindingIdentity(unittest.TestCase):
    def test_source_and_archive_finding_keys_are_identical(self):
        """The two input paths must gate on one stable file/rule identity."""

        plugin_root = Path("/tmp/review/extracted/demo-plugin")
        source_output = (
            f"FILE: {plugin_root}/includes/findings.php\n"
            '[{"type":"ERROR","code":"rule","line":2,"column":1,"message":"m"}]\n'
            f"FILE: {plugin_root}/includes/findings.php\n"
            '[{"type":"WARNING","code":"warning_rule","line":3,"column":1,"message":"w"}]\n'
        )
        source_findings = source_plugin_check._parse_findings(source_output, root=plugin_root)
        archive_findings = normalize_archive_findings(
            [
                {"file": str(plugin_root / "includes/findings.php"), "type": "ERROR", "code": "rule"},
                {"file": str(plugin_root / "includes/findings.php"), "type": "WARNING", "code": "warning_rule"},
            ],
            plugin_root,
        )

        self.assertEqual(
            source_plugin_check._count_by_key(source_findings),
            archive_error_counts(archive_findings),
        )
        self.assertEqual(source_findings[0]["file"], archive_findings[0]["file"])

    def test_absolute_and_plugin_prefixed_paths_become_relative(self):
        root = Path("/tmp/review/extracted/demo-plugin")
        findings = normalize_archive_findings(
            [
                {"file": "/tmp/review/extracted/demo-plugin/includes/foo.php", "type": "ERROR", "code": "rule"},
                {"file": "demo-plugin/includes/bar.php", "type": "WARNING", "code": "warn"},
            ],
            root,
        )
        self.assertEqual([finding["file"] for finding in findings], ["includes/foo.php", "includes/bar.php"])
        self.assertEqual(archive_error_counts(findings), {"includes/foo.php::rule": 1})

    def test_finding_path_escape_and_windows_names_are_rejected(self):
        for file_name in ("../escape.php", "includes/../escape.php", "C:\\escape.php", "/tmp/other/file.php"):
            with self.subTest(file_name=file_name), self.assertRaises(ArchiveResultError) as raised:
                normalize_archive_findings([{"file": file_name}], "/tmp/review/extracted/demo-plugin")
            self.assertEqual(raised.exception.code, "archive_finding_path")


class TestArchiveBaselineUpdate(unittest.TestCase):
    def test_nested_baseline_keys_are_validated_and_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            caller = Path(directory) / "caller"
            caller.mkdir()
            baseline = caller / "plugin-check-baseline.json"
            baseline.write_text('{"includes/foo.php::rule": 2}\n')
            self.assertEqual(
                load_archive_baseline(baseline, caller_project_root=caller),
                {"includes/foo.php::rule": 2},
            )

    def test_malformed_baseline_is_typed_instead_of_becoming_an_empty_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            caller = Path(directory) / "caller"
            caller.mkdir()
            baseline = caller / "plugin-check-baseline.json"
            baseline.write_text('["not-an-object"]\n')
            with self.assertRaises(ArchiveResultError) as raised:
                load_archive_baseline(baseline, caller_project_root=caller)
            self.assertEqual(raised.exception.code, "archive_baseline_invalid")

    def test_unknown_cleanup_leaves_caller_baseline_byte_for_byte_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            caller = Path(directory) / "caller"
            caller.mkdir()
            baseline = caller / "plugin-check-baseline.json"
            original = b'{"a.php::rule": 4}\n'
            baseline.write_bytes(original)
            with self.assertRaises(ArchiveResultError) as raised:
                update_caller_baseline_atomic(
                    baseline,
                    {"a.php::rule": 1},
                    {"status": "unknown", "recovery_required": True, "planes": {}},
                    caller_project_root=caller,
                )
            self.assertEqual(raised.exception.code, "archive_cleanup_unknown")
            self.assertEqual(baseline.read_bytes(), original)

    def test_complete_cleanup_replaces_only_caller_baseline_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            caller = Path(directory) / "caller"
            caller.mkdir()
            baseline = caller / "plugin-check-baseline.json"
            baseline.write_text('{"a.php::rule": 4}\n')
            result = update_caller_baseline_atomic(
                baseline,
                {"a.php::rule": 1, "b.php::other": 0},
                _complete_receipt(),
                caller_project_root=caller,
            )
            self.assertEqual(result, baseline.resolve())
            self.assertEqual(json.loads(baseline.read_text()), {"a.php::rule": 1, "b.php::other": 0})
            self.assertEqual(stat.S_IMODE(baseline.stat().st_mode), 0o644)

    def test_baseline_outside_caller_and_bad_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            caller = Path(directory) / "caller"
            caller.mkdir()
            with self.assertRaises(ArchiveResultError) as outside:
                update_caller_baseline_atomic(
                    Path(directory) / "outside.json",
                    {},
                    _complete_receipt(),
                    caller_project_root=caller,
                )
            self.assertEqual(outside.exception.code, "archive_baseline_invalid")
            with self.assertRaises(ArchiveResultError) as key_error:
                update_caller_baseline_atomic(
                    caller / "baseline.json",
                    {"../escape::rule": 1},
                    _complete_receipt(),
                    caller_project_root=caller,
                )
            self.assertEqual(key_error.exception.code, "archive_baseline_invalid")


class TestArchiveArtifacts(unittest.TestCase):
    def _artifact_dirs(self, root: Path, names: list[str], now: datetime):
        paths = []
        for name in names:
            path = root / name
            path.mkdir(mode=0o700)
            old = now.timestamp() - (1 if name != "run-new" else 0)
            os.utime(path, (old, old))
            paths.append(path)
        return paths

    def test_artifact_result_drops_paths_and_persists_escaped_report(self):
        with tempfile.TemporaryDirectory() as directory:
            reports = Path(directory) / "reports"
            reports.mkdir(mode=0o700)
            run = reports / "run-001"
            run.mkdir(mode=0o700)
            result = {
                "ok": True,
                "input_mode": "archive",
                "archive_sha256": "a" * 64,
                "archive_slug": "demo-plugin",
                "main_file": "demo-plugin/entrypoint.php",
                "archive_path": "/tmp/private-release.zip",
                "extraction_root": "/tmp/private-extracted",
                "findings": [{"file": "includes/<x>.php", "type": "ERROR", "code": "rule", "message": "</script>"}],
                "cleanup": {**_complete_receipt(), "journal": "/tmp/private-journal.json"},
            }
            paths = persist_archive_artifact(
                run,
                result,
                "<script id=\"findings-data\" type=\"application/json\">\\u003c/script\\u003e</script>",
                reports_root=reports,
            )
            saved = json.loads(paths["result"].read_text())
            self.assertNotIn("archive_path", saved)
            self.assertNotIn("extraction_root", saved)
            self.assertNotIn("/tmp", paths["result"].read_text())
            self.assertIn("\\u003c", paths["report"].read_text())
            self.assertEqual(stat.S_IMODE(paths["result"].stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(run.stat().st_mode), 0o700)

    def test_retention_removes_old_and_keeps_at_most_twenty_recent_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            reports = Path(directory) / "reports"
            reports.mkdir(mode=0o700)
            now = datetime(2026, 8, 26, tzinfo=timezone.utc)
            old = reports / "run-old"
            old.mkdir(mode=0o700)
            old_time = (now - timedelta(days=8)).timestamp()
            os.utime(old, (old_time, old_time))
            for index in range(21):
                path = reports / f"run-{index:02d}"
                path.mkdir(mode=0o700)
                timestamp = (now - timedelta(minutes=index)).timestamp()
                os.utime(path, (timestamp, timestamp))
            removed = prune_archive_artifacts(reports, max_reports=20, now=now)
            self.assertIn(old.resolve(), removed)
            self.assertEqual(len([path for path in reports.iterdir() if path.is_dir()]), 20)

    def test_cleanup_receipt_requires_all_planes_and_report_retention(self):
        receipt = _complete_receipt()
        self.assertTrue(cleanup_receipt_complete(receipt))
        receipt["planes"]["network"] = "unknown"
        self.assertFalse(cleanup_receipt_complete(receipt))

    def test_report_escapes_archive_controlled_html_and_script_data(self):
        html = render_report(
            [{
                "file": "includes/<img src=x onerror=alert(1)>.php",
                "type": "ERROR",
                "code": "rule&<",
                "line": '1\" onmouseover=alert(1)',
                "column": 1,
                "message": "</script><script>alert('owned')</script>",
            }],
            {
                "plugin_slug": "demo-plugin",
                "plugin_version": "1.0",
                "checker_version": "2.0",
                "wp_version": "6.8",
                "php_version": "8.3",
                "exclude_directories": [],
                "baseline_total": 1,
                "new_count": 0,
            },
        )
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html)
        self.assertIn("rule&amp;&lt;", html)
        self.assertIn("&#x27;owned&#x27;", html)
        data = html.split('<script id="findings-data" type="application/json">', 1)[1].split("</script>", 1)[0]
        self.assertNotIn("</script>", data.lower())
        self.assertIn(r"\u003c/script\u003e", data)


if __name__ == "__main__":
    unittest.main()
