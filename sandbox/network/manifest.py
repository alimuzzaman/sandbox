"""Built-in resolver declarations; implementation never implies advertising."""

from __future__ import annotations

from .registry import ResolverAdapterRegistry, ResolverAdapterSpec


class ResolverProofAttestation:
    """Invocation-only proof capability; never deserialize this from config/CLI."""

    __slots__ = ("_adapter_id", "_evidence_id")

    def __init__(self, adapter_id: str, evidence_id: str) -> None:
        if adapter_id != "systemd-resolved":
            raise ValueError("proof attestation adapter is invalid")
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            raise ValueError("proof attestation evidence is invalid")
        self._adapter_id = adapter_id
        self._evidence_id = evidence_id.strip()

    def evidence_for(self, adapter_id: str) -> str | None:
        return self._evidence_id if adapter_id == self._adapter_id else None


def _spec(adapter_id, managers, platforms, tier, capabilities, order):
    return ResolverAdapterSpec(
        adapter_id, None, tuple(managers), tuple(platforms), tier,
        frozenset(capabilities), None, order,
    )


BUILTIN_RESOLVER_ADAPTERS = (
    _spec("systemd-resolved", ("resolved",), ("linux",), "implemented_unproven", ("exact", "zone"), 10),
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
    *, proof_attestation: ResolverProofAttestation | None = None,
) -> ResolverAdapterRegistry:
    """Build the registry; live proof may opt one invocation into conformance.

    Proof evidence is deliberately an injected argument rather than an environment
    variable or project setting, so ordinary support/status calls cannot advertise
    an unproven host mutation path.
    """
    implementations = adapters or {}
    attestation = (
        proof_attestation if isinstance(proof_attestation, ResolverProofAttestation) else None
    )
    registry = ResolverAdapterRegistry()
    for declaration in BUILTIN_RESOLVER_ADAPTERS:
        evidence_id = (
            attestation.evidence_for(declaration.adapter_id) if attestation else None
        )
        tier = "adoptable" if evidence_id else declaration.support_tier
        registry.register(ResolverAdapterSpec(
            declaration.adapter_id,
            implementations.get(declaration.adapter_id),
            declaration.managers,
            declaration.platforms,
            tier,
            declaration.capabilities,
            evidence_id or declaration.evidence_id,
            declaration.order,
        ))
    return registry
