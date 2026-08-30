"""Truthful product declarations; code presence never promotes support."""

from __future__ import annotations

from .models import SupportDeclaration
from .qualification import SYSTEM_CADDY_QUALIFICATION
from .registry import IngressAdapterRegistry, IngressAdapterSpec
from .adapters.detect_only import DetectOnlyAdapter


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
    _decl("system-caddy",
          SYSTEM_CADDY_QUALIFICATION.products,
          SYSTEM_CADDY_QUALIFICATION.platforms, "adoptable",
          SYSTEM_CADDY_QUALIFICATION.capabilities,
          SYSTEM_CADDY_QUALIFICATION.evidence_id),
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


def built_in_ingress_registry(adapters=None):
    implementations = dict(adapters or {})
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
    for order, declaration in enumerate(BUILTIN_INGRESS, 10):
        registry.register(IngressAdapterSpec(
            declaration, implementations.get(declaration.adapter_id), order,
        ))
    return registry
