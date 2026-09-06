"""Fail-closed common authority checks for server configuration fragments."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

from sandbox.services.redaction import REDACTION_FAILED, redact_text

from .input import MAX_FRAGMENT_BYTES


AUTHORITY = "wordpress-cache-v1"
COMMON_POLICY_REVISION = "wordpress-cache-v1/common/1"
_NAME = re.compile(r"[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9])){0,63}\Z")
_SENSITIVE_NAME = re.compile(
    r"(?:authorization|cookie|credential|password|passphrase|secret|token|"
    r"api-?key|private-?key|access-?key)(?:s)?\Z",
    re.IGNORECASE,
)
_FORBIDDEN_TEXT = (
    re.compile(r"(?i)(?:\.\./|/etc/|/proc/|/sys/|/dev/|/var/(?!www/[^\s;]*/wp-content/uploads/))"),
    re.compile(r"(?i)/(?:wp-admin(?:/|\b)|wp-login\.php\b|wp-json(?:/|\b)|sandbox[-_/])"),
    re.compile(r"(?i)\b(?:authorization|set-cookie|proxy-authorization)\b"),
    re.compile(r"(?i)\bhttps?://"),
)
_FORBIDDEN_DIRECTIVES = frozenset({
    "chroot", "daemon", "docroot", "error_log", "exec", "fastcgi_pass",
    "grpc_pass", "group", "import", "include", "listen", "load_module", "pid",
    "proxy_pass", "return", "root", "scgi_pass", "server", "ssl_certificate",
    "ssl_certificate_key", "system", "user", "uwsgi_pass", "vhroot",
    "worker_processes",
})
_COMMON_DIRECTIVES = frozenset({
    "access_log", "add_header", "allowbrowse", "cache", "cachecontrol",
    "cachekeymodify", "cachelookup", "context", "enable", "expires", "header",
    "if", "internal", "location", "rewrite", "rewritecond", "rewriterule", "set",
})
_DIRECTIVE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


@dataclass(frozen=True)
class _NativeStatement:
    tokens: tuple[str, ...]
    contexts: tuple[tuple[str, ...], ...]
    opens_block: bool


def _digest(value: object) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def validate_fragment_name(name: str) -> str:
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError("fragment_name_invalid")
    if _SENSITIVE_NAME.search(name) or redact_text(f"{name}=synthetic") != f"{name}=synthetic":
        raise ValueError("fragment_secret_like_input")
    return name


def validate_fragment_bytes(payload: bytes) -> str:
    if not isinstance(payload, bytes):
        raise ValueError("fragment_content_invalid")
    if not payload:
        raise ValueError("fragment_source_empty")
    if len(payload) > MAX_FRAGMENT_BYTES:
        raise ValueError("fragment_source_too_large")
    if payload.startswith(b"\xef\xbb\xbf"):
        raise ValueError("fragment_content_invalid")
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError("fragment_content_invalid") from None
    if any(
        (ord(character) < 32 and character not in "\t\n\r")
        or 127 <= ord(character) <= 159
        for character in text
    ):
        raise ValueError("fragment_content_invalid")
    quote: str | None = None
    escaped = False
    comment = False
    for character in text:
        if comment:
            if character in "\r\n":
                comment = False
            continue
        if quote is None and character == "#":
            comment = True
            continue
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if character in {"'", '"'}:
            if quote is None:
                quote = character
            elif quote == character:
                quote = None
    if quote is not None or escaped:
        raise ValueError("fragment_content_invalid")
    redacted = redact_text(text)
    if redacted == REDACTION_FAILED or redacted != text:
        raise ValueError("fragment_secret_like_input")
    return text


def _native_statements(text: str) -> tuple[_NativeStatement, ...]:
    """Parse directive boundaries and retain a balanced enclosing-block stack."""
    statements: list[_NativeStatement] = []
    statement: list[str] = []
    contexts: list[tuple[str, ...]] = []
    token: list[str] = []
    quote: str | None = None
    escaped = False
    comment = False
    index = 0

    def finish_token() -> None:
        if token:
            statement.append("".join(token))
            token.clear()

    def finish_statement(*, opens_block: bool = False) -> None:
        finish_token()
        if not statement:
            if opens_block:
                raise ValueError("authority_syntax_invalid")
            return
        tokens = tuple(statement)
        statements.append(_NativeStatement(tokens, tuple(contexts), opens_block))
        statement.clear()
        if opens_block:
            contexts.append(tokens)

    while index < len(text):
        character = text[index]
        if comment:
            if character in "\r\n":
                comment = False
                finish_statement()
            index += 1
            continue
        if escaped:
            token.append(character)
            escaped = False
            index += 1
            continue
        if character == "\\":
            escaped = True
            token.append(character)
            index += 1
            continue
        if quote is not None:
            token.append(character)
            if character == quote:
                quote = None
            index += 1
            continue
        if character in {"'", '"'}:
            quote = character
            token.append(character)
            index += 1
            continue
        if character == "#":
            comment = True
            finish_statement()
            index += 1
            continue
        if character == "%" and index + 1 < len(text) and text[index + 1] == "{":
            end = text.find("}", index + 2)
            if end < 0:
                raise ValueError("authority_syntax_invalid")
            token.append(text[index:end + 1])
            index = end + 1
            continue
        if character.isspace():
            finish_token()
            if character in "\r\n":
                finish_statement()
            index += 1
            continue
        if character == ";":
            finish_statement()
            index += 1
            continue
        if character == "{":
            finish_statement(opens_block=True)
            index += 1
            continue
        if character == "}":
            finish_statement()
            if not contexts:
                raise ValueError("authority_syntax_invalid")
            contexts.pop()
            index += 1
            continue
        token.append(character)
        index += 1
    if quote is not None or escaped:
        raise ValueError("authority_syntax_invalid")
    finish_statement()
    if contexts:
        raise ValueError("authority_syntax_invalid")
    return tuple(statements)


def _cache_path(value: str, *, document_root: bool = False) -> bool:
    candidate = value.strip("()")
    if any(marker in candidate for marker in ("..", "\\", "'", '"', "%")):
        return False
    if document_root and candidate.startswith("$document_root/"):
        candidate = candidate.removeprefix("$document_root/")
    else:
        if "$" in candidate:
            return False
        candidate = candidate.lstrip("/")
    return candidate == "wp-content/cache" or candidate.startswith("wp-content/cache/")


def _validate_statement_scope(item: _NativeStatement, *, server_type: str) -> None:
    statement = item.tokens
    directive = statement[0].lower()
    context_names = tuple(context[0].lower() for context in item.contexts)
    if len(context_names) > 1:
        raise ValueError("authority_scope_forbidden")

    block_directives = (
        {"if", "location"} if server_type == "nginx" else {"rewrite", "context"}
    )
    if item.opens_block != (directive in block_directives):
        raise ValueError("authority_scope_forbidden")

    if directive == "location":
        if (
            server_type != "nginx"
            or context_names
            or len(statement) != 3
            or statement[1] != "^~"
            or not _cache_path(statement[2])
        ):
            raise ValueError("authority_scope_forbidden")
        return
    if directive == "context":
        if (
            server_type != "litespeed"
            or context_names
            or len(statement) != 2
            or not _cache_path(statement[1])
        ):
            raise ValueError("authority_scope_forbidden")
        return
    if directive == "rewrite" and server_type == "litespeed":
        if context_names or statement != ("rewrite",):
            raise ValueError("authority_scope_forbidden")
        return
    if directive == "if":
        if server_type != "nginx" or context_names or len(statement) < 3:
            raise ValueError("authority_scope_forbidden")
        normalized = tuple(token.strip("()") for token in statement[1:])
        for index, token in enumerate(normalized):
            if token in {"-f", "!-f"}:
                if index + 1 >= len(normalized) or not _cache_path(
                    normalized[index + 1], document_root=True
                ):
                    raise ValueError("authority_path_forbidden")
        return
    if server_type == "nginx":
        allowed = {
            (): {"set", "rewrite"},
            ("if",): {"set", "rewrite"},
            ("location",): {
                "internal", "add_header", "access_log", "expires", "cachecontrol",
            },
        }
    else:
        allowed = {
            ("rewrite",): {
                "enable", "rewritecond", "rewriterule", "cache", "cachecontrol",
                "cachekeymodify", "cachelookup", "header", "expires",
            },
            ("context",): {
                "allowbrowse", "cache", "cachecontrol", "cachekeymodify",
                "cachelookup", "header", "expires",
            },
        }
    if directive not in allowed.get(context_names, set()):
        raise ValueError("authority_scope_forbidden")

    if directive == "rewriterule":
        if len(statement) < 3:
            raise ValueError("authority_syntax_invalid")
        destination = statement[2].strip("()")
        if destination != "-" and not _cache_path(destination):
            raise ValueError("authority_path_forbidden")

    for raw in statement[1:]:
        argument = raw.strip("()")
        if argument.startswith("/") and not (
            directive == "rewrite" and _cache_path(argument)
        ):
            raise ValueError("authority_path_forbidden")


def validate_common_authority(text: str, *, server_type: str) -> dict[str, str]:
    if server_type not in {"nginx", "litespeed"}:
        raise ValueError("server_unsupported")
    if not isinstance(text, str):
        raise ValueError("authority_input_invalid")
    if any(rule.search(text) for rule in _FORBIDDEN_TEXT):
        raise ValueError("authority_forbidden")
    for item in _native_statements(text):
        statement = item.tokens
        directive = statement[0].lower()
        if _DIRECTIVE.fullmatch(statement[0]) is None:
            raise ValueError("authority_directive_unknown")
        if directive in _FORBIDDEN_DIRECTIVES or directive.startswith("ssl_"):
            raise ValueError("authority_forbidden")
        if directive not in _COMMON_DIRECTIVES:
            raise ValueError("authority_directive_unknown")
        _validate_statement_scope(item, server_type=server_type)
        if directive == "add_header" and (
            len(statement) not in {3, 4}
            or statement[1].lower() != "x-xspeed-cache"
            or re.fullmatch(r"[A-Za-z0-9._-]{1,64}", statement[2]) is None
            or (len(statement) == 4 and statement[3].lower() != "always")
        ):
            raise ValueError("authority_header_forbidden")
        if directive == "header":
            if (
                len(statement) != 4
                or statement[1].lower() != "set"
                or statement[2].lower() != "x-xspeed-cache"
                or re.fullmatch(r"[A-Za-z0-9._-]{1,64}", statement[3]) is None
            ):
                raise ValueError("authority_header_forbidden")
        if directive == "access_log":
            if len(statement) != 2:
                raise ValueError("authority_path_forbidden")
            path = statement[1]
            if (
                not path.startswith("wp-content/uploads/")
                or any(marker in path for marker in ("$", "%{", "'", '"', "\\", ".."))
            ):
                raise ValueError("authority_path_forbidden")
        if directive == "rewrite" and statement[1:]:
            path = statement[-2] if len(statement) >= 2 else ""
            if (
                len(statement) < 4
                or statement[-1].lower() != "last"
                or not path.lstrip("/").startswith("wp-content/cache/")
                or any(marker in path for marker in ("$", "%{", "'", '"', "\\", ".."))
            ):
                raise ValueError("authority_path_forbidden")
        for argument in statement[1:]:
            if "wp-content/cache/" in argument and not argument.lstrip("/").startswith(
                "wp-content/cache/"
            ):
                raise ValueError("authority_path_forbidden")
    return {
        "status": "accepted",
        "authority": AUTHORITY,
        "common_policy_revision": COMMON_POLICY_REVISION,
        "checks_digest": _digest(
            {"schema": 1, "authority": AUTHORITY, "server_type": server_type, "text": text}
        ),
    }


def validate_set_conflicts(items: tuple[dict[str, object], ...]) -> dict[str, str]:
    """Reject duplicate normalized names and declared complete-set ownership keys."""
    seen: dict[str, set[str]] = {
        "name": set(), "variable": set(), "location": set(), "context": set(),
        "cache_key": set(), "marker": set(),
    }
    normalized: list[dict[str, object]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("fragment_set_conflict")
        name = item.get("name")
        if not isinstance(name, str):
            raise ValueError("fragment_set_conflict")
        validate_fragment_name(name)
        for key, values in seen.items():
            raw = item.get(key)
            if key == "name":
                raw = name
            if isinstance(raw, str):
                candidates = (raw,)
            elif raw is None:
                candidates = ()
            elif isinstance(raw, (list, tuple)):
                candidates = tuple(raw)
            else:
                raise ValueError("fragment_set_conflict")
            for candidate in candidates:
                if not isinstance(candidate, str) or candidate in values:
                    raise ValueError("fragment_set_conflict")
                values.add(candidate)
        normalized.append(item)
    normalized.sort(key=lambda item: str(item["name"]))
    return {
        "status": "accepted",
        "checks_digest": _digest({"schema": 1, "items": normalized}),
    }
