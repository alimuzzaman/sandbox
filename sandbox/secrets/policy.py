"""Secret metadata, validation, fixed masking, and destination policy."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .models import SecretBrokerError


KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_JWT_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
_PEM_MARKER = "-----BEGIN "
_APPROVED_PUNCTUATION = frozenset("_-~+/=:@.%")
_PUBLIC_PREFIXES = {
    "stripe-secret-v1": ("sk_test_", "sk_live_", "rk_test_", "rk_live_", "sk_org_"),
    "github-token-v1": ("ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_"),
    "cloudflare-token-v1": ("cfut_",),
}
_DANGEROUS_EXACT = frozenset({
    "NODE_OPTIONS", "PYTHONPATH", "PYTHONHOME", "PERL5OPT", "RUBYOPT",
    "BASH_ENV", "ENV", "SHELLOPTS", "PS4", "PROMPT_COMMAND",
    "GIT_ASKPASS", "SSH_ASKPASS",
})
_DANGEROUS_PREFIXES = ("LD_", "DYLD_")


@dataclass(frozen=True)
class Classification:
    kind: str
    public_prefix: str | None = None


def validate_key(key: str) -> str:
    if not isinstance(key, str) or not KEY_RE.fullmatch(key):
        raise SecretBrokerError("key_invalid", "secret key is not a portable identifier")
    return key


def length_bucket(length: int) -> str:
    if length == 0:
        return "0"
    for upper, label in (
        (7, "1-7"), (15, "8-15"), (23, "16-23"), (31, "24-31"),
        (63, "32-63"), (127, "64-127"), (255, "128-255"),
    ):
        if length <= upper:
            return label
    return "256+"


def _character_classes(value: str) -> int:
    return sum((
        any(char.isupper() for char in value),
        any(char.islower() for char in value),
        any(char.isdigit() for char in value),
        any(char in _APPROVED_PUNCTUATION for char in value),
    ))


def classify(key: str, value: str) -> Classification:
    upper_key = key.upper()
    if any(token in upper_key for token in ("PASSWORD", "PASSPHRASE", "PRIVATE_KEY")):
        return Classification("password")
    if "\n" in value or "\r" in value:
        return Classification("multiline")
    if value.startswith(_PEM_MARKER):
        return Classification("key_material")
    if _JWT_RE.fullmatch(value):
        return Classification("jwt")
    try:
        structured = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        structured = None
    if isinstance(structured, (dict, list)):
        return Classification("structured")
    try:
        parsed = urlsplit(value)
    except ValueError:
        parsed = None
    if parsed and parsed.scheme and (parsed.username is not None or parsed.password is not None):
        return Classification("connection_string")
    if re.search(r"(?:password|passwd|pwd)=", value, re.IGNORECASE):
        return Classification("connection_string")
    for prefixes in _PUBLIC_PREFIXES.values():
        for prefix in prefixes:
            if value.startswith(prefix):
                return Classification("recognized_opaque", prefix)
    if (
        len(value) >= 24
        and value.isascii()
        and value.isprintable()
        and _character_classes(value) >= 3
        and all(char.isalnum() or char in _APPROVED_PUNCTUATION for char in value)
    ):
        return Classification("unrecognized_opaque")
    return Classification("protected")


def metadata(key: str, value: str, *, exact_length: bool = False) -> dict[str, Any]:
    classification = classify(key, value)
    result: dict[str, Any] = {
        "key": key,
        "state": "empty" if value == "" else "present",
        "kind": classification.kind,
        "length_bucket": length_bucket(len(value)),
    }
    if exact_length:
        result["exact_length"] = len(value)
        result["exact_length_disclosed"] = True
    return result


def validate(profile: str, key: str, value: str) -> dict[str, Any]:
    if profile == "opaque-token-v1":
        expected_prefixes: tuple[str, ...] = ()
    else:
        expected_prefixes = _PUBLIC_PREFIXES.get(profile, ())
        if not expected_prefixes:
            raise SecretBrokerError("profile_unknown", "secret validation profile is not registered")
    classes = _character_classes(value)
    checks = {
        "present": "pass" if bool(value) else "fail",
        "length": "pass" if len(value) >= 24 else "fail",
        "character_policy": "pass" if value.isascii() and value.isprintable() and classes >= 3 else "fail",
        "public_prefix": (
            "not_applicable" if not expected_prefixes
            else "pass" if value.startswith(expected_prefixes) else "fail"
        ),
    }
    return {
        "key": key,
        "profile": profile,
        "syntax": "pass" if all(state in {"pass", "not_applicable"} for state in checks.values()) else "fail",
        "live_checked": False,
        "checks": checks,
    }


def fixed_mask(key: str, value: str) -> dict[str, Any]:
    classification = classify(key, value)
    if len(value) < 24 or len(value) - 4 < 16:
        raise SecretBrokerError("mask_denied", "secret class is not eligible for a masked identifier")
    if classification.kind not in {"recognized_opaque", "unrecognized_opaque"}:
        raise SecretBrokerError("mask_denied", "secret class is not eligible for a masked identifier")
    prefix = classification.public_prefix or ""
    return {
        "key": key,
        "kind": classification.kind,
        "public_prefix": classification.public_prefix,
        "last4": value[-4:],
        "masked": f"{prefix}<redacted>{value[-4:]}",
        "disclosed_material": True,
    }


def validate_destination(name: str) -> str:
    validate_key(name)
    if name in _DANGEROUS_EXACT or name.startswith(_DANGEROUS_PREFIXES):
        raise SecretBrokerError("destination_denied", "secret destination can alter process execution")
    return name
