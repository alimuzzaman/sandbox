"""Fail-closed validation for audit-only parser output."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .parser import ParseResult
from .schema import (
    ACCOUNTING_SCHEMA,
    COMMAND_EXIT_STATUSES,
    MAX_ARGUMENT_KEYS,
    MAX_FORMULA_VALUE_LENGTH,
    MAX_STRING_LENGTH,
    NORMALIZED_SCHEMA,
    RELATION_SIGNALS,
    SOURCE_LABELS,
    TASK_OUTCOMES,
    TERMINAL_STATES,
    TIMESTAMP_STATES,
    TOOL_CALL_STATUSES,
    TRANSPORT_STATUSES,
    is_safe_source_ref,
)


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    errors: tuple[str, ...]

    def require_ok(self) -> "ValidationResult":
        if not self.ok:
            raise ValueError("; ".join(self.errors))
        return self


_PRIVATE_PATH_RE = re.compile(r"/(?:Users|home)/[^/\s]+")
_PRIVATE_URL_RE = re.compile(
    r"https?://(?:localhost|127\.[0-9.]+|10\.[0-9.]+|192\.168\.[0-9.]+|"
    r"172\.(?:1[6-9]|2[0-9]|3[01])\.[0-9.]+|[^/\s]+\.tst)(?:[:/]|$)",
    re.IGNORECASE,
)
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_SENSITIVE_KEY_RE = re.compile(
    r"(?:password|passwd|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"private[_-]?key|authorization|cookie|credential|secret|bearer)",
    re.IGNORECASE,
)


def _walk(value: Any, path: str = "$") -> Iterable[tuple[str, Any, str]]:
    yield path, value, "value"
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            yield child_path, key, "key"
            yield from _walk(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{path}[{index}]")


def forbidden_values(rows: Iterable[dict[str, Any]]) -> tuple[str, ...]:
    """Return safe path-only diagnostics for values forbidden in output."""

    errors: list[str] = []
    for row_index, row in enumerate(rows):
        for path, value, kind in _walk(row, f"$[{row_index}]"):
            if kind == "key":
                continue
            if not isinstance(value, str):
                continue
            if _PRIVATE_PATH_RE.search(value):
                errors.append(f"private_path:{path}")
            if _PRIVATE_URL_RE.search(value):
                errors.append(f"private_url:{path}")
            if _UUID_RE.search(value):
                errors.append(f"raw_uuid:{path}")
            if value.startswith(("=", "+", "-", "@")):
                errors.append(f"formula_unescaped:{path}")
            if len(value) > MAX_FORMULA_VALUE_LENGTH:
                errors.append(f"string_too_long:{path}")
            if _SENSITIVE_KEY_RE.search(path.rsplit(".", 1)[-1]) and value not in {
                "redacted",
                "unknown",
            }:
                errors.append(f"sensitive_value:{path}")
    return tuple(errors)


def _load_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        with path.open("r", encoding="utf-8", newline="") as stream:
            for line_index, raw_line in enumerate(stream, start=1):
                try:
                    row = json.loads(raw_line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    errors.append(f"jsonl:{line_index}")
                    continue
                if not isinstance(row, dict):
                    errors.append(f"row_shape:{line_index}")
                    continue
                rows.append(row)
    except OSError:
        errors.append("file_unreadable")
    return rows, errors


def _load_json(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None, ["json_unreadable"]
    if not isinstance(value, dict):
        return None, ["json_shape"]
    return value, []


def _validate_normalized(rows: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    line_indexes: list[int] = []
    seen_event_indexes: set[tuple[str, int]] = set()
    required = {
        "normalized_schema",
        "fixture_file",
        "source_label",
        "source_ref",
        "line_index",
        "event_index",
        "event_type",
        "tool_namespace",
        "tool_name",
        "argument_signature",
        "timestamp",
        "timestamp_state",
        "ordering_basis",
        "relation_signal",
        "relation_state",
        "transport_status",
        "tool_call_status",
        "command_exit_status",
        "task_outcome",
        "terminal_state",
    }
    allowed = required | {"formula_safe_values", "relation_join"}

    for row_number, row in enumerate(rows, start=1):
        missing = required - row.keys()
        if missing:
            errors.append(f"normalized_missing:{row_number}")
        unknown = set(row) - allowed
        if unknown:
            errors.append(f"normalized_unknown:{row_number}")
        if row.get("normalized_schema") != NORMALIZED_SCHEMA:
            errors.append(f"normalized_schema:{row_number}")
        if row.get("source_label") not in SOURCE_LABELS:
            errors.append(f"source_label:{row_number}")
        if not is_safe_source_ref(row.get("source_ref")):
            errors.append(f"source_ref:{row_number}")
        line_index = row.get("line_index")
        if not isinstance(line_index, int) or line_index < 1:
            errors.append(f"line_index:{row_number}")
        else:
            line_indexes.append(line_index)
        event_index = row.get("event_index")
        if not isinstance(event_index, int) or event_index < 0:
            errors.append(f"event_index:{row_number}")
        else:
            event_key = (row.get("source_ref", ""), event_index)
            if event_key in seen_event_indexes:
                errors.append(f"event_index_duplicate:{row_number}")
            seen_event_indexes.add(event_key)
        if row.get("event_type") not in {"tool_call", "content_block_tool_call"}:
            errors.append(f"event_type:{row_number}")
        if not isinstance(row.get("tool_namespace"), str) or len(row["tool_namespace"]) > 64:
            errors.append(f"tool_namespace:{row_number}")
        if not isinstance(row.get("tool_name"), str) or len(row["tool_name"]) > 64:
            errors.append(f"tool_name:{row_number}")
        signature = row.get("argument_signature")
        if not isinstance(signature, dict) or len(signature) > MAX_ARGUMENT_KEYS:
            errors.append(f"argument_signature:{row_number}")
        if row.get("timestamp_state") not in TIMESTAMP_STATES:
            errors.append(f"timestamp_state:{row_number}")
        if row.get("timestamp_state") == "missing" and row.get("timestamp") is not None:
            errors.append(f"timestamp_missing:{row_number}")
        if row.get("timestamp_state") in {"present", "inversion"} and not isinstance(
            row.get("timestamp"), str
        ):
            errors.append(f"timestamp_present:{row_number}")
        if row.get("ordering_basis") != "file_order":
            errors.append(f"ordering_basis:{row_number}")
        if row.get("relation_signal") not in RELATION_SIGNALS:
            errors.append(f"relation_signal:{row_number}")
        if row.get("relation_state") != "unknown":
            errors.append(f"relation_state:{row_number}")
        if row.get("relation_signal") in {"parent_candidate", "child_candidate"} and row.get(
            "relation_join"
        ) not in {"unverified", "unknown"}:
            errors.append(f"relation_join:{row_number}")
        if row.get("transport_status") not in TRANSPORT_STATUSES:
            errors.append(f"transport_status:{row_number}")
        if row.get("tool_call_status") not in TOOL_CALL_STATUSES:
            errors.append(f"tool_call_status:{row_number}")
        if row.get("command_exit_status") not in COMMAND_EXIT_STATUSES:
            errors.append(f"command_exit_status:{row_number}")
        if row.get("task_outcome") not in TASK_OUTCOMES:
            errors.append(f"task_outcome:{row_number}")
        if row.get("terminal_state") != "emitted":
            errors.append(f"terminal_state:{row_number}")
        formula_values = row.get("formula_safe_values", {})
        if not isinstance(formula_values, dict):
            errors.append(f"formula_values:{row_number}")
        else:
            for value in formula_values.values():
                if not isinstance(value, str) or len(value) > MAX_FORMULA_VALUE_LENGTH:
                    errors.append(f"formula_value_shape:{row_number}")
                elif value.startswith(("=", "+", "-", "@")):
                    errors.append(f"formula_value_unescaped:{row_number}")

    if line_indexes != sorted(line_indexes):
        errors.append("normalized_file_order")
    if len(set(line_indexes)) != len(line_indexes):
        errors.append("normalized_line_duplicate")
    errors.extend(forbidden_values(rows))
    return errors


def _validate_exclusions(rows: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    seen_lines: set[int] = set()
    allowed = {
        "normalized_schema",
        "fixture_file",
        "source_label",
        "source_ref",
        "line_index",
        "event_index",
        "terminal_state",
        "exclusion_reason",
        "duplicate_of_line",
        "trace_state",
    }
    for row_number, row in enumerate(rows, start=1):
        if set(row) - allowed:
            errors.append(f"exclusion_unknown:{row_number}")
        if row.get("normalized_schema") != NORMALIZED_SCHEMA:
            errors.append(f"exclusion_schema:{row_number}")
        if row.get("terminal_state") not in {"malformed", "duplicate", "excluded"}:
            errors.append(f"exclusion_terminal:{row_number}")
        line_index = row.get("line_index")
        if not isinstance(line_index, int) or line_index < 1 or line_index in seen_lines:
            errors.append(f"exclusion_line:{row_number}")
        else:
            seen_lines.add(line_index)
        if not isinstance(row.get("exclusion_reason"), str) or not row["exclusion_reason"]:
            errors.append(f"exclusion_reason:{row_number}")
        if row.get("terminal_state") == "malformed":
            if row.get("source_label") is not None or row.get("source_ref") is not None:
                errors.append(f"malformed_source:{row_number}")
            if row.get("trace_state") != "line_only":
                errors.append(f"malformed_trace:{row_number}")
        else:
            if row.get("source_label") not in SOURCE_LABELS:
                errors.append(f"exclusion_source:{row_number}")
            if not is_safe_source_ref(row.get("source_ref")):
                errors.append(f"exclusion_ref:{row_number}")
            if row.get("trace_state") not in {"safe_source_ref", "safe_event_key"}:
                errors.append(f"exclusion_trace:{row_number}")
        if row.get("terminal_state") == "duplicate":
            if not isinstance(row.get("duplicate_of_line"), int):
                errors.append(f"duplicate_origin:{row_number}")
        elif "duplicate_of_line" in row:
            errors.append(f"duplicate_origin_unexpected:{row_number}")
    errors.extend(forbidden_values(rows))
    return errors


def _validate_accounting(
    accounting: dict[str, Any], normalized_count: int, exclusion_rows: list[dict[str, Any]]
) -> list[str]:
    errors: list[str] = []
    for field, expected in (("accounting_schema", ACCOUNTING_SCHEMA), ("normalized_schema", NORMALIZED_SCHEMA)):
        if accounting.get(field) != expected:
            errors.append(f"accounting_{field}")
    if not isinstance(accounting.get("fixture_file"), str):
        errors.append("accounting_fixture_file")
    counts = {}
    for field in ("input_records", "parsed_records", "malformed", "duplicate", "excluded", "emitted"):
        value = accounting.get(field)
        if not isinstance(value, int) or value < 0:
            errors.append(f"accounting_{field}")
        counts[field] = value if isinstance(value, int) else -1
    if counts["input_records"] != sum(
        counts[field] for field in ("malformed", "duplicate", "excluded", "emitted")
    ):
        errors.append("accounting_input_identity")
    if counts["parsed_records"] != sum(
        counts[field] for field in ("duplicate", "excluded", "emitted")
    ):
        errors.append("accounting_parsed_identity")
    if normalized_count != counts["emitted"]:
        errors.append("accounting_emitted_rows")
    exclusion_counts = {state: 0 for state in ("malformed", "duplicate", "excluded")}
    for row in exclusion_rows:
        state = row.get("terminal_state")
        if state in exclusion_counts:
            exclusion_counts[state] += 1
    for state in exclusion_counts:
        if exclusion_counts[state] != counts[state]:
            errors.append(f"accounting_{state}_rows")

    by_source = accounting.get("by_source")
    if not isinstance(by_source, list):
        errors.append("accounting_by_source")
        by_source = []
    source_totals = {field: 0 for field in ("input_records", "parsed_records", "malformed", "duplicate", "excluded", "emitted")}
    for index, row in enumerate(by_source):
        if not isinstance(row, dict) or row.get("source_label") not in SOURCE_LABELS:
            errors.append(f"accounting_source:{index}")
            continue
        for field in source_totals:
            value = row.get(field)
            if not isinstance(value, int) or value < 0:
                errors.append(f"accounting_source_{field}:{index}")
            else:
                source_totals[field] += value
        if row.get("input_records") != sum(
            row.get(field, -1) for field in ("malformed", "duplicate", "excluded", "emitted")
        ):
            errors.append(f"accounting_source_identity:{index}")
        if row.get("parsed_records") != sum(row.get(field, -1) for field in ("duplicate", "excluded", "emitted")):
            errors.append(f"accounting_source_parsed_identity:{index}")
    for field, value in source_totals.items():
        if value != counts[field]:
            errors.append(f"accounting_source_total_{field}")

    required_terminal = ["malformed", "duplicate", "excluded", "emitted"]
    if accounting.get("terminal_states") != required_terminal:
        errors.append("accounting_terminal_states")
    if accounting.get("status_layers") != [
        "transport_status",
        "tool_call_status",
        "command_exit_status",
        "task_outcome",
    ]:
        errors.append("accounting_status_layers")
    return errors


def validate_result(result: ParseResult) -> ValidationResult:
    errors = _validate_normalized(list(result.normalized))
    errors.extend(_validate_exclusions(list(result.exclusions)))
    errors.extend(_validate_accounting(result.accounting, len(result.normalized), list(result.exclusions)))
    # Input lines must have one terminal row.  This check is intentionally
    # based on line indices, not timestamps, so missing/inverted timestamps stay
    # observable uncertainty.
    terminal_lines = [row.get("line_index") for row in result.normalized] + [
        row.get("line_index") for row in result.exclusions
    ]
    total = result.accounting.get("input_records")
    if isinstance(total, int) and sorted(terminal_lines) != list(range(1, total + 1)):
        errors.append("terminal_partition")
    return ValidationResult(ok=not errors, errors=tuple(errors))


def validate_output_files(
    normalized_path: str | Path,
    exclusions_path: str | Path,
    accounting_path: str | Path,
) -> ValidationResult:
    normalized, normalized_errors = _load_jsonl(Path(normalized_path))
    exclusions, exclusion_errors = _load_jsonl(Path(exclusions_path))
    accounting, accounting_errors = _load_json(Path(accounting_path))
    errors = normalized_errors + exclusion_errors + accounting_errors
    errors.extend(_validate_normalized(normalized))
    errors.extend(_validate_exclusions(exclusions))
    if accounting is None:
        errors.append("accounting_missing")
    else:
        errors.extend(_validate_accounting(accounting, len(normalized), exclusions))
        total = accounting.get("input_records")
        terminal_lines = [row.get("line_index") for row in normalized + exclusions]
        if isinstance(total, int) and sorted(terminal_lines) != list(range(1, total + 1)):
            errors.append("terminal_partition")
    return ValidationResult(ok=not errors, errors=tuple(errors))


__all__ = [
    "ValidationResult",
    "forbidden_values",
    "validate_output_files",
    "validate_result",
]
