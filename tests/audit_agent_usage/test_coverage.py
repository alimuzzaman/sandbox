from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.audit_agent_usage.coverage import (
    COVERAGE_SCHEMA,
    main as coverage_main,
    reconcile_coverage,
    reconcile_input,
    reconcile_to_directory,
    write_manifest,
)
from tools.audit_agent_usage.parser import parse_jsonl
from tools.audit_agent_usage.validator import forbidden_values


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "docs/audits/2026-08-24-sandbox-agent-tool-audit"
INPUT = AUDIT / "fixtures/synthetic-events.jsonl"


class CoverageReconcilerTests(unittest.TestCase):
    def test_manifest_reconciles_disjoint_record_identity_and_separate_units(self) -> None:
        manifest = reconcile_input(INPUT)
        self.assertEqual(manifest["coverage_schema"], COVERAGE_SCHEMA)
        self.assertEqual(
            {
                field: manifest[field]
                for field in ("input_records", "parsed_records", "malformed", "duplicate", "excluded", "emitted")
            },
            {
                "input_records": 19,
                "parsed_records": 18,
                "malformed": 1,
                "duplicate": 2,
                "excluded": 1,
                "emitted": 15,
            },
        )
        self.assertEqual(manifest["identities"]["input"], "19 = 1 + 2 + 1 + 15")
        self.assertEqual(manifest["identities"]["parsed"], "18 = 2 + 1 + 15")
        self.assertEqual(
            set(manifest["units"]), {"file", "record", "session", "event", "command"}
        )
        self.assertNotEqual(manifest["units"]["record"]["input"], manifest["units"]["event"]["input"])
        self.assertNotEqual(manifest["units"]["event"]["input"], manifest["units"]["command"]["observed"])
        self.assertEqual(manifest["units"]["session"]["state"], "unknown")
        self.assertEqual(manifest["units"]["session"]["observed"], 0)

    def test_manifest_keeps_exclusion_reasons_and_status_layers(self) -> None:
        manifest = reconcile_input(INPUT)
        exclusions = manifest["exclusions"]
        self.assertEqual(exclusions["total"], 4)
        self.assertEqual(exclusions["with_reason"], 4)
        self.assertEqual(exclusions["without_reason"], 0)
        self.assertTrue(exclusions["all_have_reason"])
        self.assertEqual(
            {row["reason"]: row["count"] for row in exclusions["by_reason"]},
            {
                "duplicate_event": 1,
                "duplicate_rollover_event": 1,
                "invalid_jsonl": 1,
                "unsupported_record_kind": 1,
            },
        )
        self.assertEqual(
            manifest["status_layers"]["transport_status"],
            {"completed": 13, "partial": 1, "unavailable": 1, "unknown": 0},
        )
        self.assertEqual(
            manifest["status_layers"]["tool_call_status"],
            {"completed": 11, "failed": 2, "partial": 1, "unknown": 1},
        )
        self.assertEqual(
            manifest["status_layers"]["command_exit_status"],
            {"success": 9, "failure": 2, "timeout": 1, "unknown": 3},
        )
        self.assertEqual(
            manifest["status_layers"]["task_outcome"],
            {"completed": 3, "blocked": 2, "unverified": 2, "ambiguous": 2, "unknown": 6},
        )
        self.assertEqual(manifest["units"]["event"]["unknown"], 2)
        self.assertEqual(manifest["units"]["command"]["unknown"], 3)

    def test_manifest_is_sanitized_and_two_writes_are_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_path = reconcile_to_directory(INPUT, first)
            second_path = reconcile_to_directory(INPUT, second)
            self.assertEqual(first_path.read_bytes(), second_path.read_bytes())
            manifest = json.loads(first_path.read_text(encoding="utf-8"))
            self.assertEqual(forbidden_values((manifest,)), ())
            self.assertEqual(manifest["fixture_file"], "synthetic-events.jsonl")
            self.assertNotIn(str(INPUT.parent), first_path.read_text(encoding="utf-8"))

    def test_missing_exclusion_reason_is_rejected_before_manifest_write(self) -> None:
        result = parse_jsonl(INPUT)
        exclusions = list(result.exclusions)
        exclusions[0] = {**exclusions[0], "exclusion_reason": ""}
        broken = result.__class__(result.normalized, tuple(exclusions), result.accounting)
        with self.assertRaises(ValueError):
            reconcile_coverage(broken)

    def test_cli_emits_manifest_to_explicit_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "manifest.json"
            self.assertEqual(
                coverage_main(["--input", str(INPUT), "--output", str(output)]),
                0,
            )
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["coverage_schema"], COVERAGE_SCHEMA)

    def test_write_manifest_rejects_wrong_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                write_manifest({"coverage_schema": "wrong"}, Path(directory) / "manifest.json")


if __name__ == "__main__":
    unittest.main()
