"""Read-only ingress observation and capability-aware selection boundary."""

from __future__ import annotations

from sandbox.ingress.models import IngressSelection, ListenerEndpoint


PROTOCOL_PORTS = {"http": 80, "https": 443}


class IngressService:
    def __init__(self, *, detector, registry, bind_address="127.0.0.77"):
        self.detector = detector
        self.registry = registry
        self.bind_address = bind_address

    def support(self):
        return {"ok": True, "operation": "ingress_support", "state": "ready",
                "adapters": [{
                    "adapter_id": item.adapter_id,
                    "products": list(item.declaration.products),
                    "platforms": list(item.declaration.platforms),
                    "support_tier": item.declaration.support_tier,
                    "capabilities": sorted(item.declaration.capabilities),
                    "evidence_id": item.declaration.evidence_id,
                    "adoptable": item.adoptable,
                } for item in self.registry.items()], "mutated": False}

    def detect(self):
        observations = self.detector.observe()
        return {"ok": True, "operation": "ingress_detect", "state": "ready",
                "observations": [{
                    "adapter_id": item.adapter_id, "product": item.product,
                    "support_tier": item.support_tier,
                    "capabilities": sorted(item.capabilities),
                    "fingerprint": item.fingerprint,
                    "endpoints": [endpoint.to_dict() for endpoint in item.endpoints],
                } for item in observations], "mutated": False}

    def select(self, *, required_protocols=("http",), required_capabilities=(),
               pin=None, pin_source=None):
        protocols = frozenset(required_protocols)
        capabilities = frozenset(required_capabilities) | protocols
        observations = self.detector.observe()
        endpoint_owners = {}
        for observation in observations:
            for protocol in protocols:
                requested = ListenerEndpoint(
                    self.bind_address, PROTOCOL_PORTS[protocol], "tcp",
                )
                if any(item.overlaps(requested) for item in observation.endpoints):
                    endpoint_owners.setdefault(protocol, set()).add(observation.adapter_id)
        owner_sets = [owners for owners in endpoint_owners.values() if owners]
        if len(owner_sets) > 1 and not set.intersection(*owner_sets):
            return IngressSelection(
                protocols, capabilities, None, (), "split_ingress_owners", None,
                pin, pin_source,
            )
        candidates = [item for item in self.registry.items()
                      if capabilities.issubset(item.declaration.capabilities)]
        if pin:
            candidates = [item for item in candidates if item.adapter_id == pin]
        for candidate in candidates:
            if not candidate.adoptable:
                continue
            observation = next((item for item in observations
                                if item.adapter_id == candidate.adapter_id), None)
            if observation is None and candidate.adapter_id != "sandbox-caddy":
                continue
            accepted = tuple(sorted({endpoint.address for endpoint in
                                     (observation.endpoints if observation else ())}))
            if candidate.adapter_id == "sandbox-caddy" and not accepted:
                accepted = (self.bind_address,)
            return IngressSelection(
                protocols, capabilities, candidate.adapter_id, accepted,
                "selected", observation.fingerprint if observation else None,
                pin, pin_source,
            )
        return IngressSelection(
            protocols, capabilities, None, (),
            "pin_unavailable" if pin else "no_live_proven_ingress", None,
            pin, pin_source,
        )
