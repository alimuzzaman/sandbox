"""Public-evidence-only declarations for ingress products Sandbox cannot mutate."""

from __future__ import annotations


class DetectOnlyAdapter:
    """Describe a recognized product without discovering private control state.

    This deliberately does not implement route planning, validation, activation,
    or cleanup.  The detector supplies public listener/process evidence; callers
    can report its limitation but cannot accidentally promote it to a mutator.
    """

    def __init__(self, adapter_id, *, products, platforms):
        self.adapter_id = adapter_id
        self.products = tuple(products)
        self.platforms = tuple(platforms)

    def detect(self, endpoints, *, platform):
        return {
            "adapter_id": self.adapter_id,
            "products": self.products,
            "platform": platform,
            "available": platform in self.platforms,
            "mode": "detect_only",
            "route_mutations": False,
            "evidence": tuple(endpoint.to_dict() for endpoint in endpoints),
        }
