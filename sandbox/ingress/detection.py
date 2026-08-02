"""Best-effort product evidence layered over authoritative kernel listeners."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import sys

from .manifest import BUILTIN_INGRESS
from .models import IngressObservation


PRODUCTS = {
    "nginx": ("system-nginx", "nginx"),
    "apache2": ("system-apache", "Apache"),
    "httpd": ("system-apache", "Apache"),
    "caddy": ("system-caddy", "Caddy"),
    "traefik": ("traefik", "Traefik"),
    "herd": ("herd-valet", "Herd"),
    "valet": ("herd-valet", "Valet"),
    "nginx-proxy-manager": ("nginx-proxy-manager", "Nginx Proxy Manager"),
    "ddev-router": ("ddev", "DDEV router"),
    "ddev": ("ddev", "DDEV router"),
    "xampp": ("xampp", "XAMPP"),
    "laragon": ("laragon", "Laragon"),
    "wamp": ("wamp", "WAMP"),
    "local": ("local", "Local"),
}


class IngressDetector:
    def __init__(self, *, listener_observer, declarations=BUILTIN_INGRESS, platform=None):
        self.listener_observer = listener_observer
        self.declarations = {item.adapter_id: item for item in declarations}
        self.platform = platform or ("darwin" if sys.platform == "darwin" else "linux")

    @staticmethod
    def _identity(endpoint):
        process = dict(endpoint.process or {})
        service = dict(endpoint.service or {})
        command = str(process.get("command") or "").lower()
        executable = Path(str(process.get("executable") or "")).name.lower()
        container = str(service.get("container") or "").lower()
        if "sandbox-proxy" in container and "caddy" in command:
            return "sandbox-caddy", "Sandbox Caddy"
        # Match the most specific public command token first so a product name
        # such as ``nginx-proxy-manager`` is never downgraded to plain nginx.
        for token, identity in sorted(PRODUCTS.items(), key=lambda item: len(item[0]), reverse=True):
            if token in {command, executable}:
                return identity
        return "unidentified", "Unidentified listener"

    def observe(self):
        grouped = defaultdict(list)
        names = {}
        for endpoint in self.listener_observer.snapshot():
            if endpoint.protocol != "tcp" or endpoint.port not in {80, 443}:
                continue
            adapter_id, product = self._identity(endpoint)
            grouped[adapter_id].append(endpoint)
            names[adapter_id] = product
        observations = []
        for adapter_id, endpoints in grouped.items():
            declaration = self.declarations[adapter_id]
            tier = declaration.support_tier if self.platform in declaration.platforms \
                else "outside_platform"
            observations.append(IngressObservation(
                adapter_id, names[adapter_id], tuple(endpoints),
                tier, declaration.capabilities,
                product_identity={"evidence": "process-best-effort"},
            ))
        return tuple(sorted(observations, key=lambda item: item.adapter_id))
