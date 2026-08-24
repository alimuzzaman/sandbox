"""Deterministic JSONL parser for the synthetic audit fixture lane.

The parser is intentionally a file-to-file boundary.  It performs no network,
browser, database, subprocess, remote, registry, or Sandbox-runtime access.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .atomic import atomic_write_texts
from .redactor import (
    FORMULA_CANDIDATE_MARKER,
    FORMULA_MARKER,
    REDACTED,
    is_formula_candidate,
    redact_nested,
    target_class,
)
from .schema import (
    ACCOUNTING_SCHEMA,
    COMMAND_EXIT_STATUSES,
    FIXTURE_SCHEMA,
    LABEL_KEYS,
    MAX_ARGUMENT_KEYS,
    NORMALIZED_SCHEMA,
    RELATION_SIGNALS,
    SUPPORTED_RECORD_KINDS,
    TASK_OUTCOMES,
    TOOL_CALL_STATUSES,
    TRANSPORT_STATUSES,
    bounded_text,
    safe_event_key,
    safe_file_name,
    safe_name,
    safe_source_label,
    safe_source_ref,
)


@dataclass(frozen=True)
class ParseResult:
    """In-memory output of one explicit JSONL input file."""

    normalized: tuple[dict[str, Any], ...]
    exclusions: tuple[dict[str, Any], ...]
    accounting: dict[str, Any]


def _json_line(value: Any) -> str:
    # Compact, insertion-order JSON is stable across clean runs while keeping
    # the schema readable when a line is inspected in a review.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            # Fixture timestamps are required to carry a timezone.  Treat an
            # offset-less value as invalid rather than inventing one.
            return None
        return parsed
    except (TypeError, ValueError):
        return None


def _normalise_status(value: Any, allowed: frozenset[str]) -> str:
    if isinstance(value, str) and value in allowed:
        return value
    return "unknown"


def _safe_event_index(record: dict[str, Any]) -> int | None:
    value = record.get("event_index")
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _normalise_tool_name(namespace: Any, name: Any) -> tuple[str, str]:
    raw_namespace = bounded_text(namespace, limit=64)
    raw_name = bounded_text(name, limit=64)
    if raw_namespace is None and raw_name and "." in raw_name:
        prefix, suffix = raw_name.split(".", 1)
        raw_namespace = raw_namespace or prefix
        raw_name = suffix
    return safe_name(raw_namespace), safe_name(raw_name)


def _find_tool(record: dict[str, Any]) -> tuple[str, str, str, dict[str, Any], Any] | None:
    """Return event type, namespace, name, arguments, and dedup key."""

    kind = record.get("record_kind")
    if kind == "tool_event":
        event = record.get("event")
        if not isinstance(event, dict):
            return None
        arguments = event.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        namespace, name = _normalise_tool_name(event.get("namespace"), event.get("name"))
        return "tool_call", namespace, name, arguments, record.get("event_key")

    blocks = record.get("content_blocks")
    if not isinstance(blocks, list):
        return None
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        arguments = block.get("input")
        if not isinstance(arguments, dict):
            arguments = {}
        namespace, name = _normalise_tool_name(None, block.get("name"))
        return (
            "content_block_tool_call",
            namespace,
            name,
            arguments,
            block.get("id") or record.get("event_key"),
        )
    return None


def _project_arguments(arguments: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Project only bounded, semantically useful argument markers."""

    signature: OrderedDict[str, Any] = OrderedDict()
    label_states: OrderedDict[str, str] = OrderedDict()

    explicit_target = arguments.get("target_class")
    if explicit_target is not None:
        classified = target_class(explicit_target)
    else:
        classified = target_class(arguments.get("target"))
    if classified != "unknown":
        signature["target_class"] = classified

    format_value = bounded_text(arguments.get("format"), limit=32)
    if format_value is not None and format_value in {"json", "text", "binary"}:
        signature["format"] = format_value

    if "cursor" in arguments:
        signature["cursor"] = "present"

    if "headers" in arguments:
        # Headers are sensitive-looking by definition, even if a fixture uses
        # a placeholder.  The nested value never crosses this boundary.
        signature["headers"] = REDACTED

    if "metadata" in arguments:
        # Metadata may contain a token, cookie, or session object at arbitrary
        # depth.  Keep only the bounded redaction marker in this schema.
        _ = redact_nested(arguments.get("metadata"))
        signature["metadata"] = REDACTED

    if "label" in arguments:
        label_value = arguments.get("label")
        if is_formula_candidate(label_value):
            signature["label_state"] = FORMULA_CANDIDATE_MARKER
        elif bounded_text(label_value) is not None:
            signature["label_state"] = "present"

    labels = arguments.get("labels")
    if isinstance(labels, dict):
        # Only the closed synthetic label vocabulary is retained.  Values are
        # reduced to an allowlisted state, so prose, email-like text, and
        # formula bodies never cross the projection boundary.
        for raw_key, raw_value in labels.items():
            key = bounded_text(raw_key, limit=32)
            if key not in LABEL_KEYS or len(label_states) >= MAX_ARGUMENT_KEYS:
                continue
            if is_formula_candidate(raw_value):
                label_states[key] = FORMULA_CANDIDATE_MARKER
            elif bounded_text(raw_value) is not None:
                label_states[key] = "present"
        if label_states:
            signature["labels"] = FORMULA_MARKER

    # The signature is deliberately a small allowlist.  Unknown arguments are
    # ignored rather than copied, preserving schema evolution and privacy.
    return dict(signature), dict(label_states)


