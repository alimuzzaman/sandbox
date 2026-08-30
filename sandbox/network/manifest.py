"""Built-in resolver declarations; implementation never implies advertising."""

from __future__ import annotations

from .qualification import SYSTEMD_RESOLVED_QUALIFICATION
from .registry import ResolverAdapterRegistry, ResolverAdapterSpec


def _spec(adapter_id, managers, platforms, tier, capabilities, order, evidence=None):
    return ResolverAdapterSpec(
        adapter_id, None, tuple(managers), tuple(platforms), tier,
        frozenset(capabilities), evidence, order,
    )


BUILTIN_RESOLVER_ADAPTERS = (
    _spec("systemd-resolved", SYSTEMD_RESOLVED_QUALIFICATION.managers,
          SYSTEMD_RESOLVED_QUALIFICATION.platforms, "implemented_unproven",
          SYSTEMD_RESOLVED_QUALIFICATION.capabilities, 10),
    _spec("networkmanager", ("networkmanager",), ("linux",), "implemented_unproven", ("exact", "zone"), 20),
    _spec("macos", ("macos",), ("darwin",), "implemented_unproven", ("exact", "zone"), 30),
    _spec("dnsmasq", ("dnsmasq",), ("linux", "darwin"), "implemented_unproven", ("exact", "zone"), 40),
    _spec("herd-valet", ("herd", "valet"), ("darwin", "linux"), "implemented_unproven", ("exact", "zone"), 50),
    _spec("hosts", ("hosts",), ("linux", "darwin"), "implemented_unproven", ("exact",), 60),
    _spec("external", ("external",), ("linux", "darwin", "wsl2"), "external", ("verify",), 70),
    _spec("unknown", ("unknown",), ("linux", "darwin", "wsl2"), "detect_only", (), 80),
)


def built_in_resolver_registry(
    adapters: dict[str, object] | None = None,
) -> ResolverAdapterRegistry:
    """Build the fixed source-qualified resolver registry."""
    implementations = adapters or {}
    registry = ResolverAdapterRegistry()
    for declaration in BUILTIN_RESOLVER_ADAPTERS:
        registry.register(ResolverAdapterSpec(
            declaration.adapter_id,
            implementations.get(declaration.adapter_id),
            declaration.managers,
            declaration.platforms,
            declaration.support_tier,
            declaration.capabilities,
            declaration.evidence_id,
            declaration.order,
        ))
    return registry
