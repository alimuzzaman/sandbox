"""Audit-only coverage reconciliation for the synthetic parser lane.

The reconciler consumes :class:`~tools.audit_agent_usage.parser.ParseResult`
objects (or one explicit synthetic JSONL path) and emits a deterministic,
sanitized coverage manifest.  It deliberately does not inspect any corpus,
runtime, browser store, opaque store, remote host, subprocess, or registry.

Record accounting is kept separate from file, session, event, and command
units.  In particular, an event observation is not silently treated as a
command, and a source reference is not promoted to a session identifier.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Any, Iterable

from .parser import ParseResult, parse_jsonl
from .schema import (
    ACCOUNTING_SCHEMA,
    FIXTURE_SCHEMA,
    NORMALIZED_SCHEMA,
    safe_file_name,
)
from .validator import forbidden_values, validate_result


COVERAGE_SCHEMA = "audit-coverage-v1"
_TERMINAL_FIELDS = ("input", "parsed", "malformed", "duplicate", "excluded", "emitted")
_TERMINAL_STATES = ("malformed", "duplicate", "excluded", "emitted")
_SAFE_REASON_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_STATUS_ORDERS: dict[str, tuple[str, ...]] = {
    "transport_status": ("completed", "partial", "unavailable", "unknown"),
    "tool_call_status": ("completed", "failed", "partial", "unknown"),
    "command_exit_status": ("success", "failure", "timeout", "unknown"),
    "task_outcome": ("completed", "blocked", "unverified", "ambiguous", "unknown"),
}


def _json(value: Any) -> str:
    """Serialize a manifest with a stable, reviewable representation."""

    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _zero_terminal() -> OrderedDict[str, int]:
    return OrderedDict((field, 0) for field in _TERMINAL_FIELDS)


def _identity_strings(counts: dict[str, int]) -> dict[str, str]:
    return {
        "input": (
            f"{counts['input']} = {counts['malformed']} + {counts['duplicate']} + "
            f"{counts['excluded']} + {counts['emitted']}"
        ),
        "parsed": (
            f"{counts['parsed']} = {counts['duplicate']} + {counts['excluded']} + "
            f"{counts['emitted']}"
        ),
    }


def _assert_identity(label: str, counts: dict[str, int]) -> None:
    if any(not isinstance(counts.get(field), int) or counts[field] < 0 for field in _TERMINAL_FIELDS):
        raise ValueError(f"{label} counts must be non-negative integers")
    if counts["input"] != sum(counts[field] for field in ("malformed", "duplicate", "excluded", "emitted")):
        raise ValueError(f"{label} input identity mismatch")
    if counts["parsed"] != sum(counts[field] for field in ("duplicate", "excluded", "emitted")):
        raise ValueError(f"{label} parsed identity mismatch")


def _record_counts(result: ParseResult) -> OrderedDict[str, int]:
    """Recompute terminal counts from rows and compare them with parser data."""

    accounting = result.accounting
    counts = OrderedDict(
        (field, accounting.get(f"{field}_records", accounting.get(field)))
        for field in _TERMINAL_FIELDS
    )
    if not isinstance(accounting.get("parsed_records"), int):
        raise ValueError("missing parsed_records accounting")
    counts["parsed"] = accounting["parsed_records"]
    if not isinstance(accounting.get("input_records"), int):
        raise ValueError("missing input_records accounting")
    counts["input"] = accounting["input_records"]
    _assert_identity("record", counts)

    row_counts = Counter(row.get("terminal_state") for row in result.normalized)
    row_counts.update(row.get("terminal_state") for row in result.exclusions)
    for state in _TERMINAL_STATES:
        if row_counts[state] != counts[state]:
            raise ValueError(f"record {state} count does not match terminal rows")

    terminal_lines = [row.get("line_index") for row in result.normalized] + [
        row.get("line_index") for row in result.exclusions
    ]
    if sorted(terminal_lines) != list(range(1, counts["input"] + 1)):
        raise ValueError("record terminal partition is incomplete")
    return counts


def _check_exclusions(result: ParseResult) -> tuple[dict[str, int], list[dict[str, Any]]]:
    """Require a bounded reason for every non-emitted row."""

    reasons: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []
    for row in result.exclusions:
        reason = row.get("exclusion_reason")
        if not isinstance(reason, str) or not _SAFE_REASON_RE.fullmatch(reason):
            raise ValueError("every exclusion must have a bounded exclusion_reason")
        if row.get("terminal_state") not in {"malformed", "duplicate", "excluded"}:
            raise ValueError("exclusion has an invalid terminal state")
        reasons[reason] += 1
        rows.append(row)
    ordered_reasons = {reason: reasons[reason] for reason in sorted(reasons)}
    return ordered_reasons, rows


def _status_counts(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = OrderedDict()
    materialized = tuple(rows)
    for field, states in _STATUS_ORDERS.items():
        values = Counter(row.get(field) for row in materialized)
        result[field] = {state: values.get(state, 0) for state in states}
    return result


def _timestamp_counts(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    values = Counter(row.get("timestamp_state") for row in rows)
    return {state: values.get(state, 0) for state in ("present", "missing", "inversion", "invalid")}


def _relation_counts(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    values = Counter(row.get("relation_state") for row in rows)
    return {"unknown": values.get("unknown", 0)}


def _event_unit(result: ParseResult) -> dict[str, Any]:
    """Account for event observations without promoting them to commands.

    Emitted rows and duplicate exclusions carry enough evidence that an event
    was present.  A malformed line or a non-event exclusion remains an
    ``unknown`` event observation instead of being fabricated or discarded.
    """

    counts = _zero_terminal()
    counts["emitted"] = len(result.normalized)
    counts["duplicate"] = sum(
        1 for row in result.exclusions if row.get("terminal_state") == "duplicate"
    )
    counts["input"] = counts["emitted"] + counts["duplicate"]
    counts["parsed"] = counts["emitted"] + counts["duplicate"]
    unknown_records = {
        "malformed": sum(1 for row in result.exclusions if row.get("terminal_state") == "malformed"),
        "excluded": sum(1 for row in result.exclusions if row.get("terminal_state") == "excluded"),
    }
    return {
        **counts,
        "identities": _identity_strings(counts),
        "observed": counts["input"],
        "unknown": sum(unknown_records.values()),
        "unknown_records": unknown_records,
        "basis": "emitted rows plus duplicate exclusions; other records remain unknown",
    }


def _command_unit(rows: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    """Account for command-exit status observations as a separate unit.

    The parser has no command-execution record type in this fixture.  This
    unit therefore reports one status observation per emitted normalized row;
    ``unknown`` is retained and is not reclassified as a missing command.
    """

    status_values = Counter(row.get("command_exit_status") for row in rows)
    status_counts = {
        state: status_values.get(state, 0)
        for state in _STATUS_ORDERS["command_exit_status"]
    }
    observations = _zero_terminal()
    observations.update({"input": len(rows), "parsed": len(rows), "emitted": len(rows)})
    return {
        "observations": {
            **observations,
            "identities": _identity_strings(observations),
        },
        "observed": len(rows),
        "known": len(rows) - status_counts["unknown"],
        "unknown": status_counts["unknown"],
        "status_counts": status_counts,
        "basis": "emitted command_exit_status observations; unknown remains unknown",
    }


def _source_rows(
    result: ParseResult,
    record_counts: OrderedDict[str, int],
) -> list[dict[str, Any]]:
    """Return parser accounting per source with event/command unit details."""

    by_source = result.accounting.get("by_source")
    if not isinstance(by_source, list):
        raise ValueError("parser accounting is missing by_source")

    normalized_by_source: dict[str, list[dict[str, Any]]] = {}
    for row in result.normalized:
        label = row.get("source_label")
        if not isinstance(label, str):
            raise ValueError("normalized row has no safe source label")
        normalized_by_source.setdefault(label, []).append(row)
    exclusions_by_source: dict[str, list[dict[str, Any]]] = {}
    for row in result.exclusions:
        label = row.get("source_label") or "unknown"
        if not isinstance(label, str):
            label = "unknown"
        exclusions_by_source.setdefault(label, []).append(row)

    output: list[dict[str, Any]] = []
    source_fields = ("input_records", "parsed_records", "malformed", "duplicate", "excluded", "emitted")
    source_totals = _zero_terminal()
    for source in by_source:
        if not isinstance(source, dict) or not isinstance(source.get("source_label"), str):
            raise ValueError("parser accounting has an invalid source row")
        label = source["source_label"]
        row = OrderedDict((field, source.get(field)) for field in source_fields)
        row["source_label"] = label
        source_counts = OrderedDict(
            (
                ("input", source.get("input_records")),
                ("parsed", source.get("parsed_records")),
                ("malformed", source.get("malformed")),
                ("duplicate", source.get("duplicate")),
                ("excluded", source.get("excluded")),
                ("emitted", source.get("emitted")),
            )
        )
        _assert_identity(f"source:{label}", source_counts)
        for field in _TERMINAL_FIELDS:
            source_totals[field] += source_counts[field]
        source_events = _event_unit(
            ParseResult(
                tuple(normalized_by_source.get(label, ())),
                tuple(exclusions_by_source.get(label, ())),
                {"input_records": source_counts["input"], "parsed_records": source_counts["parsed"]},
            )
        )
        source_commands = _command_unit(tuple(normalized_by_source.get(label, ())))
        row["identity"] = _identity_strings(source_counts)["input"]
        row["parsed_identity"] = _identity_strings(source_counts)["parsed"]
        row["event"] = source_events
        row["command"] = source_commands
        output.append(dict(row))

    # Compare source totals against the top-level parser counters.  This also
    # catches a source bucket that was silently omitted from a manifest.
    for field in _TERMINAL_FIELDS:
        expected = record_counts[field]
        if source_totals[field] != expected:
            raise ValueError(f"source totals do not reconcile for {field}")
    return output


def _file_unit() -> dict[str, Any]:
    counts = OrderedDict(
        (
            ("input", 1),
            ("parsed", 1),
            ("malformed", 0),
            ("duplicate", 0),
            ("excluded", 0),
            ("emitted", 1),
        )
    )
    return {
        **counts,
        "identities": _identity_strings(counts),
        "basis": "one explicit fixture path successfully parsed",
    }


def _session_unit() -> dict[str, Any]:
    return {
        "observed": 0,
        "unknown": 1,
        "state": "unknown",
        "basis": "no session identifier is present; source_ref is not a session join key",
    }


def _safe_manifest_check(manifest: dict[str, Any]) -> None:
    errors = forbidden_values((manifest,))
    if errors:
        raise ValueError("coverage manifest failed forbidden-field validation")


def reconcile_coverage(
    result: ParseResult,
    *,
    input_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build a sanitized coverage manifest from one validated parse result."""

    validation = validate_result(result)
    if not validation.ok:
        raise ValueError("parser result failed validation")
    if result.accounting.get("fixture_schema") != FIXTURE_SCHEMA:
        raise ValueError("unsupported fixture schema")
    record_counts = _record_counts(result)
    exclusion_reasons, exclusions = _check_exclusions(result)
    normalized = tuple(result.normalized)
    fixture_file = result.accounting.get("fixture_file")
    if not isinstance(fixture_file, str):
        raise ValueError("parser accounting is missing fixture_file")
    fixture_file = safe_file_name(fixture_file)

    fixture_digest: str | None = None
    if input_path is not None:
        path = Path(input_path)
        if not path.is_file():
            raise FileNotFoundError(path)
        fixture_digest = hashlib.sha256(path.read_bytes()).hexdigest()

    status_layers = _status_counts(normalized)
    timestamp_states = _timestamp_counts(normalized)
    relation_states = _relation_counts(normalized)
    event_unit = _event_unit(result)
    command_unit = _command_unit(normalized)
    record_unit = {
        **record_counts,
        "identities": _identity_strings(record_counts),
        "terminal_states": list(_TERMINAL_STATES),
    }
    by_source = _source_rows(result, record_counts)

    manifest: OrderedDict[str, Any] = OrderedDict(
        (
            ("coverage_schema", COVERAGE_SCHEMA),
            ("accounting_schema", ACCOUNTING_SCHEMA),
            ("fixture_schema", FIXTURE_SCHEMA),
            ("normalized_schema", NORMALIZED_SCHEMA),
            ("fixture_file", fixture_file),
            ("fixture_sha256", fixture_digest),
            ("input_records", record_counts["input"]),
            ("parsed_records", record_counts["parsed"]),
            ("malformed", record_counts["malformed"]),
            ("duplicate", record_counts["duplicate"]),
            ("excluded", record_counts["excluded"]),
            ("emitted", record_counts["emitted"]),
            ("identities", _identity_strings(record_counts)),
            ("by_source", by_source),
            (
                "exclusions",
                {
                    "total": len(exclusions),
                    "with_reason": sum(1 for row in exclusions if row.get("exclusion_reason")),
                    "without_reason": sum(1 for row in exclusions if not row.get("exclusion_reason")),
                    "all_have_reason": all(bool(row.get("exclusion_reason")) for row in exclusions),
                    "by_reason": [
                        {"reason": reason, "count": count}
                        for reason, count in exclusion_reasons.items()
                    ],
                },
            ),
            ("status_layers", status_layers),
            (
                "uncertainty",
                {
                    "timestamp_state": timestamp_states,
                    "relation_state": relation_states,
                },
            ),
            (
                "units",
                {
                    "file": _file_unit(),
                    "record": record_unit,
                    "session": _session_unit(),
                    "event": event_unit,
                    "command": command_unit,
                },
            ),
        )
    )
    _safe_manifest_check(dict(manifest))
    return dict(manifest)


