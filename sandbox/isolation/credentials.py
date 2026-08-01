"""Build a minimal environment and descriptor-closure plan for payload launch."""

from __future__ import annotations


ENV_ALLOWLIST = frozenset({"PATH", "LANG", "LC_ALL", "TZ", "HOME", "USER", "LOGNAME",
                           "WP_ENVIRONMENT_TYPE", "XDEBUG_TRIGGER"})


def sanitize_execution_context(environment, credential_refs=()):
    clean = {key: str(value) for key, value in dict(environment).items()
             if key in ENV_ALLOWLIST and "\x00" not in str(value)}
    refs = tuple(credential_refs)
    if any(not isinstance(ref, str) or not ref or "=" in ref for ref in refs):
        raise ValueError("credential reference is invalid")
    return {"environment": clean, "credential_refs": refs,
            "close_fds_from": 3, "pass_fds": (), "control_sockets": ()}
