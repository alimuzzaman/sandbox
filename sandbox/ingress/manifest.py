"""Truthful product declarations; code presence never promotes support."""

from .models import SupportDeclaration
from .registry import IngressAdapterRegistry, IngressAdapterSpec


def _decl(adapter_id, products, platforms, tier, capabilities, evidence=None):
    return SupportDeclaration(adapter_id, tuple(products), tuple(platforms), tier,
                              frozenset(capabilities), evidence)


BUILTIN_INGRESS = (
    _decl("sandbox-caddy", ("sandbox-caddy",), ("linux", "darwin"),
          "implemented_unproven", ("http", "https", "wildcard")),
    _decl("herd-valet", ("herd", "valet"), ("linux", "darwin"),
          "implemented_unproven", ("http", "https", "wildcard")),
    _decl("system-nginx", ("nginx",), ("linux", "darwin"),
          "implemented_unproven", ("http", "https", "wildcard")),
    _decl("system-apache", ("apache", "httpd"), ("linux", "darwin"),
          "implemented_unproven", ("http", "https", "wildcard")),
    _decl("system-caddy", ("caddy",), ("linux", "darwin"),
          "implemented_unproven", ("http", "https", "wildcard")),
    _decl("traefik", ("traefik",), ("linux", "darwin"),
          "implemented_unproven", ("http", "https", "wildcard")),
    _decl("nginx-proxy-manager", ("nginx-proxy-manager",), ("linux", "darwin"),
          "credential_pending", ("http", "https", "wildcard")),
    _decl("ddev", ("ddev-router",), ("linux", "darwin"),
          "detect_only", ("http", "https")),
    _decl("desktop-products", ("local", "xampp", "laragon", "wamp"),
          ("linux", "darwin", "windows"), "detect_only", ("http",)),
    _decl("unidentified", ("unknown",), ("linux", "darwin", "wsl2"),
          "unidentified", ()),
)


def built_in_ingress_registry(adapters=None):
    implementations = adapters or {}
    registry = IngressAdapterRegistry()
    for order, declaration in enumerate(BUILTIN_INGRESS, 10):
        registry.register(IngressAdapterSpec(
            declaration, implementations.get(declaration.adapter_id), order,
        ))
    return registry
