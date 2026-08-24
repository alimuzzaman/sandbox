from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.audit_agent_usage.__main__ import main as cli_main
from tools.audit_agent_usage.parser import ParseResult, parse_jsonl, parse_to_directory, write_result
from tools.audit_agent_usage.redactor import formula_safe, redact_nested
from tools.audit_agent_usage.schema import safe_source_ref
from tools.audit_agent_usage.validator import validate_output_files, validate_result


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "docs/audits/2026-08-24-sandbox-agent-tool-audit"
FIXTURES = AUDIT / "fixtures"
INPUT = FIXTURES / "synthetic-events.jsonl"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class SyntheticFixtureTests(unittest.TestCase):
    def test_fixture_rows_and_accounting_match_accepted_contract(self) -> None:
        result = parse_jsonl(INPUT)
        self.assertEqual(
            list(result.normalized), read_jsonl(FIXTURES / "expected-normalized.jsonl")
        )
        self.assertEqual(
            list(result.exclusions), read_jsonl(FIXTURES / "expected-exclusions.jsonl")
        )
        self.assertEqual(
            result.accounting,
            json.loads((FIXTURES / "expected-accounting.json").read_text(encoding="utf-8")),
        )

    def test_validator_accepts_fixture_and_keeps_layers_separate(self) -> None:
        result = parse_jsonl(INPUT)
        validation = validate_result(result)
        self.assertTrue(validation.ok, validation.errors)
        row_by_line = {row["line_index"]: row for row in result.normalized}
        self.assertEqual(row_by_line[11]["transport_status"], "partial")
        self.assertEqual(row_by_line[11]["tool_call_status"], "partial")
        self.assertEqual(row_by_line[11]["command_exit_status"], "unknown")
        self.assertEqual(row_by_line[11]["task_outcome"], "unknown")
        self.assertEqual(row_by_line[14]["command_exit_status"], "success")
        self.assertEqual(row_by_line[14]["task_outcome"], "ambiguous")

    def test_order_timestamp_and_parent_child_uncertainty_are_preserved(self) -> None:
        result = parse_jsonl(INPUT)
        rows = list(result.normalized)
        self.assertEqual([row["line_index"] for row in rows], [1, 2, 4, 5, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 19])
        by_line = {row["line_index"]: row for row in rows}
        self.assertEqual(by_line[9]["timestamp_state"], "missing")
        self.assertEqual(by_line[10]["timestamp_state"], "inversion")
        self.assertEqual(by_line[15]["relation_signal"], "parent_candidate")
        self.assertEqual(by_line[16]["relation_signal"], "child_candidate")
        self.assertEqual(by_line[15]["relation_state"], "unknown")
        self.assertEqual(by_line[16]["relation_state"], "unknown")
        self.assertEqual(by_line[15]["relation_join"], "unverified")

    def test_duplicate_malformed_and_unsupported_have_one_terminal_row_each(self) -> None:
        result = parse_jsonl(INPUT)
        exclusions = list(result.exclusions)
        self.assertEqual([row["line_index"] for row in exclusions], [3, 6, 17, 18])
        self.assertEqual([row["terminal_state"] for row in exclusions], ["duplicate", "duplicate", "excluded", "malformed"])
        self.assertEqual(exclusions[0]["duplicate_of_line"], 2)
        self.assertEqual(exclusions[1]["duplicate_of_line"], 5)
        self.assertIsNone(exclusions[-1]["source_ref"])
        self.assertEqual(result.accounting["input_records"], 19)
        self.assertEqual(result.accounting["parsed_records"], 18)
        self.assertEqual(result.accounting["input_records"], 1 + 2 + 1 + 15)
        self.assertEqual(result.accounting["parsed_records"], 2 + 1 + 15)

    def test_redaction_and_formula_projection_never_keeps_nested_values(self) -> None:
        result = parse_jsonl(INPUT)
        by_line = {row["line_index"]: row for row in result.normalized}
        nested = by_line[7]
        self.assertEqual(nested["argument_signature"], {"target_class": "remote", "metadata": "redacted"})
        self.assertNotIn("access_token", json.dumps(nested))
        self.assertNotIn("cookie", json.dumps(nested))
        formula = by_line[8]
        self.assertEqual(
            formula["formula_safe_values"],
            {
                "equals": "formula_candidate",
                "plus": "formula_candidate",
                "minus": "formula_candidate",
                "at": "formula_candidate",
            },
        )
        for value in formula["formula_safe_values"].values():
            self.assertIn(value, {"present", "formula_candidate"})
        self.assertEqual(formula_safe("=SYNTHETIC()"), "'=SYNTHETIC()")

    def test_bounded_nested_redactor(self) -> None:
        projected = redact_nested(
            {
                "outer": {"api_key": "<redacted>", "safe": "synthetic-value"},
                "items": ["=SYNTHETIC()"],
            }
        )
        self.assertEqual(projected["outer"]["api_key"], "redacted")
        self.assertEqual(projected["outer"]["safe"], "synthetic-value")
        self.assertEqual(projected["items"], ["'=SYNTHETIC()"])

    def test_unknown_fields_are_ignored_and_unknown_source_is_safe(self) -> None:
        record = {
            "fixture_schema": "audit-fixture-v1",
            "source_label": "synthetic-owner-label",
            "source_ref": "synthetic-private-id",
            "record_kind": "tool_event",
            "event_index": 1,
            "event_key": "SYN-UNKNOWN-001",
            "event": {
                "namespace": "sandbox",
                "name": "job-status",
                "arguments": {
                    "target_class": "unclassified",
                    "unknown_field": "synthetic prose that must not survive",
                },
            },
            "status": {},
            "future_field": {"access_token": "<redacted>"},
        }
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "synthetic-unknown.jsonl"
            source.write_text(json.dumps(record) + "\n", encoding="utf-8")
            result = parse_jsonl(source)
        self.assertEqual(len(result.normalized), 1)
        row = result.normalized[0]
        self.assertEqual(row["source_label"], "unknown")
        self.assertTrue(row["source_ref"].startswith("SAFE-SRC-"))
        self.assertEqual(row["argument_signature"], {})
        self.assertEqual(row["task_outcome"], "unknown")
        self.assertNotIn("synthetic prose", json.dumps(row))
        validation = validate_result(result)
        self.assertTrue(validation.ok, validation.errors)

    def test_label_projection_is_closed_and_drops_prose_and_email_values(self) -> None:
        record = {
            "fixture_schema": "audit-fixture-v1",
            "source_label": "CODEX-LOCAL-EXACT-CWD",
            "source_ref": "SYN-LABEL-001",
            "record_kind": "tool_event",
            "event_index": 1,
            "event_key": "SYN-LABEL-EVENT",
            "event": {
                "namespace": "sandbox",
                "name": "feedback",
                "arguments": {
                    "labels": {
                        "equals": "operator@example.com",
                        "plus": "synthetic prose must not survive",
                        "minus": "=SYNTHETIC_FORMULA()",
                        "at": 42,
                        "unapproved": "unapproved value",
                        "description": "another prose value",
                    }
                },
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "synthetic-labels.jsonl"
            source.write_text(json.dumps(record) + "\n", encoding="utf-8")
            result = parse_jsonl(source)

        row = result.normalized[0]
        self.assertEqual(
            row["formula_safe_values"],
            {"equals": "present", "plus": "present", "minus": "formula_candidate"},
        )
        rendered = json.dumps(row)
        self.assertNotIn("operator@example.com", rendered)
        self.assertNotIn("synthetic prose", rendered)
        self.assertNotIn("unapproved", rendered)
        self.assertTrue(validate_result(result).ok)

    def test_missing_explicit_index_is_excluded_without_inference(self) -> None:
        record = {
            "fixture_schema": "audit-fixture-v1",
            "source_label": "CODEX-LOCAL-EXACT-CWD",
            "source_ref": "SYN-MISSING-INDEX-001",
            "record_kind": "tool_event",
            "event_key": "SYN-MISSING-INDEX-EVENT",
            "event": {"namespace": "sandbox", "name": "job-status"},
        }
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "synthetic-missing-index.jsonl"
            source.write_text(json.dumps(record) + "\n", encoding="utf-8")
            result = parse_jsonl(source)
        self.assertEqual(result.normalized, ())
        self.assertEqual(result.exclusions[0]["exclusion_reason"], "missing_event_index")
        self.assertTrue(validate_result(result).ok)

    def test_unrecognized_relation_join_is_reduced_to_unknown(self) -> None:
        record = {
            "fixture_schema": "audit-fixture-v1",
            "source_label": "CODEX-LOCAL-EXACT-CWD",
            "source_ref": "SYN-RELATION-001",
            "record_kind": "tool_event",
            "event_index": 1,
            "event_key": "SYN-RELATION-EVENT",
            "relation_signal": {
                "role": "parent_candidate",
                "join_status": "synthetic-parent-identifier",
            },
            "event": {"namespace": "sandbox", "name": "exec"},
        }
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "synthetic-relation.jsonl"
            source.write_text(json.dumps(record) + "\n", encoding="utf-8")
            result = parse_jsonl(source)
        self.assertEqual(result.normalized[0]["relation_state"], "unknown")
        self.assertEqual(result.normalized[0]["relation_join"], "unknown")
        self.assertTrue(validate_result(result).ok)

    def test_safe_source_reference_is_stable_and_non_reversible(self) -> None:
        first = safe_source_ref("SYNTHETIC-RAW-ID")
        second = safe_source_ref("SYNTHETIC-RAW-ID")
        self.assertEqual(first, second)
        self.assertEqual(first, "SAFE-SRC-" + hashlib.sha256(b"sandbox-agent-tool-audit:v1:SYNTHETIC-RAW-ID").hexdigest()[:20])
        self.assertNotIn("SYNTHETIC-RAW-ID", first)

    def test_two_clean_writes_are_byte_identical_and_file_validator_accepts(self) -> None:
        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            parse_to_directory(INPUT, first_dir)
            parse_to_directory(INPUT, second_dir)
            for filename in ("normalized.jsonl", "exclusions.jsonl", "accounting.json"):
                self.assertEqual(
                    (Path(first_dir) / filename).read_bytes(),
                    (Path(second_dir) / filename).read_bytes(),
                    filename,
                )
            validation = validate_output_files(
                Path(first_dir) / "normalized.jsonl",
                Path(first_dir) / "exclusions.jsonl",
                Path(first_dir) / "accounting.json",
            )
            self.assertTrue(validation.ok, validation.errors)

    def test_write_result_validates_before_creating_or_overwriting_outputs(self) -> None:
        result = parse_jsonl(INPUT)
        broken_row = dict(result.normalized[0])
        broken_row["formula_safe_values"] = {"equals": "operator@example.com"}
        broken = ParseResult(
            normalized=(broken_row,) + result.normalized[1:],
            exclusions=result.exclusions,
            accounting=result.accounting,
        )
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "derived"
            with self.assertRaises(ValueError):
                write_result(broken, output_dir)
            self.assertFalse(output_dir.exists())

    def test_write_result_replaces_each_file_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "derived"
            parse_to_directory(INPUT, output_dir)
            before = {
                path.name: path.read_bytes()
                for path in output_dir.iterdir()
                if path.is_file()
            }
            result = parse_jsonl(INPUT)
            write_result(result, output_dir)
            after = {
                path.name: path.read_bytes()
                for path in output_dir.iterdir()
                if path.is_file()
            }
            self.assertEqual(after, before)
            self.assertEqual(
                sorted(path.name for path in output_dir.iterdir()),
                ["accounting.json", "exclusions.jsonl", "normalized.jsonl"],
            )

    def test_write_result_rolls_back_when_a_file_replace_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "derived"
            parse_to_directory(INPUT, output_dir)
            before = {
                path.name: path.read_bytes()
                for path in output_dir.iterdir()
                if path.is_file()
            }
            result = parse_jsonl(INPUT)

            import tools.audit_agent_usage.atomic as atomic_module

            original_replace = atomic_module.os.replace
            calls = 0

            def fail_once(source: str | bytes | Path, destination: str | bytes | Path) -> None:
                nonlocal calls
                calls += 1
                if calls == 5:
                    raise OSError("synthetic replace failure")
                original_replace(source, destination)

            with patch.object(atomic_module.os, "replace", side_effect=fail_once):
                with self.assertRaises(OSError):
                    write_result(result, output_dir)

            after = {
                path.name: path.read_bytes()
                for path in output_dir.iterdir()
                if path.is_file()
            }
            self.assertEqual(after, before)
            self.assertEqual(
                [path.name for path in output_dir.iterdir() if path.name.endswith((".tmp", ".bak"))],
                [],
            )

    def test_write_result_cleans_staging_files_when_backup_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "derived"
            parse_to_directory(INPUT, output_dir)
            before = {
                path.name: path.read_bytes()
                for path in output_dir.iterdir()
                if path.is_file()
            }
            result = parse_jsonl(INPUT)

            import tools.audit_agent_usage.atomic as atomic_module

            original_replace = atomic_module.os.replace
            failed = False

            def fail_backup_once(source: str | bytes | Path, destination: str | bytes | Path) -> None:
                nonlocal failed
                if not failed:
                    failed = True
                    raise OSError("synthetic backup failure")
                original_replace(source, destination)

            with patch.object(atomic_module.os, "replace", side_effect=fail_backup_once):
                with self.assertRaises(OSError):
                    write_result(result, output_dir)

            after = {
                path.name: path.read_bytes()
                for path in output_dir.iterdir()
                if path.is_file()
            }
            self.assertEqual(after, before)
            self.assertEqual(
                [path.name for path in output_dir.iterdir() if path.name.endswith((".tmp", ".bak"))],
                [],
            )

    def test_write_result_preserves_backups_when_restore_fails_persistently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "derived"
            parse_to_directory(INPUT, output_dir)
            before = {
                path.name: path.read_bytes()
                for path in output_dir.iterdir()
                if path.is_file()
            }
            result = parse_jsonl(INPUT)

            import tools.audit_agent_usage.atomic as atomic_module

            original_replace = atomic_module.os.replace
            calls = 0

            def fail_commit_and_restore(
                source: str | bytes | Path, destination: str | bytes | Path
            ) -> None:
                nonlocal calls
                calls += 1
                if str(source).endswith(".bak") or calls == 5:
                    raise OSError("persistent synthetic replace failure")
                original_replace(source, destination)

            with patch.object(atomic_module.os, "replace", side_effect=fail_commit_and_restore):
                with self.assertRaises(OSError):
                    write_result(result, output_dir)

            self.assertEqual(calls, 8)  # 3 backups + 2 commits + 3 restore attempts.
            backups = sorted(output_dir.glob(".*.bak"))
            self.assertEqual(len(backups), len(before))
            for backup in backups:
                filename = next(
                    name for name in before if backup.name.startswith(f".{name}.")
                )
                self.assertEqual(backup.read_bytes(), before[filename])

            # The retained backups are sufficient to restore the prior set;
            # clean them up through the normal filesystem path in this test.
            for backup in backups:
                filename = next(
                    name for name in before if backup.name.startswith(f".{name}.")
                )
                original_replace(backup, output_dir / filename)
            self.assertEqual(
                {
                    path.name: path.read_bytes()
                    for path in output_dir.iterdir()
                    if path.is_file()
                },
                before,
            )

    def test_cli_requires_explicit_input_and_output_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "cli-output"
            self.assertEqual(
                cli_main(["--input", str(INPUT), "--output-dir", str(output_dir)]),
                0,
            )
            self.assertTrue((output_dir / "normalized.jsonl").is_file())
            self.assertTrue((output_dir / "exclusions.jsonl").is_file())
            self.assertTrue((output_dir / "accounting.json").is_file())

    def test_validator_rejects_collapsed_terminal_partition(self) -> None:
        result = parse_jsonl(INPUT)
        exclusions = list(result.exclusions)
        exclusions.pop()
        broken = result.__class__(result.normalized, tuple(exclusions), result.accounting)
        validation = validate_result(broken)
        self.assertFalse(validation.ok)
        self.assertIn("terminal_partition", validation.errors)


if __name__ == "__main__":
    unittest.main()
