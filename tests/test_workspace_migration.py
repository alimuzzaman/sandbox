"""Legacy scanner safety and correlation tests."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from sandbox.workspaces.migration import correlate, scan_legacy
from sandbox.workspaces.models import JobEvidence


class WorkspaceMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "workspaces"
        self.root.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def _write(self, namespace, label, payload):
        path = self.root / namespace / label
        path.mkdir(parents=True, exist_ok=True)
        (path / "workspace.json").write_bytes(payload)
        return path

    def test_malformed_oversize_missing_and_symlink_are_visible(self):
        self._write("n", "bad", b"not-json")
        self._write("n", "large", b"x" * 32)
        (self.root / "n" / "missing").mkdir(parents=True)
        outside = Path(self.temp.name) / "outside.json"
        outside.write_text("{}")
        self._write("n", "link", b"{}")
        (self.root / "n" / "link" / "workspace.json").unlink()
        (self.root / "n" / "link" / "workspace.json").symlink_to(outside)
        result = scan_legacy(self.root, max_metadata_bytes=16)
        statuses = {item.label: item.status for item in result.records}
        self.assertEqual(statuses["bad"], "invalid")
        self.assertEqual(statuses["large"], "invalid")
        self.assertEqual(statuses["missing"], "incomplete")
        self.assertEqual(statuses["link"], "invalid")
        self.assertTrue(result.findings)

    def test_symlink_namespace_is_rejected_without_following(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (outside / "x").mkdir()
        (outside / "x" / "workspace.json").write_text("{}")
        (self.root / "escaped").symlink_to(outside, target_is_directory=True)
        result = scan_legacy(self.root)
        self.assertFalse(result.records)
        self.assertEqual(result.findings[0]["code"], "symlink_namespace")

    def test_unsafe_namespace_and_label_are_findings(self):
        self._write("safe", "good", b"{}")
        unsafe_namespace = self.root / "unsafe!"
        unsafe_namespace.mkdir()
        (unsafe_namespace / "good" / "workspace.json").parent.mkdir(parents=True)
        (unsafe_namespace / "good" / "workspace.json").write_text("{}")
        unsafe_label = self.root / "safe" / "bad!"
        unsafe_label.mkdir()
        (unsafe_label / "workspace.json").write_text("{}")
        result = scan_legacy(self.root)
        finding_codes = {item["code"] for item in result.findings}
        self.assertIn("invalid_namespace", finding_codes)
        self.assertIn("invalid_label", finding_codes)

    def test_symlink_root_is_rejected_without_following(self):
        link = Path(self.temp.name) / "workspace-link"
        link.symlink_to(self.root, target_is_directory=True)
        result = scan_legacy(link)
        self.assertEqual(result.records, ())
        self.assertEqual(result.findings[0]["code"], "symlink_root")

    def test_unique_none_and_conflicting_typed_evidence(self):
        self._write("n", "x", b"{}")
        record = scan_legacy(self.root).records
        unique = correlate(record, [JobEvidence("project:one", "n", "x")])[0]
        self.assertEqual(unique.status, "adoptable")
        unresolved = correlate(record, [])[0]
        self.assertEqual(unresolved.status, "unresolved")
        conflict = correlate(record, [
            JobEvidence("project:one", "n", "x"), JobEvidence("project:two", "n", "x")
        ])[0]
        self.assertEqual(conflict.status, "conflict")

    def test_typed_colon_namespace_matches_sanitized_legacy_directory(self):
        self._write("remote-vps-abc123", "ci", b"{}")
        record = scan_legacy(self.root).records
        adopted = correlate(
            record,
            [JobEvidence("project:one", "remote:vps:abc123", "ci")],
        )[0]
        self.assertEqual(adopted.status, "adoptable")

    def test_inconsistent_declared_identity_is_invalid_and_preserved(self):
        path = self._write(
            "local-abc", "expected",
            b'{"label":"different","namespace":"local:abc"}\n',
        ) / "workspace.json"
        before = path.read_bytes()
        result = scan_legacy(self.root)
        self.assertEqual(result.records[0].status, "invalid")
        self.assertIn("inconsistent_metadata", result.records[0].reason)
        self.assertEqual(path.read_bytes(), before)

    def test_bytes_and_path_remain_unchanged_after_scan(self):
        path = self._write("n", "x", b"{\n  \"x\": 1\n}\n") / "workspace.json"
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        scan_legacy(self.root)
        self.assertEqual(path.read_bytes(), before[0])
        self.assertEqual(path.stat().st_mtime_ns, before[1])


if __name__ == "__main__":
    unittest.main()