def _project_relation(record: dict[str, Any]) -> tuple[str, str | None, str]:
    relation = record.get("relation_signal")
    if not isinstance(relation, dict):
        return "none", None, "unknown"
    signal = relation.get("role")
    if signal not in RELATION_SIGNALS or signal == "none":
        return "none", None, "unknown"
    join_status = relation.get("join_status")
    if join_status not in {"unverified", "unknown"}:
        join_status = "unknown"
    return signal, join_status, "unknown"


def _normalised_row(
    *,
    record: dict[str, Any],
    fixture_file: str,
    line_index: int,
    event_type: str,
    namespace: str,
    name: str,
    arguments: dict[str, Any],
    source_label: str,
    source_ref: str,
    timestamp_before: datetime | None,
) -> tuple[dict[str, Any], datetime | None]:
    timestamp_raw = record.get("timestamp")
    timestamp = None
    timestamp_state = "missing"
    parsed_timestamp = _parse_timestamp(timestamp_raw)
    if isinstance(timestamp_raw, str) and timestamp_raw.strip():
        if parsed_timestamp is None:
            timestamp_state = "invalid"
        else:
            timestamp = bounded_text(timestamp_raw, limit=64)
            timestamp_state = (
                "inversion"
                if timestamp_before is not None and parsed_timestamp < timestamp_before
                else "present"
            )

    relation_signal, relation_join, relation_state = _project_relation(record)
    status = record.get("status")
    if not isinstance(status, dict):
        status = {}
    argument_signature, formula_values = _project_arguments(arguments)

    row: OrderedDict[str, Any] = OrderedDict(
        (
            ("normalized_schema", NORMALIZED_SCHEMA),
            ("fixture_file", fixture_file),
            ("source_label", source_label),
            ("source_ref", source_ref),
            ("line_index", line_index),
            ("event_index", _safe_event_index(record)),
            ("event_type", event_type),
            ("tool_namespace", namespace),
            ("tool_name", name),
            ("argument_signature", argument_signature),
        )
    )
    if formula_values:
        row["formula_safe_values"] = formula_values
    row.update(
        (
            ("timestamp", timestamp),
            ("timestamp_state", timestamp_state),
            ("ordering_basis", "file_order"),
            ("relation_signal", relation_signal),
            ("relation_state", relation_state),
        )
    )
    if relation_join is not None:
        # Keep the unverified join class, never the candidate's raw parent ref.
        row["relation_join"] = relation_join
        # Expected fixture rows place relation_join before relation_state.  A
        # dict's semantic equality does not depend on order, but use a rebuild
        # so byte output remains pleasant for reviewers.
        relation_state_value = row.pop("relation_state")
        row["relation_join"] = relation_join
        row["relation_state"] = relation_state_value
    row.update(
        (
            ("transport_status", _normalise_status(status.get("transport"), TRANSPORT_STATUSES)),
            ("tool_call_status", _normalise_status(status.get("tool_call"), TOOL_CALL_STATUSES)),
            ("command_exit_status", _normalise_status(status.get("command_exit"), COMMAND_EXIT_STATUSES)),
            ("task_outcome", _normalise_status(status.get("task"), TASK_OUTCOMES)),
            ("terminal_state", "emitted"),
        )
    )
    return dict(row), parsed_timestamp or timestamp_before


