"""Synthetic-only redaction cases; consumers report case names, never values."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RedactionCase:
    name: str
    value: object
    forbidden: tuple[str, ...]


def _join(prefix: str, fill: str, count: int) -> str:
    return prefix + fill * count


GITHUB = _join("github_pat_", "a", 30)
OPENAI = _join("sk-proj-", "B", 28)
SLACK = "xoxb-123456789012-123456789012-synthetic"
AWS = "AKIA" + "C" * 16
GOOGLE = "AIza" + "D" * 34
STRIPE = _join("sk_test_", "E", 20)
BASIC_USER = "fixture-user"
BASIC_PASSWORD = "fixture-password"
QUERY_TOKEN = _join("query-", "F", 24)
ASSIGNMENT_TOKEN = _join("assignment-", "G", 24)
BEARER = _join("bearer-", "H", 24)


TEXT_CASES = (
    RedactionCase("bearer_header", f"Authorization: Bearer {BEARER}", (BEARER,)),
    RedactionCase("assignment_token", f"token={ASSIGNMENT_TOKEN}", (ASSIGNMENT_TOKEN,)),
    RedactionCase("assignment_password_mixed", f"PaSsWoRd : {ASSIGNMENT_TOKEN}", (ASSIGNMENT_TOKEN,)),
    RedactionCase("assignment_api_key_spacing", f"api_key   =   {ASSIGNMENT_TOKEN}", (ASSIGNMENT_TOKEN,)),
    RedactionCase("github_provider", f"provider failed {GITHUB}", (GITHUB,)),
    RedactionCase("openai_provider", f"provider failed {OPENAI}", (OPENAI,)),
    RedactionCase("slack_provider", f"provider failed {SLACK}", (SLACK,)),
    RedactionCase("aws_provider", f"provider failed {AWS}", (AWS,)),
    RedactionCase("google_provider", f"provider failed {GOOGLE}", (GOOGLE,)),
    RedactionCase("stripe_provider", f"provider failed {STRIPE}", (STRIPE,)),
    RedactionCase(
        "basic_auth_url",
        f"https://{BASIC_USER}:{BASIC_PASSWORD}@example.test/path?mode=safe",
        (BASIC_USER, BASIC_PASSWORD),
    ),
    RedactionCase(
        "token_query",
        f"https://example.test/path?mode=safe&access_token={QUERY_TOKEN}",
        (QUERY_TOKEN,),
    ),
)


STRUCTURE_CASES = (
    RedactionCase(
        "nested_structure",
        {"ok": False, "error": {"message": f"token={ASSIGNMENT_TOKEN}"}, "password": ASSIGNMENT_TOKEN},
        (ASSIGNMENT_TOKEN,),
    ),
    RedactionCase(
        "serialized_argv",
        {"argv": ["tool", "--endpoint", f"https://{BASIC_USER}:{BASIC_PASSWORD}@example.test"]},
        (BASIC_USER, BASIC_PASSWORD),
    ),
)


def nested_exception() -> BaseException:
    try:
        raise ValueError(f"token={ASSIGNMENT_TOKEN}")
    except ValueError as cause:
        try:
            raise RuntimeError(f"request failed at https://{BASIC_USER}:{BASIC_PASSWORD}@example.test") from cause
        except RuntimeError as outer:
            return outer


ALL_FORBIDDEN = tuple(dict.fromkeys(
    item for case in (*TEXT_CASES, *STRUCTURE_CASES) for item in case.forbidden
))


__all__ = [
    "ALL_FORBIDDEN", "ASSIGNMENT_TOKEN", "BASIC_PASSWORD", "BASIC_USER", "BEARER",
    "GITHUB", "OPENAI", "QUERY_TOKEN", "SLACK", "STRUCTURE_CASES", "TEXT_CASES",
    "nested_exception",
]
