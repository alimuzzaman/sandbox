"""Bounded, inert parsing for registered secret assignment sources.

This module deliberately implements a small data grammar.  It never sources a
file, expands a value, imports a dotenv implementation, or includes offending
input in an exception.  Raw records are retained internally so a later writer
can replace exactly one assignment without reconstructing unrelated content.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import shlex
from types import MappingProxyType
from typing import Literal, Mapping
import unicodedata


DEFAULT_MAX_BYTES = 1_048_576
DEFAULT_MAX_ENTRIES = 4_096
DEFAULT_MAX_VALUE_BYTES = 65_536
MAX_KEY_LENGTH = 128

_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_ASSIGNMENT = re.compile(
    r"^(?P<leading>[ \t]*)"
    r"(?:(?P<export>export)(?P<export_space>[ \t]+))?"
    r"(?P<key>[A-Za-z_][A-Za-z0-9_]*)"
    r"=(?P<encoded>.*)$"
)


class SecretParseError(ValueError):
    """A parse refusal containing only a stable, non-secret reason code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, repr=False)
class CommentRecord:
    raw: bytes
    text: str
    ending: bytes
    kind: Literal["comment"] = "comment"

    def __repr__(self) -> str:
        return "CommentRecord()"


@dataclass(frozen=True, repr=False)
class BlankRecord:
    raw: bytes
    text: str
    ending: bytes
    kind: Literal["blank"] = "blank"

    def __repr__(self) -> str:
        return "BlankRecord()"


@dataclass(frozen=True, repr=False)
class AssignmentRecord:
    raw: bytes
    text: str
    ending: bytes
    key: str
    value: str
    exported: bool
    assignment_prefix: str
    kind: Literal["assignment"] = "assignment"

    def __repr__(self) -> str:
        return f"AssignmentRecord(key={self.key!r}, value=<redacted>)"


Record = CommentRecord | BlankRecord | AssignmentRecord


@dataclass(frozen=True, repr=False)
class ParsedDocument:
    raw_bytes: bytes
    newline_style: Literal["lf", "crlf", "mixed"]
    records: tuple[Record, ...]
    entries: Mapping[str, AssignmentRecord]

    def render(self) -> bytes:
        """Return the original internal bytes without normalizing syntax."""
        return b"".join(record.raw for record in self.records)

    def __repr__(self) -> str:
        return (
            "ParsedDocument("
            f"newline_style={self.newline_style!r}, "
            f"record_count={len(self.records)}, entry_count={len(self.entries)})"
        )


def _refuse(code: str) -> None:
    raise SecretParseError(code)


def _validate_key(key: str) -> None:
    if len(key) > MAX_KEY_LENGTH:
        _refuse("invalid_key")
    if not _KEY.fullmatch(key):
        _refuse("invalid_key")


def _validate_controls(text: str, *, line_structure: bool) -> None:
    for character in text:
        if line_structure and character in "\n\r\t":
            continue
        category = unicodedata.category(character)
        if category in {"Cc", "Cf"}:
            _refuse("control_character")


def _utf8_size(text: str) -> int:
    encoded: bytes | None
    try:
        encoded = text.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        # Do not retain an exception whose object field contains candidate data.
        encoded = None
    if encoded is None:
        _refuse("invalid_encoding")
    return len(encoded)


def _decode_literal(encoded: str) -> str:
    """Decode one shell-shaped word while refusing every active expansion."""
    encoded = encoded.strip(" \t")
    if not encoded:
        return ""

    output: list[str] = []
    state: Literal["plain", "single", "double"] = "plain"
    index = 0
    while index < len(encoded):
        character = encoded[index]

        if state == "single":
            if character == "'":
                state = "plain"
            else:
                output.append(character)
            index += 1
            continue

        if state == "double":
            if character == '"':
                state = "plain"
                index += 1
                continue
            if character == "\\":
                index += 1
                if index >= len(encoded):
                    _refuse("invalid_quoting")
                output.append(encoded[index])
                index += 1
                continue
            if character in {"$", "`"}:
                _refuse("expansion_not_allowed")
            output.append(character)
            index += 1
            continue

        if character == "'":
            state = "single"
            index += 1
            continue
        if character == '"':
            state = "double"
            index += 1
            continue
        if character == "\\":
            index += 1
            if index >= len(encoded):
                _refuse("invalid_quoting")
            output.append(encoded[index])
            index += 1
            continue
        if character in {"$", "`"}:
            _refuse("expansion_not_allowed")
        if character == "~" and not output:
            _refuse("expansion_not_allowed")
        if character in "<>" and index + 1 < len(encoded) and encoded[index + 1] == "(":
            _refuse("expansion_not_allowed")
        if character.isspace():
            _refuse("unsupported_syntax")
        output.append(character)
        index += 1

    if state != "plain":
        _refuse("invalid_quoting")
    value = "".join(output)
    _validate_controls(value, line_structure=False)
    return value