def reconcile_input(input_path: str | Path) -> dict[str, Any]:
    """Parse one explicit synthetic JSONL file and return its manifest."""

    path = Path(input_path)
    result = parse_jsonl(path)
    return reconcile_coverage(result, input_path=path)


def write_manifest(manifest: dict[str, Any], output_path: str | Path) -> Path:
    """Write one deterministic manifest to an explicit output path."""

    if manifest.get("coverage_schema") != COVERAGE_SCHEMA:
        raise ValueError("unsupported coverage schema")
    _safe_manifest_check(manifest)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json(manifest), encoding="utf-8")
    return path


def reconcile_to_directory(
    input_path: str | Path,
    output_dir: str | Path,
    *,
    manifest_name: str = "coverage-manifest.json",
) -> Path:
    """Emit one sanitized manifest into an explicit temporary directory."""

    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", manifest_name):
        raise ValueError("manifest_name must be a bounded basename")
    manifest = reconcile_input(input_path)
    return write_manifest(manifest, Path(output_dir) / manifest_name)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reconcile one synthetic audit JSONL fixture into a sanitized coverage manifest."
    )
    parser.add_argument("--input", required=True, type=Path, help="explicit synthetic JSONL input path")
    output = parser.add_mutually_exclusive_group(required=True)
    output.add_argument("--output", type=Path, help="explicit manifest output path")
    output.add_argument("--output-dir", type=Path, help="explicit output directory (writes coverage-manifest.json)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        output_path = args.output or (args.output_dir / "coverage-manifest.json")
        manifest = reconcile_input(args.input)
        write_manifest(manifest, output_path)
    except (OSError, ValueError):
        print(json.dumps({"ok": False, "error": "coverage_reconciliation_failed"}))
        return 2
    print(
        json.dumps(
            {
                "coverage_schema": manifest["coverage_schema"],
                "emitted": manifest["emitted"],
                "excluded": manifest["excluded"],
                "input_records": manifest["input_records"],
                "ok": True,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "COVERAGE_SCHEMA",
    "build_parser",
    "main",
    "reconcile_coverage",
    "reconcile_input",
    "reconcile_to_directory",
    "write_manifest",
]
