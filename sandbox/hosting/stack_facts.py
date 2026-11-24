"""Declared stack facts: named, non-secret values a stack page shows.

A template's ``stack.facts`` names a label, an env file in the stack checkout,
one key in it and a kind (``url`` or ``text``). At stack up Sandbox reads only
those keys from those files and records ``{label, kind, value}``. Every other
line of the file is skipped unparsed, so its key and value never reach the
record, a log or an error message. This module is pure: callers pass the file
bytes in. It imports only the standard library, so the panel's record
reader stays light.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Callable, Literal, TypedDict
from urllib.parse import urlsplit

FACTS_INVALID = "stack_facts_invalid"
FACTS_LIMIT = "stack_facts_limit"
FACT_SECRET_KEY = "stack_fact_secret_key"
FACT_MISSING = "stack_fact_missing"
FACT_VALUE_INVALID = "stack_fact_value_invalid"

MAX_FACTS = 8
MAX_ENV_FILE_BYTES = 65536
MAX_URL_LENGTH = 512
KINDS = ("url", "text")
FactKind = Literal["url", "text"]

_LABEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,31}")
_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
_PATH_PART_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")
_TEXT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._:/+@-]{0,127}")
_HOST_RE = re.compile(r"(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*")
_URL_CHARS_RE = re.compile(r"[A-Za-z0-9._~:/?#\[\]@!&'()*+,;=%-]+")
_ASSIGNMENT_RE = re.compile(rb"\s*(?:export\s+)?([A-Za-z0-9_.-]+)\s*[=:]\s*")
# A key whose name says it carries a credential is refused, whatever its value.
_SECRET_WORDS = ("PASSWORD", "PASSWD", "PASSPHRASE", "TOKEN", "SECRET", "KEY", "CREDENTIAL")
_SECRET_PARTS = {"PASS", "PWD", "SALT", "DSN", "COOKIE", "SESSION"}


class StackFactsError(ValueError):
    """A fact declaration or value that cannot be shown; callers map it to their refusal type."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.detail = message


class Fact(TypedDict):
    label: str
    kind: FactKind
    value: str


@dataclass(frozen=True)
class FactDeclaration:
    label: str
    env_file: str
    key: str
    kind: FactKind


