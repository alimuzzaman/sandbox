"""Focused tests for the exact-release archive preflight boundary.

These tests are host-only.  They never boot a Sandbox instance and never
execute the PHP contained in the side-effect fixture.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import tempfile
import unittest
import warnings
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "plugin_check_archive.py"
spec = importlib.util.spec_from_file_location("plugin_check_archive_fixture", FIXTURE_PATH)
fixtures = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = fixtures
spec.loader.exec_module(fixtures)

from sandbox.plugin_check.archive import (  # noqa: E402
    ArchiveLimits,
    ArchivePreflightError,
    DEFAULT_LIMITS,
    open_archive,
    preflight_archive,
)


class TestArchiveFixtureCorpus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            cls.corpus = fixtures.build_fixture_corpus()

    def test_generator_is_deterministic_and_valid_sha_is_stable(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            again = fixtures.build_fixture_corpus()
        self.assertEqual(self.corpus["valid"].data, again["valid"].data)
        self.assertEqual(
            hashlib.sha256(self.corpus["valid"].data).hexdigest(),
            "34de3e374abf0aad08753f3a582be384c845ed7052f9b70dd0d0b2af686c5cfd",
        )
        self.assertEqual(self.corpus["valid"].expected_error, None)
        self.assertEqual(self.corpus["valid_non_slug_main"].expected_error, None)

    def test_generator_can_materialise_named_corpus(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = fixtures.write_fixture_corpus(directory)
            self.assertEqual(set(paths), set(self.corpus))
            self.assertTrue(all(path.is_file() for path in paths.values()))
            self.assertEqual(paths["valid"].read_bytes(), self.corpus["valid"].data)


class TestArchivePreflight(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            cls.corpus = fixtures.build_fixture_corpus()

    def _write(self, directory: str, name: str) -> Path:
        path = Path(directory) / f"{name}.zip"
        path.write_bytes(self.corpus[name].data)
        return path

    def test_valid_archive_has_slug_non_slug_main_and_stable_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(directory, "valid_non_slug_main")
            first = preflight_archive(path)
            second = preflight_archive(path)
        self.assertEqual(first.archive_slug, "demo-plugin")
        self.assertEqual(first.main_file, "demo-plugin/bootstrap.php")
        self.assertEqual(first.archive_sha256, second.archive_sha256)
        self.assertEqual(first.member_manifest_sha256, second.member_manifest_sha256)
        self.assertEqual(first.members, second.members)
        self.assertEqual(first.member_count, 4)
        self.assertEqual(first.total_expanded_bytes, sum(item.expanded_size for item in first.members))

    def test_invalid_corpus_is_rejected_before_extraction(self):
        invalid = {
            name: fixture
            for name, fixture in self.corpus.items()
            if fixture.expected_error is not None
        }
        for name, fixture in invalid.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                archive_path = self._write(directory, name)
                extraction_root = Path(directory) / "extracted"
                with self.assertRaises(ArchivePreflightError) as raised:
                    preflight_archive(archive_path, extraction_root=extraction_root)
                self.assertEqual(raised.exception.code, fixture.expected_error)
                self.assertFalse(extraction_root.exists(), name)

    def test_same_open_descriptor_is_used_for_inspection_and_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            archive_path = self._write(directory, "valid")
            extraction_root = Path(directory) / "extracted"
            with open_archive(archive_path) as session:
                result = session.inspect()
                descriptor = session.descriptor_fileno
                session.extract_to(extraction_root, result)
                self.assertEqual(session.descriptor_fileno, descriptor)
            self.assertEqual(
                (extraction_root / "demo-plugin" / "entrypoint.php").read_bytes(),
                b"<?php\n/**\n * Plugin Name: Demo Plugin\n * Version: 1.0.0\n */\n",
            )
            findings = (extraction_root / "demo-plugin" / "includes" / "findings.php").read_text()
            self.assertIn("SANDBOX_FIXTURE_ERROR: wp_deprecated_function", findings)
            self.assertIn("SANDBOX_FIXTURE_WARNING: nonce_check", findings)
            self.assertFalse((extraction_root / "demo-plugin" / "sandbox-archive-fixture-sentinel").exists())

    def test_input_symlink_is_rejected_without_following_it(self):
        with tempfile.TemporaryDirectory() as directory:
            real = self._write(directory, "valid")
            link = Path(directory) / "link.zip"
            link.symlink_to(real)
            with self.assertRaises(ArchivePreflightError) as raised:
                preflight_archive(link)
            self.assertEqual(raised.exception.code, "archive_symlink")

    def test_exact_inclusive_limits_are_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(directory, "valid")
            baseline = preflight_archive(path)
            limits = ArchiveLimits(
                archive_bytes=path.stat().st_size,
                max_members=baseline.member_count,
                max_path_bytes=max(len(member.name.encode("utf-8")) for member in baseline.members),
                max_path_depth=max(len(member.name.split("/")) for member in baseline.members),
                max_file_bytes=max(member.expanded_size for member in baseline.members),
                max_total_bytes=baseline.total_expanded_bytes,
                max_compression_ratio=1,
            )
            result = preflight_archive(path, limits=limits)
            self.assertEqual(result.member_manifest_sha256, baseline.member_manifest_sha256)

    def test_each_declared_limit_fails_only_when_crossed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(directory, "valid")
            baseline = preflight_archive(path)
            max_path = max(len(member.name.encode("utf-8")) for member in baseline.members)
            max_depth = max(len(member.name.split("/")) for member in baseline.members)
            max_file = max(member.expanded_size for member in baseline.members)
            cases = (
                ("archive_size_limit", replace(DEFAULT_LIMITS, archive_bytes=path.stat().st_size - 1)),
                ("archive_member_limit", replace(DEFAULT_LIMITS, max_members=baseline.member_count - 1)),
                ("archive_path_limit", replace(DEFAULT_LIMITS, max_path_bytes=max_path - 1)),
                ("archive_path_limit", replace(DEFAULT_LIMITS, max_path_depth=max_depth - 1)),
                ("archive_file_limit", replace(DEFAULT_LIMITS, max_file_bytes=max_file - 1)),
                ("archive_total_limit", replace(DEFAULT_LIMITS, max_total_bytes=baseline.total_expanded_bytes - 1)),
                ("archive_ratio_limit", replace(DEFAULT_LIMITS, max_compression_ratio=0)),
            )
            for expected, limits in cases:
                with self.subTest(expected=expected):
                    with self.assertRaises(ArchivePreflightError) as raised:
                        preflight_archive(path, limits=limits)
                    self.assertEqual(raised.exception.code, expected)

    def test_manifest_digest_is_independent_of_zip_member_order(self):
        entries = fixtures._valid_entries()
        reordered = fixtures._zip([entries[0], entries[3], entries[1], entries[2]])
        with tempfile.TemporaryDirectory() as directory:
            first_path = Path(directory) / "first.zip"
            second_path = Path(directory) / "second.zip"
            first_path.write_bytes(self.corpus["valid"].data)
            second_path.write_bytes(reordered)
            first = preflight_archive(first_path)
            second = preflight_archive(second_path)
        self.assertNotEqual(first.archive_sha256, second.archive_sha256)
        self.assertEqual(first.member_manifest_sha256, second.member_manifest_sha256)


if __name__ == "__main__":
    unittest.main()
