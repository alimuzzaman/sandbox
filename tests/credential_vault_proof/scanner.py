"""Bounded, secret-safe scanner for harness documents and evidence output.

Findings never echo what they matched. A finding carries a stable code, the
location, and an offset, so a reviewer can go look without the report itself
becoming the leak. The scanner reads only paths it is handed; it never walks a
home directory, an environment, or a registered secret source.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any


MAX_SCAN_BYTES = 1024 * 1024
MAX_SCAN_FILES = 512
MAX_FINDINGS = 64
MAX_DEPTH = 12

# Key names that may never appear in a manifest, ledger record, artifact, or
# rendered report, whatever their value.
FORBIDDEN_KEYS = frozenset({
    "api_key", "apikey", "audit_token", "authorization", "body", "claim_id",
    "cookie", "credential", "credential_value", "descriptor_content",
    "environment", "headers", "lease_id", "operation_id", "password",
    "prepared_attempt", "private_key", "request_body", "request_digest",
    "request_headers", "response_body", "secret", "session", "source_reference",
    "token",
})

_PATTERNS = (
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{8,}")),
    ("authorization_header", re.compile(r"(?i)\bauthorization\s*[:=]\s*\S")),
    ("api_key_assignment", re.compile(
        r"(?i)\b(?:api[_-]?key|x-api-key|apikey)\s*[:=]\s*\S{6,}")),
    ("cookie_header", re.compile(r"(?i)\b(?:set-)?cookie\s*[:=]\s*\S")),
    ("pem_private_key", re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("aws_secret_key", re.compile(r"(?i)\baws_secret_access_key\s*[:=]")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("stripe_key", re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{10,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b")),
    ("json_web_token", re.compile(
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("database_url", re.compile(
        r"(?i)\b(?:postgres|postgresql|mysql|mariadb|mongodb|redis|amqp)://\S*:\S*@")),
    ("url_credentials", re.compile(r"(?i)\bhttps?://[^\s/@]+:[^\s/@]+@")),
    ("environment_dump", re.compile(r"(?m)^[A-Z][A-Z0-9_]{2,}=\S+$")),
    ("opaque_blob", re.compile(r"[A-Za-z0-9+/]{512,}={0,2}")),
    ("exception_trace", re.compile(r"Traceback \(most recent call last\)")),
    ("internal_identifier", re.compile(
        r"(?i)\b(?:operation_id|lease_id|request_digest|claim_id|audit_token|"
        r"prepared_attempt|descriptor_content)\b")),
    ("guest_request_header", re.compile(
        r"(?i)^\s*(?:user-agent|accept-encoding|x-forwarded-for|proxy-authorization)\s*:",
        re.MULTILINE)),
)


def _finding(code: str, location: str, offset: int) -> dict[str, Any]:
    return {"code": code, "location": location[:256], "offset": int(offset)}


def scan_text(text: Any, *, location: str = "text") -> tuple[dict[str, Any], ...]:
    """Report which detectors fired and where, never what they matched."""
    if isinstance(text, (bytes, bytearray)):
        try:
            text = bytes(text).decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return (_finding("undecodable_bytes", location, 0),)
    if not isinstance(text, str):
        return (_finding("unscannable_value", location, 0),)
    if len(text) > MAX_SCAN_BYTES:
        return (_finding("oversize_scan_target", location, 0),)
    findings = []
    for code, pattern in _PATTERNS:
        match = pattern.search(text)
        if match is not None:
            findings.append(_finding(code, location, match.start()))
            if len(findings) >= MAX_FINDINGS:
                break
    return tuple(findings)


def scan_document(value: Any, *, location: str = "document",
                  depth: int = 0) -> tuple[dict[str, Any], ...]:
    """Walk a JSON-compatible document for forbidden keys and secret shapes."""
    if depth > MAX_DEPTH:
        return (_finding("document_too_deep", location, 0),)
    findings: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            place = f"{location}.{key}"
            if isinstance(key, str) and key.strip().lower() in FORBIDDEN_KEYS:
                findings.append(_finding("forbidden_key", place, 0))
                continue
            if isinstance(key, str):
                findings.extend(scan_text(key, location=place))
            findings.extend(scan_document(item, location=place, depth=depth + 1))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            findings.extend(scan_document(
                item, location=f"{location}[{index}]", depth=depth + 1,
            ))
    elif isinstance(value, str):
        findings.extend(scan_text(value, location=location))
    elif isinstance(value, (bytes, bytearray)):
        findings.append(_finding("binary_value", location, 0))
    elif not isinstance(value, (int, float, bool)) and value is not None:
        findings.append(_finding("unscannable_value", location, 0))
    return tuple(findings[:MAX_FINDINGS])


def scan_directory(root: Any, *, max_files: int = MAX_SCAN_FILES,
                   max_bytes: int = MAX_SCAN_BYTES) -> tuple[dict[str, Any], ...]:
    """Scan one evidence directory, refusing symlinks and oversize members."""
    path = Path(root)
    findings: list[dict[str, Any]] = []
    if path.is_symlink() or not path.is_dir():
        return (_finding("evidence_root_invalid", str(path), 0),)
    seen = 0
    for member in sorted(path.rglob("*")):
        if seen >= max_files:
            findings.append(_finding("evidence_too_many_files", str(path), 0))
            break
        if member.is_dir():
            continue
        seen += 1
        location = str(member.relative_to(path))
        if member.is_symlink():
            findings.append(_finding("evidence_symlink", location, 0))
            continue
        try:
            size = member.stat().st_size
        except OSError:
            findings.append(_finding("evidence_unreadable", location, 0))
            continue
        if size > max_bytes:
            findings.append(_finding("oversize_scan_target", location, 0))
            continue
        try:
            findings.extend(scan_text(member.read_bytes(), location=location))
        except OSError:
            findings.append(_finding("evidence_unreadable", location, 0))
        if len(findings) >= MAX_FINDINGS:
            break
    return tuple(findings[:MAX_FINDINGS])


def is_clean(findings: Any) -> bool:
    return not tuple(findings or ())


__all__ = [
    "FORBIDDEN_KEYS", "MAX_FINDINGS", "MAX_SCAN_BYTES", "MAX_SCAN_FILES",
    "is_clean", "scan_directory", "scan_document", "scan_text",
]
