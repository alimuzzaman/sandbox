"""Shared mismatch refusal shape and remedies (spec 061 FR-016..FR-018).

Every remedy is built as an argv list for the named remote and rendered with
``shlex.join``, so it never carries a placeholder; tests parse each one with
the real CLI parser.
"""
from __future__ import annotations

import shlex

from sandbox.remote_runtime.verdict import (
    COMPATIBLE, EXACT_ONLY, PROTOCOL_NEWER, PROTOCOL_TOO_OLD, UNKNOWN, Verdict,
)

MISMATCH_CODE = "remote_runtime_revision_mismatch"
STRICT_PIN_UNVERIFIABLE = "strict_pin_unverifiable"

_SB = "./sb"


def _command(*argv: str) -> str:
    return shlex.join([_SB, *argv])


def migrate_plan_remedy(remote: str) -> str:
    return _command("remote", "service", "migrate", remote, "--plan")


def migrate_confirm_remedy(remote: str) -> str:
    return _command("remote", "service", "migrate", remote, "--confirm")


def status_remedy(remote: str) -> str:
    return _command("remote", "service", "status", remote)


def diagnostics_remedy(remote: str) -> str:
    return _command("remote", "service", "diagnostics", remote)


def remedies_for(verdict: Verdict, remote: str) -> list[str]:
    if verdict.state == UNKNOWN:
        return [status_remedy(remote), diagnostics_remedy(remote)]
    if verdict.state == PROTOCOL_TOO_OLD:
        # The checkout is older than the runtime serves; updating the checkout
        # is the usual fix, and a migrate plan shows what a downgrade breaks.
        return [status_remedy(remote), migrate_plan_remedy(remote)]
    return [migrate_plan_remedy(remote), migrate_confirm_remedy(remote)]


_MESSAGES = {
    PROTOCOL_NEWER: ("this checkout speaks a newer control protocol than the "
                     "installed runtime serves; migrating installs this checkout's "
                     "runtime for every controller of the remote"),
    PROTOCOL_TOO_OLD: ("this checkout's control protocol is older than the installed "
                       "runtime serves; update this checkout, or plan a migrate to see "
                       "what installing it would break"),
    EXACT_ONLY: ("the installed runtime predates control-protocol declarations, so "
                 "only the exact revision is accepted until it is migrated"),
    UNKNOWN: ("the installed runtime state is not determinate; establish it before "
              "retrying or migrating"),
    COMPATIBLE: ("strict mode requires the exact installed revision"),
}


def refusal(verdict: Verdict, remote: str, *, code: str = MISMATCH_CODE,
            message: str | None = None, broken_pin: dict | None = None) -> dict:
    """The shared refusal ``error`` object for CLI JSON and MCP."""
    error = {
        "code": code,
        "message": message or _MESSAGES.get(verdict.state, _MESSAGES[UNKNOWN]),
        "verdict": verdict.state,
        "reason": verdict.reason,
        "remote": remote,
        **{key: value for key, value in verdict.as_mapping().items()
           if key in {"local", "installed", "strict"}},
        "remedies": remedies_for(verdict, remote),
    }
    if broken_pin:
        error["broken_pin"] = broken_pin
    return error


def refusal_text(error: dict) -> str:
    """One-paragraph human rendering of :func:`refusal`."""
    local = error.get("local") or {}
    installed = error.get("installed") or {}

    def protocol(side):
        value = side.get("protocol")
        return (f"protocol {value['spoken']} (serves {value['oldest_served']}+)"
                if isinstance(value, dict) else "protocol undeclared")

    lines = [
        f"{error['code']}: {error['message']}",
        f"  verdict: {error['verdict']} ({error['reason']})",
        f"  installed: {installed.get('revision') or 'unknown'}, {protocol(installed)}",
        f"  local:     {local.get('revision') or 'unknown'}, {protocol(local)}",
    ]
    pin = error.get("broken_pin")
    if isinstance(pin, dict):
        lines.append(f"  your pin was broken by {pin.get('broken_by')} at {pin.get('broken_at')}")
    lines.append("  remedies:")
    lines.extend(f"    {item}" for item in error.get("remedies") or ())
    return "\n".join(lines)
