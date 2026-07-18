"""Explicit manifest for common project configuration providers."""

from __future__ import annotations

from .runtime import normalize_runtime_policy


COMMON_CONFIG_PROVIDERS = (
    ("runtime", normalize_runtime_policy, "sandbox.config.runtime", 10),
)


def apply_common_config(result: dict) -> dict:
    resolved = dict(result)
    for key, provider, _owner, _order in sorted(COMMON_CONFIG_PROVIDERS, key=lambda item: item[3]):
        resolved[key] = provider(resolved.get(key))
    return resolved
