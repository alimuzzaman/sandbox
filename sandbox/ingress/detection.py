"""Best-effort product evidence layered over authoritative kernel listeners."""

from __future__ import annotations

from collections import defaultdict

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
}


class IngressDetector:
    def __init__(self, *, listener_observer, declarations=BUILTIN_INGRESS):
        self.listener_observer = listener_observer
        self.declarations = {item.adapter_id: item for item in declarations}

    @staticmethod
    def _identity(endpoint):
        process = dict(endpoint.process or {})
        service = dict(endpoint.service or {})
        command = str(process.get("command") or process.get("executable") or "").lower()
        container = str(service.get("container") or "").lower()
        if "sandbox-proxy" in container and "caddy" in command:
            return "sandbox-caddy", "Sandbox Caddy"
        for token, identity in PRODUCTS.items():
            if token in command:
                return identity
        return "unidentified", "Unidentified listener"

    def observe(self):
        grouped = defaultdict(list)
        names = {}
        for endpoint in self.listener_observer.snapshot():
            adapter_id, product = self._identity(endpoint)
            grouped[adapter_id].append(endpoint)
            names[adapter_id] = product
        observations = []
        for adapter_id, endpoints in grouped.items():
            declaration = self.declarations[adapter_id]
            observations.append(IngressObservation(
                adapter_id, names[adapter_id], tuple(endpoints),
                declaration.support_tier, declaration.capabilities,
                product_identity={"evidence": "process-best-effort"},
            ))
        return tuple(sorted(observations, key=lambda item: item.adapter_id))