def _new_exclusion(
    *,
    fixture_file: str,
    line_index: int,
    source_label: str | None,
    source_ref: str | None,
    event_index: int | None,
    reason: str,
    terminal_state: str,
    trace_state: str,
    duplicate_of_line: int | None = None,
) -> dict[str, Any]:
    row: OrderedDict[str, Any] = OrderedDict(
        (
            ("normalized_schema", NORMALIZED_SCHEMA),
            ("fixture_file", fixture_file),
            ("source_label", source_label),
            ("source_ref", source_ref),
            ("line_index", line_index),
            ("event_index", event_index),
            ("terminal_state", terminal_state),
            ("exclusion_reason", reason),
        )
    )
    if duplicate_of_line is not None:
        row["duplicate_of_line"] = duplicate_of_line
    row["trace_state"] = trace_state
    return dict(row)


def _empty_source_counts() -> OrderedDict[str, int]:
    return OrderedDict(
        (
            ("input_records", 0),
            ("parsed_records", 0),
            ("malformed", 0),
            ("duplicate", 0),
            ("excluded", 0),
            ("emitted", 0),
        )
    )


def _accounting(
    *, fixture_file: str, total: int, counts: OrderedDict[str, OrderedDict[str, int]]
) -> dict[str, Any]:
    malformed = sum(bucket["malformed"] for bucket in counts.values())
    duplicate = sum(bucket["duplicate"] for bucket in counts.values())
    excluded = sum(bucket["excluded"] for bucket in counts.values())
    emitted = sum(bucket["emitted"] for bucket in counts.values())
    parsed = duplicate + excluded + emitted
    by_source = []
    for label, bucket in counts.items():
        row: OrderedDict[str, Any] = OrderedDict(
            (
                ("source_label", label),
                ("input_records", bucket["input_records"]),
                ("parsed_records", bucket["parsed_records"]),
                ("malformed", bucket["malformed"]),
                ("duplicate", bucket["duplicate"]),
                ("excluded", bucket["excluded"]),
                ("emitted", bucket["emitted"]),
                (
                    "identity",
                    f"{bucket['input_records']} = {bucket['malformed']} + "
                    f"{bucket['duplicate']} + {bucket['excluded']} + {bucket['emitted']}",
                ),
            )
        )
        if label == "unknown" and bucket["malformed"]:
            row["reason"] = "line-only malformed trace has no trusted source label"
        by_source.append(dict(row))

    return {
        "accounting_schema": ACCOUNTING_SCHEMA,
        "fixture_schema": FIXTURE_SCHEMA,
        "normalized_schema": NORMALIZED_SCHEMA,
        "fixture_file": fixture_file,
        "input_records": total,
        "parsed_records": parsed,
        "malformed": malformed,
        "duplicate": duplicate,
        "excluded": excluded,
        "emitted": emitted,
        "identities": {
            "input": f"{total} = {malformed} + {duplicate} + {excluded} + {emitted}",
            "parsed": f"{parsed} = {duplicate} + {excluded} + {emitted}",
        },
        "by_source": by_source,
        "terminal_states": ["malformed", "duplicate", "excluded", "emitted"],
        "status_layers": [
            "transport_status",
            "tool_call_status",
            "command_exit_status",
            "task_outcome",
        ],
        "ordering": {
            "preserve_file_order": True,
            "global_timestamp_sort": False,
            "missing_timestamp_is_unknown": True,
            "timestamp_inversion_is_unknown": True,
        },
        "relations": {
            "missing_join_ids_are_non_joinable": True,
            "unverified_parent_child_signals_remain_unknown": True,
        },
    }


