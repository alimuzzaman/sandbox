"""Tell a Tailscale SSH policy refusal on a joined remote apart from control access.

A joined remote admits its control service by tailnet identity alone. Jobs,
server CLI work and front-door probes still run `sb` over keyless Tailscale
SSH, which the server's own tailnet SSH policy decides separately. When that
policy refuses, say so with the one check the operator can run themselves.
"""

REFUSALS = ("permission denied", "tailnet policy", "requires an additional check")
NEEDS_SSH = "jobs (job-start/job-status/job-output --remote), host stack and host apply"


def joined(remote) -> bool:
    return (isinstance(remote, dict) and remote.get("joined") is True
            and remote.get("control_transport") == "tailscale")


def refused(remote, result) -> bool:
    """True only for the SSH client's own refusal (exit 255), never a remote command's exit."""
    if not joined(remote) or getattr(result, "returncode", None) != 255:
        return False
    stderr = getattr(result, "stderr", "")
    text = stderr.lower() if isinstance(stderr, str) else ""
    return any(refusal in text for refusal in REFUSALS)


def target(remote) -> str:
    user = str(remote.get("ssh") or "").rpartition("@")[0] or "USER"
    host = remote.get("tailscale_host") or "HOST"
    return f"{user}@{host}"


def check_command(remote) -> str:
    return f"tailscale ssh {target(remote)} true"


def denied_message(remote) -> str:
    return (f"Tailscale SSH to {target(remote)} is refused by the server's tailnet SSH policy; "
            f"{NEEDS_SSH} run over it on a joined remote. Control reads such as "
            f"`remote service status` still work. Check: `{check_command(remote)}` "
            "(finish any browser check it opens); these commands work once it succeeds.")
