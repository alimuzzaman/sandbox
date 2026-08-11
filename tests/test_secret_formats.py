from __future__ import annotations

import json
from pathlib import Path
import traceback
import unittest

from sandbox.secrets.formats import SecretFormatError, parse_secret_document


FIXTURES = Path(__file__).parent / "fixtures/secret-formats"
CANARY_PREFIX = "SB_SYNTHETIC_"


class SecretFormatCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((FIXTURES / "manifest.json").read_text())

    def test_manifest_uses_https_provenance_and_only_synthetic_material(self):
        self.assertGreaterEqual(len(self.manifest["fixtures"]), 18)
        for item in self.manifest["fixtures"]:
            with self.subTest(item=item["id"]):
                self.assertTrue(item["docs"].startswith("https://"))
                content = (FIXTURES / item["file"]).read_bytes()
                self.assertNotIn(b"AKIA", content)
                self.assertNotIn(b"ghp_", content)
                self.assertNotIn(b"github_pat_", content)
                if item["format"] != "pem":
                    self.assertIn(b"synthetic", content.lower())

    def test_every_document_lists_only_expected_key_paths(self):
        for item in self.manifest["fixtures"]:
            with self.subTest(item=item["id"], format=item["format"]):
                document = parse_secret_document(
                    (FIXTURES / item["file"]).read_bytes(), item["format"],
                )
                self.assertEqual(sorted(document.entries), item["expectedKeys"])
                rendered = repr(document)
                self.assertNotIn(CANARY_PREFIX, rendered)
                self.assertNotIn("value=", rendered.lower())

    def test_structured_entries_never_allow_masking_or_exact_length(self):
        for item in self.manifest["fixtures"]:
            document = parse_secret_document(
                (FIXTURES / item["file"]).read_bytes(), item["format"],
            )
            if item["format"] == "opaque":
                continue
            for entry in document.entries.values():
                with self.subTest(item=item["id"], key=entry.key):
                    self.assertFalse(entry.allow_mask)
                    self.assertFalse(entry.allow_exact_length)

    def test_duplicate_and_active_syntax_fail_without_canary_leakage(self):
        cases = (
            ("json", b'{"token":"SB_SYNTHETIC_ONE","token":"SB_SYNTHETIC_TWO"}'),
            ("ini", b'[default]\ntoken=SB_SYNTHETIC_ONE\ntoken=SB_SYNTHETIC_TWO\n'),
            ("yaml", b'token: &x SB_SYNTHETIC_ONE\ncopy: *x\n'),
            ("xml", b'<!DOCTYPE x [<!ENTITY e "SB_SYNTHETIC_ONE">]><x>&e;</x>'),
        )
        for format_name, content in cases:
            with self.subTest(format=format_name), self.assertRaises(SecretFormatError) as raised:
                parse_secret_document(content, format_name)
            self.assertNotIn("SB_SYNTHETIC", str(raised.exception))
            self.assertIsNone(raised.exception.__cause__)
            self.assertIsNone(raised.exception.__context__)

    def test_unknown_format_fails_closed(self):
        with self.assertRaisesRegex(SecretFormatError, "format_unsupported"):
            parse_secret_document(b"SB_SYNTHETIC_NOT_REAL", "auto")

    def test_every_parser_failure_discards_input_and_traceback_chain(self):
        canary = "SB_SYNTHETIC_SECRET_CANARY_91ac"
        cases = (
            ("json", ('{"token":"' + canary + '",').encode()),
            ("toml", ("token = \"" + canary + "\"\n[").encode()),
            ("yaml", ("token: " + canary + "\nbroken: [").encode()),
            ("ini", ("token=" + canary).encode()),
            ("properties", ("token " + canary).encode()),
            ("xml", ("<root><token>" + canary + "</root>").encode()),
            ("pem", ("-----BEGIN PRIVATE KEY-----\n" + canary +
                     "\n-----END PRIVATE KEY-----\n").encode()),
            ("opaque", (canary + "\nsecond-line").encode()),
            ("binary", b""),
        )
        for format_name, content in cases:
            with self.subTest(format=format_name), self.assertRaises(SecretFormatError) as raised:
                parse_secret_document(content, format_name)
            error = raised.exception
            self.assertIsNone(error.__cause__)
            self.assertIsNone(error.__context__)
            rendered = "".join(traceback.format_exception(error))
            self.assertNotIn(canary, rendered)
            self.assertNotIn(repr(content), rendered)


if __name__ == "__main__":
    unittest.main()
