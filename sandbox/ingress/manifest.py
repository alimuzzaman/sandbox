"""Truthful product declarations; code presence never promotes support."""

from __future__ import annotations

from .models import SupportDeclaration
from .registry import IngressAdapterRegistry, IngressAdapterSpec
from .adapters.detect_only import DetectOnlyAdapter


class IngressProofAttestation:
    """Invocation-only live-proof capability; never deserialize from config/CLI."""

    __slots__ = ("_adapter_id", "_evidence_id")

    def __init__(self, adapter_id: str, evidence_id: str) -> None:
        if adapter_id != "system-caddy":
            raise ValueError("proof attestation adapter is invalid")
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            raise ValueError("proof attestation evidence is invalid")
        self._adapter_id = adapter_id
        self._evidence_id = evidence_id.strip()

    def evidence_for(self, adapter_id: str) -> str | None:
        return self._evidence_id if adapter_id == self._adapter_id else None


def _decl(adapter_id, products, platforms, tier, capabilities, evidence=None):
    return SupportDeclaration(adapter_id, tuple(products), tuple(platforms), tier,
                              frozenset(capabilities), evidence)


BUILTIN_INGRESS = (
    _decl("sandbox-caddy", ("sandbox-caddy",), ("linux", "darwin"),
          "implemented_unproven", ("http", "https", "wildcard")),
    _decl("herd-valet", ("herd", "valet"), ("linux", "darwin"),
          "implemented_unproven", ("http", "https", "wildcard")),
    _decl("system-nginx", ("nginx",), ("linux", "darwin"),
          "implemented_unproven", ("http", "wildcard")),
    _decl("system-apache", ("apache", "httpd"), ("linux", "darwin"),
          "implemented_unproven", ("http", "wildcard")),
    _decl("system-caddy", ("caddy",), ("linux", "darwin"),
          "implemented_unproven", ("http",)),
    _decl("traefik", ("traefik",), ("linux", "darwin"),
          "implemented_unproven", ("http", "wildcard")),
    _decl("nginx-proxy-manager", ("nginx-proxy-manager",), ("linux", "darwin"),
          "credential_pending", ("http", "https", "wildcard")),
    _decl("ddev", ("ddev-router",), ("linux", "darwin"),
          "detect_only", ("http", "https")),
    _decl("local", ("local",), ("darwin",), "detect_only", ("http",)),
    _decl("xampp", ("xampp",), ("linux", "darwin"), "detect_only", ("http",)),
    _decl("laragon", ("laragon",), ("windows",), "detect_only", ("http",)),
    _decl("wamp", ("wamp",), ("windows",), "detect_only", ("http",)),
    _decl("unidentified", ("unknown",), ("linux", "darwin", "wsl2"),
          "unidentified", ()),
)


def built_in_ingress_registry(
    adapters=None, *, proof_attestation: IngressProofAttestation | None = None,
):
    implementations = dict(adapters or {})
    attestation = (
        proof_attestation
        if isinstance(proof_attestation, IngressProofAttestation)
        else None
    )
    for declaration in BUILTIN_INGRESS:
        if declaration.support_tier != "detect_only":
            continue
        implementations.setdefault(
            declaration.adapter_id,
            DetectOnlyAdapter(
                declaration.adapter_id,
                products=declaration.products,
                platforms=declaration.platforms,
            ),
        )
    registry = IngressAdapterRegistry()
    for order, base in enumerate(BUILTIN_INGRESS, 10):
        evidence_id = attestation.evidence_for(base.adapter_id) if attestation else None
        declaration = (_decl(base.adapter_id, base.products, base.platforms,
                             "adoptable", base.capabilities, evidence_id)
                       if evidence_id else base)
        registry.register(IngressAdapterSpec(
            declaration, implementations.get(declaration.adapter_id), order,
        ))
    return registry
