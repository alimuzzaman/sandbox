"""Build a minimal environment and descriptor-closure plan for payload launch."""

from __future__ import annotations


ENV_ALLOWLIST = frozenset({"PATH", "LANG", "LC_ALL", "TZ", "HOME", "USER", "LOGNAME",
                           "WP_ENVIRONMENT_TYPE", "XDEBUG_TRIGGER"})


class CredentialInjector:
    """Stage credential bytes out-of-band and install only opaque references."""
    def __init__(self, *, secret_provider, staging_writer, installer):
        self.secret_provider = secret_provider
        self.staging_writer = staging_writer
        self.installer = installer

    def install(self, *, machine_id, policy_digest, references):
        installed = []
        for reference in tuple(references):
            if not isinstance(reference, str) or not reference or "=" in reference:
                raise ValueError("credential reference is invalid")
            name = reference.rsplit("/", 1)[-1]
            secret = self.secret_provider(reference)
            if not isinstance(secret, (bytes, bytearray)) or not secret:
                raise ValueError("credential provider returned invalid bytes")
            path = self.staging_writer(machine_id, name, bytes(secret))
            self.installer(machine_id, policy_digest, name, path)
            installed.append({"reference": reference, "name": name,
                              "container_path": f"/run/credentials/sandbox/{name}"})
        return tuple(installed)


def sanitize_execution_context(environment, credential_refs=()):
    clean = {key: str(value) for key, value in dict(environment).items()
             if key in ENV_ALLOWLIST and "\x00" not in str(value)}
    refs = tuple(credential_refs)
    if any(not isinstance(ref, str) or not ref or "=" in ref for ref in refs):
        raise ValueError("credential reference is invalid")
    return {"environment": clean, "credential_refs": refs,
            "close_fds_from": 3, "pass_fds": (), "control_sockets": ()}
