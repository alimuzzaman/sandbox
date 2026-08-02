"""Single composed seam that decides WHICH clean-URL provider serves an instance.

Legacy entry points in `sandbox/core/_domains.py` used to embed this decision.
They now delegate here (037 T043, 038 T033) so provider policy lives in the
application layer while the core keeps the default provider's mechanics as a
rollback control.

Policy (037 FR-007/FR-031, 038 FR-029/FR-030):

- `sandbox-caddy` is the DEFAULT: Sandbox's own Docker/Caddy proxy plus
  Sandbox-owned DNS, on every platform and for every runtime.
- Any other adapter id opts in to host adoption through the composed ingress and
  resolver services.
- `disabled` opts out of clean URLs entirely and keeps the per-port URL.
- Precedence: process environment, then machine-local configuration, then
  project configuration, then the default.

Resolution is pure: it reads no files, starts no process, and mutates nothing.
"""

from __future__ import annotations

from typing import Any, Mapping, NamedTuple


DEFAULT_PROVIDER = "sandbox-caddy"
ENV_VARIABLE = "SANDBOX_CLEAN_URL_MODE"
_DEFAULT_ALIASES = frozenset({DEFAULT_PROVIDER, "default", "sandbox", "caddy"})
_DISABLED = "disabled"


class ProviderSelection(NamedTuple):
    """The effective provider, where it came from, and what it implies."""

    provider: str
    source: str
    adoption: bool
    disabled: bool

    def to_dict(self) -> dict[str, Any]:
        return {"provider": self.provider, "source": self.source,
                "adoption": self.adoption, "disabled": self.disabled}


def _candidate(layer: Mapping[str, Any] | None) -> str | None:
    """The provider named by one configuration layer, if any.

    Accepts either the `domains` block or a document containing one, so callers
    can hand over a raw project/machine document without reshaping it first.
    """
    if not isinstance(layer, Mapping):
        return None
    block = layer.get("domains") if isinstance(layer.get("domains"), Mapping) else layer
    if not isinstance(block, Mapping):
        return None
    for key in ("ingress", "strategy"):
        value = block.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return None


def resolve_provider(*, env: Mapping[str, str] | None = None,
                     machine: Mapping[str, Any] | None = None,
                     project: Mapping[str, Any] | None = None) -> ProviderSelection:
    """Resolve the effective clean-URL provider without touching host state."""
    layers = (
        ((env or {}).get(ENV_VARIABLE), "environment"),
        (_candidate(machine), "machine_override"),
        (_candidate(project), "project"),
    )
    for value, source in layers:
        if isinstance(value, str) and value.strip():
            provider = value.strip().lower()
            if provider in _DEFAULT_ALIASES:
                return ProviderSelection(DEFAULT_PROVIDER, source, False, False)
            return ProviderSelection(provider, source, provider != _DISABLED,
                                     provider == _DISABLED)
    return ProviderSelection(DEFAULT_PROVIDER, "default", False, False)