def parse_jsonl(input_path: str | Path) -> ParseResult:
    """Parse one explicit JSONL file into normalized and exclusion rows."""

    path = Path(input_path)
    if not path.is_file():
        raise FileNotFoundError(path)
    fixture_file = safe_file_name(path.name)
    normalized: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    source_counts: OrderedDict[str, OrderedDict[str, int]] = OrderedDict()
    seen_event_keys: dict[tuple[str, str], int] = {}
    previous_timestamp: datetime | None = None
    total_lines = 0

    def bucket_for(label: str) -> OrderedDict[str, int]:
        if label not in source_counts:
            source_counts[label] = _empty_source_counts()
        return source_counts[label]

    with path.open("r", encoding="utf-8", newline="") as stream:
        for line_index, raw_line in enumerate(stream, start=1):
            total_lines = line_index
            bucket: OrderedDict[str, int] | None = None
            try:
                if not raw_line.strip():
                    raise ValueError("empty_jsonl_line")
                record = json.loads(raw_line)
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError):
                bucket = bucket_for("unknown")
                bucket["input_records"] += 1
                bucket["malformed"] += 1
                exclusions.append(
                    _new_exclusion(
                        fixture_file=fixture_file,
                        line_index=line_index,
                        source_label=None,
                        source_ref=None,
                        event_index=None,
                        reason="invalid_jsonl",
                        terminal_state="malformed",
                        trace_state="line_only",
                    )
                )
                continue

            if not isinstance(record, dict):
                bucket = bucket_for("unknown")
                bucket["input_records"] += 1
                bucket["malformed"] += 1
                exclusions.append(
                    _new_exclusion(
                        fixture_file=fixture_file,
                        line_index=line_index,
                        source_label=None,
                        source_ref=None,
                        event_index=None,
                        reason="invalid_record_shape",
                        terminal_state="malformed",
                        trace_state="line_only",
                    )
                )
                continue

            source_label = safe_source_label(record.get("source_label"))
            source_ref = safe_source_ref(record.get("source_ref"))
            bucket = bucket_for(source_label)
            bucket["input_records"] += 1

            if record.get("fixture_schema") != FIXTURE_SCHEMA:
                bucket["parsed_records"] += 1
                bucket["excluded"] += 1
                exclusions.append(
                    _new_exclusion(
                        fixture_file=fixture_file,
                        line_index=line_index,
                        source_label=source_label,
                        source_ref=source_ref,
                        event_index=_safe_event_index(record),
                        reason="unsupported_schema",
                        terminal_state="excluded",
                        trace_state="safe_source_ref",
                    )
                )
                continue

            record_kind = record.get("record_kind")
            if record_kind not in SUPPORTED_RECORD_KINDS:
                bucket["parsed_records"] += 1
                bucket["excluded"] += 1
                exclusions.append(
                    _new_exclusion(
                        fixture_file=fixture_file,
                        line_index=line_index,
                        source_label=source_label,
                        source_ref=source_ref,
                        event_index=_safe_event_index(record),
                        reason="unsupported_record_kind",
                        terminal_state="excluded",
                        trace_state="safe_source_ref",
                    )
                )
                continue

            explicit_event_index = _safe_event_index(record)
            if explicit_event_index is None:
                bucket["parsed_records"] += 1
                bucket["excluded"] += 1
                exclusions.append(
                    _new_exclusion(
                        fixture_file=fixture_file,
                        line_index=line_index,
                        source_label=source_label,
                        source_ref=source_ref,
                        event_index=None,
                        reason="missing_event_index",
                        terminal_state="excluded",
                        trace_state="safe_source_ref",
                    )
                )
                continue

            tool = _find_tool(record)
            if tool is None:
                bucket["parsed_records"] += 1
                bucket["excluded"] += 1
                exclusions.append(
                    _new_exclusion(
                        fixture_file=fixture_file,
                        line_index=line_index,
                        source_label=source_label,
                        source_ref=source_ref,
                        event_index=_safe_event_index(record),
                        reason="invalid_tool_event",
                        terminal_state="excluded",
                        trace_state="safe_source_ref",
                    )
                )
                continue

            event_type, namespace, name, arguments, raw_event_key = tool
            event_key = safe_event_key(raw_event_key)
            dedup_key = (source_label, source_ref, event_key or f"line:{line_index}")
            if event_key is not None and dedup_key in seen_event_keys:
                bucket["parsed_records"] += 1
                bucket["duplicate"] += 1
                exclusions.append(
                    _new_exclusion(
                        fixture_file=fixture_file,
                        line_index=line_index,
                        source_label=source_label,
                        source_ref=source_ref,
                        event_index=_safe_event_index(record),
                        reason=(
                            "duplicate_rollover_event"
                            if record.get("segment") == "rollover"
                            else "duplicate_event"
                        ),
                        terminal_state="duplicate",
                        trace_state="safe_event_key",
                        duplicate_of_line=seen_event_keys[dedup_key],
                    )
                )
                continue
            if event_key is not None:
                seen_event_keys[dedup_key] = line_index

            bucket["parsed_records"] += 1
            bucket["emitted"] += 1
            row, parsed_timestamp = _normalised_row(
                record=record,
                fixture_file=fixture_file,
                line_index=line_index,
                event_type=event_type,
                namespace=namespace,
                name=name,
                arguments=arguments,
                source_label=source_label,
                source_ref=source_ref,
                timestamp_before=previous_timestamp,
            )
            normalized.append(row)
            if parsed_timestamp is not None:
                previous_timestamp = parsed_timestamp

    return ParseResult(
        normalized=tuple(normalized),
        exclusions=tuple(exclusions),
        accounting=_accounting(
            fixture_file=fixture_file,
            total=total_lines,
            counts=source_counts,
        ),
    )


def write_result(result: ParseResult, output_dir: str | Path) -> tuple[Path, Path, Path]:
    """Validate then atomically write canonical JSONL/JSON output."""

    # Keep validation ahead of directory creation and every output write.  The
    # local import avoids the parser/validator type-import cycle.
    from .validator import validate_result

    validate_result(result).require_ok()

    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    normalized_path = directory / "normalized.jsonl"
    exclusions_path = directory / "exclusions.jsonl"
    accounting_path = directory / "accounting.json"
    atomic_write_texts(
        {
            normalized_path: "".join(_json_line(row) + "\n" for row in result.normalized),
            exclusions_path: "".join(_json_line(row) + "\n" for row in result.exclusions),
            accounting_path: json.dumps(result.accounting, ensure_ascii=False, indent=2) + "\n",
        }
    )
    return normalized_path, exclusions_path, accounting_path


def parse_to_directory(input_path: str | Path, output_dir: str | Path) -> ParseResult:
    result = parse_jsonl(input_path)
    write_result(result, output_dir)
    return result


__all__ = ["ParseResult", "parse_jsonl", "parse_to_directory", "write_result"]