def _env_file(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 128 or value.startswith("/"):
        raise StackFactsError(FACTS_INVALID, "stack.facts env_file must be a relative path")
    parts = value.split("/")
    if any(part in {".", ".."} or not _PATH_PART_RE.fullmatch(part) for part in parts):
        raise StackFactsError(FACTS_INVALID, "stack.facts env_file must be a relative path inside the stack")
    return PurePosixPath(*parts).as_posix()


def secret_looking(key: str) -> bool:
    upper = key.upper()
    parts = upper.split("_")
    return (any(word in upper for word in _SECRET_WORDS)
            or not _SECRET_PARTS.isdisjoint(parts) or parts[-1] == "AUTH")


def parse_declarations(value: object) -> tuple[FactDeclaration, ...]:
    """Validate ``stack.facts``; no file is read here."""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise StackFactsError(FACTS_INVALID, "stack.facts must be a list")
    if len(value) > MAX_FACTS:
        raise StackFactsError(FACTS_LIMIT, f"stack.facts accepts at most {MAX_FACTS} entries")
    out: list[FactDeclaration] = []
    for index, item in enumerate(value):
        where = f"stack.facts[{index}]"
        if not isinstance(item, dict) or set(item) - {"label", "env_file", "key", "kind"}:
            raise StackFactsError(FACTS_INVALID, f"{where} takes only label, env_file, key and kind")
        label, key, kind = item.get("label"), item.get("key"), item.get("kind", "text")
        if not isinstance(label, str) or not _LABEL_RE.fullmatch(label):
            raise StackFactsError(FACTS_INVALID, f"{where}.label must be 1 to 32 letters, digits, spaces, dots, "
                                  "underscores or hyphens")
        if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
            raise StackFactsError(FACTS_INVALID, f"{where}.key must be an environment variable name")
        if secret_looking(key):
            raise StackFactsError(FACT_SECRET_KEY, f"{where}.key {key} looks like a credential; "
                                  "stack facts show only non-secret values")
        if kind not in KINDS:
            raise StackFactsError(FACTS_INVALID, f"{where}.kind must be url or text")
        out.append(FactDeclaration(label=label, env_file=_env_file(item.get("env_file")), key=key, kind=kind))
    if len({item.label for item in out}) != len(out):
        raise StackFactsError(FACTS_INVALID, "stack.facts labels must be unique")
    return tuple(out)


def check_value(kind: str, value: object, label: str = "fact") -> str:
    """Return ``value`` when it is safe to show as ``kind``; otherwise refuse without echoing it."""
    bad = StackFactsError(FACT_VALUE_INVALID, f"stack fact {label}: the value is not a valid {kind}")
    if not isinstance(value, str):
        raise bad
    if kind == "url":
        if len(value) > MAX_URL_LENGTH or not value.startswith("https://") or not _URL_CHARS_RE.fullmatch(value):
            raise bad
        try:
            parts = urlsplit(value)
            port = parts.port
        except ValueError:
            raise bad from None
        host = (parts.hostname or "").removesuffix(".")
        if (parts.scheme != "https" or "@" in parts.netloc or parts.username is not None
                or not _HOST_RE.fullmatch(host) or port == 0):
            raise bad
        return value
    if kind == "text" and _TEXT_RE.fullmatch(value):
        return value
    raise bad


def url_hostname(value: str) -> str | None:
    try:
        host = urlsplit(value).hostname
        return host.removesuffix(".") if host else None
    except ValueError:
        return None


def _quote_end(line: bytes, quote: int, start: int) -> int | None:
    escaped = False
    for index in range(start, len(line)):
        char = line[index]
        if char == quote and not escaped:
            return index
        escaped = char == ord("\\") and not escaped
    return None


def _named_values(data: bytes, keys: set[bytes]) -> dict[bytes, str]:
    """Extract declared literals, skipping multiline assignments as a unit."""
    found: dict[bytes, str] = {}
    quote = None
    for line in data.removeprefix(b"\xef\xbb\xbf").splitlines():
        if quote is not None:
            if _quote_end(line, quote, 0) is not None:
                quote = None
            continue
        match = _ASSIGNMENT_RE.match(line)
        if not match:
            continue
        key, start = match[1], match.end()
        end = None
        if start < len(line) and line[start] in b"'\"":
            end = _quote_end(line, line[start], start + 1)
            if end is None:
                quote = line[start]
        if key not in keys:
            continue
        if quote is not None:
            found[key] = "\0"
        elif end is not None:
            tail = line[end + 1:].strip()
            raw = line[start + 1:end] if not tail or tail.startswith(b"#") else b"\0"
            found[key] = raw.decode("ascii", errors="replace")
        else:
            raw = re.split(rb"[ \t]+#", line[start:], maxsplit=1)[0].rstrip()
            found[key] = raw.decode("ascii", errors="replace")
    return found


def read_facts(declarations: tuple[FactDeclaration, ...],
               read_file: Callable[[str], bytes | None]) -> list[Fact]:
    """Read each declared key from its file and return the checked values, in order.

    ``read_file`` returns the file's bytes or None when the stack has no such
    file. Each file is read once.
    """
    keys_by_file: dict[str, set[bytes]] = {}
    for item in declarations:
        keys_by_file.setdefault(item.env_file, set()).add(item.key.encode("ascii"))
    values: dict[str, dict[bytes, str]] = {}
    facts: list[Fact] = []
    for item in declarations:
        if item.env_file not in values:
            data = read_file(item.env_file)
            if data is not None and len(data) > MAX_ENV_FILE_BYTES:
                raise StackFactsError(FACTS_INVALID, f"stack fact {item.label}: {item.env_file} is too large")
            values[item.env_file] = _named_values(data or b"", keys_by_file[item.env_file])
        value = values[item.env_file].get(item.key.encode("ascii"))
        if not value:
            raise StackFactsError(FACT_MISSING,
                                  f"stack fact {item.label}: {item.env_file} does not set {item.key}")
        facts.append({"label": item.label, "kind": item.kind, "value": check_value(item.kind, value, item.label)})
    return facts


def parse_recorded(value: object) -> list[Fact]:
    """Validate facts read back from a stack record or panel summary."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_FACTS:
        raise ValueError("stack facts must be a list of at most 8 entries")
    out: list[Fact] = []
    for item in value:
        if (not isinstance(item, dict) or set(item) != {"label", "kind", "value"}
                or not isinstance(item["label"], str) or not _LABEL_RE.fullmatch(item["label"])
                or item["kind"] not in KINDS):
            raise ValueError("stack fact is malformed")
        try:
            check_value(item["kind"], item["value"], item["label"])
        except StackFactsError:
            raise ValueError("stack fact value is invalid") from None
        out.append({"label": item["label"], "kind": item["kind"], "value": item["value"]})
    if len({item["label"] for item in out}) != len(out):
        raise ValueError("stack fact labels must be unique")
    return out
