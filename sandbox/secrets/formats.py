"""Bounded inert parsers for explicitly registered secret-file formats.

The parsers return selectors and internal scalar values only.  Their public
representations never include values, source text, parser diagnostics, or
paths.  Formats are explicit configuration, never inferred from secret bytes.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import re
import tomllib
from types import MappingProxyType
from typing import Any, Mapping
import unicodedata
import xml.etree.ElementTree as ElementTree

from .models import MAX_ENTRIES, MAX_SOURCE_BYTES, MAX_VALUE_BYTES


SUPPORTED_FORMATS = frozenset({
    "binary", "dotenv", "ini", "json", "opaque", "pem", "properties",
    "toml", "xml", "yaml",
})
MAX_DEPTH = 32
MAX_SELECTOR_LENGTH = 512
_PEM_BLOCK = re.compile(
    rb"-----BEGIN (?P<label>[A-Z0-9][A-Z0-9 .#_-]{0,63})-----\r?\n"
    rb"(?P<body>[A-Za-z0-9+/=\r\n]+?)"
    rb"-----END (?P=label)-----",
)
_SECTION = re.compile(r"^\[(?P<section>[^\[\]\r\n]{1,256})\]$")


class SecretFormatError(ValueError):
    """Stable non-secret parse refusal."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, repr=False)
class SecretEntry:
    key: str
    value: str | None
    kind_hint: str | None = None
    byte_length: int | None = None
    allow_mask: bool = False
    allow_exact_length: bool = False

    def __repr__(self) -> str:
        return f"SecretEntry(key={self.key!r}, value=<redacted>)"


@dataclass(frozen=True, repr=False)
class SecretDocument:
    format: str
    entries: Mapping[str, SecretEntry]

    def __repr__(self) -> str:
        return f"SecretDocument(format={self.format!r}, entry_count={len(self.entries)})"


def _refuse(code: str) -> None:
    raise SecretFormatError(code)


def _decode(content: bytes) -> str:
    decoded: str | None
    try:
        decoded = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        decoded = None
    if decoded is None:
        _refuse("invalid_encoding")
    return decoded


def _safe_text(value: str, *, multiline: bool = True) -> str:
    if len(value.encode("utf-8")) > MAX_VALUE_BYTES:
        _refuse("value_too_large")
    for character in value:
        if multiline and character in "\t\n\r":
            continue
        if unicodedata.category(character) in {"Cc", "Cf"}:
            _refuse("control_character")
    return value


def _pointer_segment(value: object) -> str:
    text = str(value)
    _safe_text(text, multiline=False)
    if not text or any(not character.isascii() or not character.isprintable() for character in text):
        _refuse("selector_unsupported")
    return text.replace("~", "~0").replace("/", "~1")


def _selector(parts: tuple[object, ...]) -> str:
    result = "/" + "/".join(_pointer_segment(part) for part in parts)
    if len(result) > MAX_SELECTOR_LENGTH:
        _refuse("selector_too_long")
    return result


