"""Tell a Tailscale SSH policy refusal on a joined remote apart from control access.

A joined remote admits its control service by tailnet identity alone. Jobs,
server CLI work and front-door probes still run `sb` over keyless Tailscale
SSH, which the server's own tailnet SSH policy decides separately. When that
policy refuses, say so with the one check the operator can run themselves.

A failed session's exit status and stderr alone prove nothing: OpenSSH passes
on the remote command's own exit status, so a command that ran can exit 255
after printing "Permission denied". A refusal is concluded only when a session
that runs nothing but `true` is refused the same way. Tailscale check mode
(a browser re-authentication, often ending in a timeout) is its own outcome
and never says whether a command ran.
"""

DENIALS = ("permission denied", "tailnet policy")
CHECKS = ("requires an additional check",)
REFUSALS = DENIALS + CHECKS
NEEDS_SSH = "jobs (job-start/job-status/job-output --remote), host stack and host apply"
PROBE_COMMAND = "true"
PROBE_TIMEOUT = 15

DENIED = "denied"
CHECK_REQUIRED = "check_required"
AMBIGUOUS = "ambiguous"


def joined(remote) -> bool:
    return (isinstance(remote, dict) and remote.get("joined") is True
            and remote.get("control_transport") == "tailscale")


def _stderr(result) -> str:
    value = getattr(result, "stderr", "")
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    return value.lower() if isinstance(value, str) else ""


def symptom(result):
    """What a session's own output suggests: DENIED, CHECK_REQUIRED or None.

    ``result`` is a finished process or a ``TimeoutExpired``. This is a
    symptom, not proof; see :func:`classify`.
    """
    text = _stderr(result)
    if any(check in text for check in CHECKS):
        return CHECK_REQUIRED
    if getattr(result, "returncode", None) == 255 and any(denial in text for denial in DENIALS):
        return DENIED
    return None


def classify(remote, result, probe):
    """DENIED, CHECK_REQUIRED, AMBIGUOUS, or None when SSH did not refuse.

    DENIED needs evidence that the login itself was refused: ``probe()`` runs
    only ``true`` on a new session, which cannot print a refusal or exit 255,
    so the same refusal there happened before any command. A probe that
    succeeds means the original failure came from the command that ran.
    """
    if not joined(remote):
        return None
    seen = symptom(result)
    if seen != DENIED:
        return seen
    try:
        confirmed = probe()
    except Exception as exc:  # noqa: BLE001 - any probe failure leaves it unproven
        return CHECK_REQUIRED if symptom(exc) == CHECK_REQUIRED else AMBIGUOUS
    if getattr(confirmed, "returncode", None) == 0:
        return None
    return symptom(confirmed) or AMBIGUOUS


def target(remote) -> str:
    user = str(remote.get("ssh") or "").rpartition("@")[0] or "USER"
    host = remote.get("tailscale_host") or "HOST"
    return f"{user}@{host}"


def check_command(remote) -> str:
    return f"tailscale ssh {target(remote)} true"


def denied_message(remote) -> str:
    return (f"Tailscale SSH to {target(remote)} is refused by the server's tailnet SSH policy; "
            f"{NEEDS_SSH} run over it on a joined remote. Control reads such as "
            f"`remote service status` still work. Check: `{check_command(remote)}`; "
            "these commands work once it succeeds.")


def check_required_hint(remote) -> str:
    return (f"Tailscale SSH to {target(remote)} is waiting for a Tailscale check (re-authentication); "
            f"{NEEDS_SSH} run over it on a joined remote. Run `{check_command(remote)}` and finish "
            "the browser check it opens.")


def check_required_message(remote) -> str:
    return (check_required_hint(remote)
            + " Whether this command reached the server is unknown: inspect it before any retry.")


def refusal_error(remote, outcome):
    """The typed transport error for a classified outcome, else None."""
    from sandbox.transports.remote_jobs import TailnetSshCheckRequired, TailnetSshDenied
    if outcome == DENIED:
        return TailnetSshDenied(denied_message(remote))
    if outcome == CHECK_REQUIRED:
        return TailnetSshCheckRequired(check_required_message(remote))
    return None
