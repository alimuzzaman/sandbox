import unittest

from sandbox.secrets.parser import (
    AssignmentRecord,
    SecretParseError,
    parse_document,
    render_assignment,
    remove_assignment,
    replace_assignment,
)


class SecretParserTests(unittest.TestCase):
    def assert_refused(self, content, code, **limits):
        with self.assertRaises(SecretParseError) as raised:
            parse_document(content, **limits)
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(str(raised.exception), code)

    def test_parses_only_inert_literal_assignments_and_preserves_syntax(self):
        source = (
            b"# synthetic fixture only\n"
            b"\n"
            b"export FIRST='value with spaces'\n"
            b'SECOND="literal value"\n'
            b"THIRD='dollar$and`backtick`stay-literal'\n"
            b"EMPTY=\n"
        )

        document = parse_document(source)

        self.assertEqual(document.newline_style, "lf")
        self.assertEqual([record.kind for record in document.records], [
            "comment", "blank", "assignment", "assignment", "assignment", "assignment",
        ])
        self.assertEqual(tuple(document.entries), ("FIRST", "SECOND", "THIRD", "EMPTY"))
        self.assertEqual(document.entries["FIRST"].value, "value with spaces")
        self.assertEqual(document.entries["SECOND"].value, "literal value")
        self.assertEqual(document.entries["THIRD"].value, "dollar$and`backtick`stay-literal")
        self.assertEqual(document.entries["EMPTY"].value, "")
        self.assertEqual(document.render(), source)

    def test_accepts_export_spacing_and_escaped_literal_dollar(self):
        document = parse_document(b"  export   TOKEN=escaped\\$literal\n")
        entry = document.entries["TOKEN"]
        self.assertTrue(entry.exported)
        self.assertEqual(entry.value, "escaped$literal")
        self.assertEqual(entry.assignment_prefix, "  export   TOKEN=")

    def test_detects_lf_crlf_and_mixed_endings(self):
        self.assertEqual(parse_document(b"A=one\nB=two\n").newline_style, "lf")
        self.assertEqual(parse_document(b"A=one\r\nB=two\r\n").newline_style, "crlf")
        mixed = parse_document(b"A=one\r\nB=two\n")
        self.assertEqual(mixed.newline_style, "mixed")
        with self.assertRaises(SecretParseError) as raised:
            replace_assignment(mixed, "A", "replacement")
        self.assertEqual(raised.exception.code, "mixed_newlines")

    def test_document_and_record_representations_do_not_disclose_values(self):
        marker = "synthetic-sensitive-marker"
        document = parse_document(f"TOKEN={marker}\n".encode())
        record = document.entries["TOKEN"]

        self.assertNotIn(marker, repr(document))
        self.assertNotIn(marker, repr(record))
        self.assertIn("TOKEN", repr(record))

    def test_rejects_duplicate_keys_without_echoing_input(self):
        marker = "synthetic-sensitive-marker"
        with self.assertRaises(SecretParseError) as raised:
            parse_document(f"TOKEN={marker}\nTOKEN=other\n".encode())
        self.assertEqual(raised.exception.code, "duplicate_key")
        self.assertNotIn(marker, str(raised.exception))

    def test_rejects_invalid_encoding_and_terminal_controls(self):
        with self.assertRaises(SecretParseError) as raised:
            parse_document(b"TOKEN=\xff\n")
        self.assertEqual(raised.exception.code, "invalid_encoding")
        self.assertIsNone(raised.exception.__context__)
        self.assert_refused(b"TOKEN=value\x00tail\n", "control_character")
        self.assert_refused(b"# comment\x1b[31m\nTOKEN=value\n", "control_character")
        self.assert_refused(b"TOKEN=value\r", "control_character")

    def test_rejects_invalid_or_oversized_keys(self):
        self.assert_refused(b"1TOKEN=value\n", "unsupported_syntax")
        oversized = ("A" * 129 + "=value\n").encode()
        self.assert_refused(oversized, "invalid_key")

    def test_rejects_expansion_and_command_forms_without_evaluation(self):
        rejected = (
            b"TOKEN=$OTHER\n",
            b'TOKEN="${OTHER}"\n',
            b"TOKEN=$(printf synthetic)\n",
            b"TOKEN=`printf synthetic`\n",
            b"TOKEN=<(printf synthetic)\n",
            b"TOKEN=>(printf synthetic)\n",
            b"TOKEN=~/synthetic\n",
        )
        for source in rejected:
            with self.subTest(source=source.split(b"=", 1)[1][:2]):
                self.assert_refused(source, "expansion_not_allowed")

    def test_rejects_unsupported_statements_and_malformed_quotes(self):
        self.assert_refused(b"source .env.local\n", "unsupported_syntax")
        self.assert_refused(b"TOKEN=two words\n", "unsupported_syntax")
        self.assert_refused(b"TOKEN='unterminated\n", "invalid_quoting")
        self.assert_refused(b"export TOKEN=value trailing\n", "unsupported_syntax")

    def test_enforces_assignment_and_value_bounds(self):
        accepted = parse_document(b"A=1234\n", max_entries=1, max_value_bytes=4)
        self.assertEqual(accepted.entries["A"].value, "1234")
        self.assert_refused(b"A=1\nB=2\n", "too_many_entries", max_entries=1)
        self.assert_refused(b"TOKEN=12345\n", "value_too_large", max_value_bytes=4)
        self.assert_refused(b"TOKEN=value\n", "source_too_large", max_bytes=11)

    def test_replacement_changes_only_one_record_and_quotes_candidate(self):
        source = (
            b"# keep this exactly\r\n"
            b"  export   TOKEN=old\r\n"
            b"OTHER='unchanged value'\r\n"
        )
        document = parse_document(source)

        rendered = replace_assignment(document, "TOKEN", "new 'quoted' $value")

        self.assertEqual(
            rendered,
            b"# keep this exactly\r\n"
            b"  export   TOKEN='new '\"'\"'quoted'\"'\"' $value'\r\n"
            b"OTHER='unchanged value'\r\n",
        )
        reparsed = parse_document(rendered)
        self.assertEqual(reparsed.entries["TOKEN"].value, "new 'quoted' $value")
        self.assertEqual(reparsed.entries["OTHER"].value, "unchanged value")

    def test_render_assignment_is_single_line_and_bounded(self):
        self.assertEqual(render_assignment("TOKEN", "safe value", exported=True), "export TOKEN='safe value'")
        candidates = (
            ("", "empty_value"),
            ("line\nbreak", "multiline_value"),
            ("nul\x00value", "control_character"),
        )
        for value, code in candidates:
            with self.subTest(code=code):
                with self.assertRaises(SecretParseError) as raised:
                    render_assignment("TOKEN", value, max_value_bytes=64)
                self.assertEqual(raised.exception.code, code)
        with self.assertRaises(SecretParseError) as raised:
            render_assignment("TOKEN", "12345", max_value_bytes=4)
        self.assertEqual(raised.exception.code, "value_too_large")
        with self.assertRaises(SecretParseError) as raised:
            render_assignment("TOKEN", "synthetic\udcffmarker")
        self.assertEqual(raised.exception.code, "invalid_encoding")
        self.assertIsNone(raised.exception.__context__)

    def test_replace_requires_existing_single_assignment(self):
        document = parse_document(b"OTHER=value\n")
        with self.assertRaises(SecretParseError) as raised:
            replace_assignment(document, "TOKEN", "replacement")
        self.assertEqual(raised.exception.code, "missing_key")

    def test_remove_preserves_unrelated_records_and_supports_mixed_newlines(self):
        source = b"# keep\r\nREMOVE=fixture\nOTHER='unchanged'"
        document = parse_document(source)
        self.assertEqual(
            remove_assignment(document, "REMOVE"),
            b"# keep\r\nOTHER='unchanged'",
        )

    def test_remove_requires_an_existing_valid_key(self):
        document = parse_document(b"OTHER=value\n")
        with self.assertRaises(SecretParseError) as raised:
            remove_assignment(document, "MISSING")
        self.assertEqual(raised.exception.code, "missing_key")


if __name__ == "__main__":
    unittest.main()