def validate_selector(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or len(value) > MAX_SELECTOR_LENGTH
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        _refuse("invalid_selector")
    index = 0
    while index < len(value):
        if value[index] == "~":
            if index + 1 >= len(value) or value[index + 1] not in "01":
                _refuse("invalid_selector")
            index += 2
            continue
        index += 1
    return value


def _scalar_text(value: object) -> tuple[str | None, str | None]:
    if value is None:
        return "null", "null"
    if isinstance(value, bool):
        return ("true" if value else "false"), "boolean"
    if isinstance(value, str):
        return _safe_text(value), None
    if isinstance(value, (int, float)):
        return json.dumps(value, allow_nan=False), "number"
    if hasattr(value, "isoformat"):
        return str(value.isoformat()), "timestamp"
    return None, None


def _flatten(value: object, *, format_name: str) -> SecretDocument:
    entries: dict[str, SecretEntry] = {}

    def visit(item: object, parts: tuple[object, ...], depth: int) -> None:
        if depth > MAX_DEPTH:
            _refuse("structure_too_deep")
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    _refuse("key_unsupported")
                visit(child, (*parts, key), depth + 1)
            return
        if isinstance(item, (list, tuple)):
            for index, child in enumerate(item):
                visit(child, (*parts, index), depth + 1)
            return
        text, hint = _scalar_text(item)
        if text is None:
            _refuse("value_unsupported")
        if not parts:
            parts = ("value",)
        key = _selector(parts)
        if key in entries:
            _refuse("duplicate_key")
        if len(entries) >= MAX_ENTRIES:
            _refuse("too_many_entries")
        entries[key] = SecretEntry(
            key=key, value=text, kind_hint=hint,
            byte_length=len(text.encode("utf-8")),
        )

    visit(value, (), 0)
    return SecretDocument(format_name, MappingProxyType(entries))


def _json(content: bytes) -> SecretDocument:
    class DuplicateKey(Exception):
        pass

    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise DuplicateKey
            result[key] = value
        return result

    def reject_constant(_value):
        raise ValueError

    value = json.loads(
        _decode(content), object_pairs_hook=object_pairs,
        parse_constant=reject_constant,
    )
    return _flatten(value, format_name="json")


def _toml(content: bytes) -> SecretDocument:
    return _flatten(tomllib.loads(_decode(content)), format_name="toml")


def _yaml(content: bytes) -> SecretDocument:
    try:
        import yaml
    except ImportError:
        _refuse("dependency_unavailable")

    decoded = _decode(content)
    for token in yaml.scan(decoded):
        if isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken, yaml.tokens.TagToken)):
            _refuse("active_syntax_denied")

    class StrictSafeLoader(yaml.SafeLoader):
        pass

    def construct_mapping(loader, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                _refuse("duplicate_key")
            result[key] = loader.construct_object(value_node, deep=deep)
        return result

    StrictSafeLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_mapping,
    )
    return _flatten(yaml.load(decoded, Loader=StrictSafeLoader), format_name="yaml")


def _ini(content: bytes) -> SecretDocument:
    decoded = _decode(content)
    sections: dict[str, dict[str, str]] = {}
    current: str | None = None
    previous: str | None = None
    for line in decoded.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";")):
            continue
        section_match = _SECTION.fullmatch(stripped)
        if section_match:
            current = _safe_text(section_match.group("section").strip(), multiline=False)
            if current in sections:
                _refuse("duplicate_section")
            sections[current] = {}
            previous = None
            continue
        if current is None:
            _refuse("section_required")
        if line[:1].isspace() and previous is not None and "=" not in stripped and ":" not in stripped:
            sections[current][previous] += "\n" + _safe_text(stripped)
            continue
        delimiters = [position for position in (line.find("="), line.find(":")) if position >= 0]
        if not delimiters:
            _refuse("unsupported_syntax")
        position = min(delimiters)
        key = _safe_text(line[:position].strip(), multiline=False)
        if not key or key in sections[current]:
            _refuse("duplicate_key")
        sections[current][key] = _safe_text(line[position + 1:].strip())
        previous = key
    return _flatten(sections, format_name="ini")


def _properties(content: bytes) -> SecretDocument:
    decoded = _decode(content)
    result: dict[str, str] = {}
    for line in decoded.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";")):
            continue
        if stripped.endswith("\\"):
            _refuse("continuation_unsupported")
        position = line.find("=")
        if position < 0:
            position = line.find(":")
        if position < 0:
            _refuse("unsupported_syntax")
        key = _safe_text(line[:position].strip(), multiline=False)
        if not key or key in result:
            _refuse("duplicate_key")
        result[key] = _safe_text(line[position + 1:].strip())
    return _flatten(result, format_name="properties")


def _xml_name(tag: str) -> str:
    if tag.startswith("{") and "}" in tag:
        tag = tag.split("}", 1)[1]
    return _pointer_segment(tag)