def _split_raw_lines(content: bytes) -> list[tuple[bytes, bytes, bytes]]:
    lines: list[tuple[bytes, bytes, bytes]] = []
    for raw in content.splitlines(keepends=True):
        if raw.endswith(b"\r\n"):
            body, ending = raw[:-2], b"\r\n"
        elif raw.endswith(b"\n"):
            body, ending = raw[:-1], b"\n"
        else:
            body, ending = raw, b""
        if b"\r" in body:
            _refuse("control_character")
        lines.append((raw, body, ending))
    return lines


def parse_document(
    content: bytes,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    max_value_bytes: int = DEFAULT_MAX_VALUE_BYTES,
) -> ParsedDocument:
    """Parse bounded UTF-8 bytes as comments, blanks, and literal assignments."""
    if not isinstance(content, bytes):
        raise TypeError("content must be bytes")
    if len(content) > max_bytes:
        _refuse("source_too_large")
    decoded: str | None
    try:
        decoded = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        # Raise outside the handler so the retained exception context cannot
        # carry a representation of the secret-bearing byte buffer.
        decoded = None
    if decoded is None:
        _refuse("invalid_encoding")
    _validate_controls(decoded, line_structure=True)

    records: list[Record] = []
    entries: dict[str, AssignmentRecord] = {}
    endings: set[bytes] = set()

    for raw, body, ending in _split_raw_lines(content):
        if ending:
            endings.add(ending)
        text = body.decode("utf-8")
        stripped = text.strip(" \t")
        if not stripped:
            records.append(BlankRecord(raw=raw, text=text, ending=ending))
            continue
        if text.lstrip(" \t").startswith("#"):
            records.append(CommentRecord(raw=raw, text=text, ending=ending))
            continue

        match = _ASSIGNMENT.fullmatch(text)
        if not match:
            _refuse("unsupported_syntax")
        key = match.group("key")
        _validate_key(key)
        if key in entries:
            _refuse("duplicate_key")
        if len(entries) >= max_entries:
            _refuse("too_many_entries")

        value = _decode_literal(match.group("encoded"))
        if _utf8_size(value) > max_value_bytes:
            _refuse("value_too_large")
        record = AssignmentRecord(
            raw=raw,
            text=text,
            ending=ending,
            key=key,
            value=value,
            exported=match.group("export") is not None,
            assignment_prefix=text[: match.start("encoded")],
        )
        records.append(record)
        entries[key] = record

    if endings == {b"\r\n"}:
        newline_style: Literal["lf", "crlf", "mixed"] = "crlf"
    elif len(endings) > 1:
        newline_style = "mixed"
    else:
        newline_style = "lf"

    return ParsedDocument(
        raw_bytes=content,
        newline_style=newline_style,
        records=tuple(records),
        entries=MappingProxyType(entries),
    )


def render_assignment(
    key: str,
    value: str,
    *,
    exported: bool = False,
    max_value_bytes: int = DEFAULT_MAX_VALUE_BYTES,
) -> str:
    """Render one safe, single-line assignment without a line ending."""
    _validate_key(key)
    if not isinstance(value, str):
        raise TypeError("value must be str")
    if not value:
        _refuse("empty_value")
    if "\n" in value or "\r" in value:
        _refuse("multiline_value")
    _validate_controls(value, line_structure=False)
    if _utf8_size(value) > max_value_bytes:
        _refuse("value_too_large")
    prefix = "export " if exported else ""
    return f"{prefix}{key}={shlex.quote(value)}"


def replace_assignment(
    document: ParsedDocument,
    key: str,
    value: str,
    *,
    max_value_bytes: int = DEFAULT_MAX_VALUE_BYTES,
) -> bytes:
    """Render one replacement while preserving every unrelated raw record."""
    _validate_key(key)
    if document.newline_style == "mixed":
        _refuse("mixed_newlines")
    target = document.entries.get(key)
    if target is None:
        _refuse("missing_key")

    rendered = render_assignment(
        key,
        value,
        exported=target.exported,
        max_value_bytes=max_value_bytes,
    )
    # Preserve leading indentation and the original export spacing.  The value
    # itself is normalized to a safely quoted literal.
    rendered = target.assignment_prefix + rendered.split("=", 1)[1]
    replacement = rendered.encode("utf-8") + target.ending
    return b"".join(replacement if record is target else record.raw for record in document.records)


__all__ = [
    "AssignmentRecord",
    "BlankRecord",
    "CommentRecord",
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_ENTRIES",
    "DEFAULT_MAX_VALUE_BYTES",
    "MAX_KEY_LENGTH",
    "ParsedDocument",
    "SecretParseError",
    "parse_document",
    "render_assignment",
    "replace_assignment",
]
