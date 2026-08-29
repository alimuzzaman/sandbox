"""No-leak scanner over synthetic fixtures only.

Every string below is invented for this test. The scanner is never pointed at a
real secret source, a home directory, or the process environment.
"""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from credential_vault_proof import scanner  # noqa: E402


# Synthetic, structurally-valid-looking values that are not real credentials.
SYNTHETIC = {
    "bearer_token": "Authorization: Bearer aaaaaaaaaaaaaaaaaaaa",
    "api_key_assignment": "x-api-key: abcdefghijklmnop",
    "cookie_header": "Set-Cookie: session=abcdefghij",
    "pem_private_key": "-----BEGIN RSA PRIVATE KEY-----\nAAAA\n",
    "aws_access_key": "AKIAIOSFODNN7EXAMPLE",
    "aws_secret_key": "aws_secret_access_key = wJalrXUtnFEMIK7MDENGbPxRfiCY",
    "github_token": "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "slack_token": "xoxb-1111111111-abcdefghijkl",
    "stripe_key": "sk_live_aaaaaaaaaaaaaaaaaaaa",
    "google_api_key": "AIzaSyAaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "json_web_token": "eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.aaaaaaaaaaaa",
    "database_url": "postgres://user:hunter2@db.internal:5432/app",
    "url_credentials": "https://user:hunter2@example.test/path",
    "environment_dump": "TEMPLATELY_API_KEY=abcdefghijklmnop",
    "exception_trace": 'Traceback (most recent call last):\n  File "x", line 1',
    "internal_identifier": "operation_id was op-0123456789abcdef",
    "guest_request_header": "user-agent: curl/8.5.0",
}


class TestNoLeakScanner(unittest.TestCase):
    def test_each_detector_fires_on_its_synthetic_fixture(self):
        for code, text in SYNTHETIC.items():
            with self.subTest(code=code):
                findings = scanner.scan_text(text, location=code)
                self.assertIn(code, {item["code"] for item in findings})

    def test_a_finding_never_echoes_what_it_matched(self):
        for code, text in SYNTHETIC.items():
            with self.subTest(code=code):
                findings = scanner.scan_text(text, location=code)
                rendered = repr(findings)
                for fragment in ("hunter2", "AKIAIOSFODNN7EXAMPLE", "ghp_",
                                 "sk_live_", "xoxb-", "AIzaSy"):
                    self.assertNotIn(fragment, rendered)
                for item in findings:
                    self.assertEqual(set(item), {"code", "location", "offset"})

    def test_ordinary_harness_text_is_clean(self):
        for text in (
            "os_release_supported passed",
            "unit sandbox-credential-broker@sb-0123456789ab.service is absent",
            "machine_id sb-0123456789ab epoch epoch-fixture-0001",
            "sha256 " + "a" * 64,
        ):
            with self.subTest(text=text[:24]):
                self.assertTrue(scanner.is_clean(scanner.scan_text(text)))

    def test_forbidden_keys_are_refused_wherever_they_appear(self):
        for key in ("authorization", "operation_id", "lease_id", "request_digest",
                    "headers", "body", "source_reference", "token", "environment"):
            with self.subTest(key=key):
                findings = scanner.scan_document({"outer": {key: "value"}})
                self.assertIn("forbidden_key", {item["code"] for item in findings})

    def test_document_walking_bounds_depth_and_refuses_binary(self):
        deep = current = {}
        for _index in range(scanner.MAX_DEPTH + 3):
            nested = {}
            current["next"] = nested
            current = nested
        self.assertIn("document_too_deep",
                      {item["code"] for item in scanner.scan_document(deep)})
        self.assertIn("binary_value",
                      {item["code"] for item in scanner.scan_document({"a": b"\x00"})})
        self.assertTrue(scanner.is_clean(scanner.scan_document(
            {"a": 1, "b": True, "c": None, "d": [1, "ok"]},
        )))

    def test_directory_scanning_refuses_symlinks_and_oversize_members(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "clean.json").write_text('{"check":"passed"}')
            self.assertTrue(scanner.is_clean(scanner.scan_directory(root)))

            (root / "dirty.txt").write_text(SYNTHETIC["bearer_token"])
            codes = {item["code"] for item in scanner.scan_directory(root)}
            self.assertIn("bearer_token", codes)
            (root / "dirty.txt").unlink()

            (root / "link.json").symlink_to(root / "clean.json")
            codes = {item["code"] for item in scanner.scan_directory(root)}
            self.assertIn("evidence_symlink", codes)
            (root / "link.json").unlink()

            (root / "huge.bin").write_bytes(b"a" * (scanner.MAX_SCAN_BYTES + 1))
            codes = {item["code"] for item in scanner.scan_directory(root)}
            self.assertIn("oversize_scan_target", codes)

    def test_a_missing_or_symlinked_root_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "absent"
            self.assertIn("evidence_root_invalid",
                          {item["code"] for item in scanner.scan_directory(missing)})

    def test_findings_stay_bounded(self):
        text = "\n".join(SYNTHETIC.values() for _ in range(1)) \
            if False else "\n".join(SYNTHETIC.values())
        findings = scanner.scan_text(text)
        self.assertLessEqual(len(findings), scanner.MAX_FINDINGS)
        self.assertGreater(len(findings), 5)


if __name__ == "__main__":
    unittest.main()