def _xml(content: bytes) -> SecretDocument:
    lowered = content.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        _refuse("active_syntax_denied")
    root = ElementTree.fromstring(content)
    entries: dict[str, SecretEntry] = {}

    def add(parts: tuple[object, ...], value: str) -> None:
        value = _safe_text(value)
        key = _selector(parts)
        if key in entries:
            _refuse("duplicate_key")
        if len(entries) >= MAX_ENTRIES:
            _refuse("too_many_entries")
        entries[key] = SecretEntry(key=key, value=value, byte_length=len(value.encode()))

    def visit(element, parts: tuple[object, ...], depth: int) -> None:
        if depth > MAX_DEPTH:
            _refuse("structure_too_deep")
        for name, value in element.attrib.items():
            add((*parts, "@" + _xml_name(name)), value)
        if element.text and element.text.strip():
            add((*parts, "#text"), element.text.strip())
        children = list(element)
        totals: dict[str, int] = {}
        for child in children:
            name = _xml_name(child.tag)
            totals[name] = totals.get(name, 0) + 1
        seen: dict[str, int] = {}
        for child in children:
            name = _xml_name(child.tag)
            child_parts = (*parts, name)
            if totals[name] > 1:
                index = seen.get(name, 0)
                seen[name] = index + 1
                child_parts = (*child_parts, index)
            visit(child, child_parts, depth + 1)

    visit(root, (_xml_name(root.tag),), 0)
    return SecretDocument("xml", MappingProxyType(entries))


def _pem(content: bytes) -> SecretDocument:
    entries: dict[str, SecretEntry] = {}
    cursor = 0
    for index, match in enumerate(_PEM_BLOCK.finditer(content)):
        if content[cursor:match.start()].strip():
            _refuse("unsupported_syntax")
        body = re.sub(rb"\s+", b"", match.group("body"))
        try:
            base64.b64decode(body, validate=True)
        except ValueError:
            _refuse("invalid_pem")
        label = match.group("label").decode("ascii").lower().replace(" ", "-")
        key = _selector(("blocks", index, label))
        value = match.group(0).decode("ascii")
        entries[key] = SecretEntry(
            key=key, value=value, kind_hint="key_material",
            byte_length=len(match.group(0)),
        )
        cursor = match.end()
    if not entries or content[cursor:].strip():
        _refuse("invalid_pem")
    return SecretDocument("pem", MappingProxyType(entries))


def _opaque(content: bytes) -> SecretDocument:
    decoded = _decode(content)
    if decoded.endswith("\r\n"):
        decoded = decoded[:-2]
    elif decoded.endswith("\n"):
        decoded = decoded[:-1]
    if not decoded or "\n" in decoded or "\r" in decoded:
        _refuse("opaque_value_invalid")
    value = _safe_text(decoded, multiline=False)
    key = "/value"
    return SecretDocument("opaque", MappingProxyType({
        key: SecretEntry(
            key=key, value=value, byte_length=len(content),
            allow_mask=True, allow_exact_length=True,
        ),
    }))


def _binary(content: bytes) -> SecretDocument:
    if not content:
        _refuse("empty_source")
    key = "/file"
    return SecretDocument("binary", MappingProxyType({
        key: SecretEntry(key=key, value=None, kind_hint="binary", byte_length=len(content)),
    }))


_PARSERS = {
    "binary": _binary,
    "ini": _ini,
    "json": _json,
    "opaque": _opaque,
    "pem": _pem,
    "properties": _properties,
    "toml": _toml,
    "xml": _xml,
    "yaml": _yaml,
}


def parse_secret_document(content: bytes, format_name: str) -> SecretDocument:
    """Parse one explicitly selected format without returning parser details."""
    if not isinstance(content, bytes):
        raise TypeError("content must be bytes")
    if len(content) > MAX_SOURCE_BYTES:
        _refuse("source_too_large")
    if format_name == "dotenv":
        _refuse("format_owned_by_dotenv_parser")
    parser = _PARSERS.get(format_name)
    if parser is None:
        _refuse("format_unsupported")

    document: SecretDocument | None = None
    refusal: str | None = None
    try:
        document = parser(content)
    except SecretFormatError as exc:
        refusal = exc.code
    except Exception:
        refusal = "syntax_unsupported"
    if refusal is not None:
        raise SecretFormatError(refusal)
    assert document is not None
    return document


__all__ = [
    "SUPPORTED_FORMATS", "SecretDocument", "SecretEntry", "SecretFormatError",
    "parse_secret_document", "validate_selector",
]
