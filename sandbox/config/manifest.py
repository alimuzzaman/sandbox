"""Explicit manifest for common project configuration providers."""

from __future__ import annotations

from .domains import normalize_domain_policy
from .runtime import normalize_runtime_policy


COMMON_CONFIG_PROVIDERS = (
    ("runtime", lambda result: normalize_runtime_policy(result.get("runtime")),
     "sandbox.config.runtime", 10),
    ("domains", normalize_domain_policy, "sandbox.config.domains", 20),
)


def apply_common_config(result: dict) -> dict:
    resolved = dict(result)
    for key, provider, _owner, _order in sorted(COMMON_CONFIG_PROVIDERS, key=lambda item: item[3]):
        resolved[key] = provider(resolved)
    resolved.pop("_domains_raw", None)
    resolved.pop("_persisted_hostname", None)
    return resolved
