"""Read-only ingress observation and capability-aware selection boundary."""

from __future__ import annotations

from sandbox.ingress.models import IngressSelection, ListenerEndpoint
from sandbox.ingress.models import RouteRecord
from sandbox.ingress.models import digest
from datetime import datetime, timezone


PROTOCOL_PORTS = {"http": 80, "https": 443}


class IngressService:
    def __init__(self, *, detector, registry, bind_address="127.0.0.77",
                 bind_probe=None, repository=None, transaction_runner=None,
                 consent_decider=None, route_verifier=None, clock=None):
        self.detector = detector
        self.registry = registry
        self.bind_address = bind_address
        self.bind_probe = bind_probe
        self.repository = repository
        self.transaction_runner = transaction_runner
        self.consent_decider = consent_decider or (lambda _identity: False)
        self.route_verifier = route_verifier or (lambda *_args: False)
        self.clock = clock

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
        requested = []
        for protocol, port in PROTOCOL_PORTS.items():
            endpoint = ListenerEndpoint(self.bind_address, port)
            overlap = [
                item.to_dict() for observation in observations
                for item in observation.endpoints if item.overlaps(endpoint)
            ]
            probe = self.bind_probe.check(endpoint) if self.bind_probe else "unavailable"
            requested.append({
                "protocol": protocol, "address": self.bind_address, "port": port,
                "kernel_bind": probe,
                "state": "free" if probe == "free" else
                         "conflict" if probe == "conflict" or overlap else "unknown",
                "overlaps": overlap,
            })
        return {"ok": True, "operation": "ingress_detect", "state": "ready",
                "observations": [{
                    "adapter_id": item.adapter_id, "product": item.product,
                    "support_tier": item.support_tier,
                    "capabilities": sorted(item.capabilities),
                    "fingerprint": item.fingerprint,
                    "endpoints": [endpoint.to_dict() for endpoint in item.endpoints],
                } for item in observations], "requested_endpoints": requested,
                "mutated": False}

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
        pinned = self.registry.get(pin) if pin else None
        pinned_observed = pin and any(item.adapter_id == pin for item in observations)
        if pinned and pinned_observed:
            tier = pinned.declaration.support_tier
            reason = "credential_pending" if tier == "credential_pending" \
                else "detected_not_adoptable"
        else:
            reason = "pin_unavailable" if pin else "no_live_proven_ingress"
        return IngressSelection(
            protocols, capabilities, None, (), reason, None,
            pin, pin_source,
        )

    def naming_offer(self, selection, *, fallback_url):
        if selection.adapter_id is None:
            return None
        spec = self.registry.get(selection.adapter_id)
        return {
            "selection_id": selection.observation_fingerprint,
            "adapter": selection.adapter_id,
            "support_tier": spec.declaration.support_tier,
            "accepted_addresses": selection.accepted_addresses,
            "required_protocols": tuple(sorted(selection.required_protocols)),
            "capabilities": {name: True for name in selection.required_capabilities},
            "fallback_url": fallback_url,
        }

    @staticmethod
    def consent_identity(selection):
        return f"{selection.adapter_id}:{selection.observation_fingerprint or 'free'}"

    def authorize(self, selection, *, interactive=False, fallback_url=""):
        if selection.adapter_id is None:
            return {"ok": False, "state": "fallback", "mutated": False,
                    "fallback_url": fallback_url,
                    "reason": {"code": selection.reason_code}}
        if self.repository is None:
            return {"ok": False, "state": "fallback", "mutated": False,
                    "fallback_url": fallback_url,
                    "reason": {"code": "ingress_repository_unavailable"}}
        identity = self.consent_identity(selection)
        consent = self.repository.snapshot()["consents"].get(identity)
        if consent and consent.get("decision") == "accepted":
            return {"ok": True, "state": "ready", "mutated": False,
                    "consent_identity": identity, "reason": {"code": "consent_remembered"}}
        if consent and consent.get("decision") == "declined":
            return {"ok": False, "state": "fallback", "mutated": False,
                    "fallback_url": fallback_url, "consent_identity": identity,
                    "reason": {"code": "consent_declined"}}
        if not interactive:
            return {"ok": False, "state": "pending_consent", "mutated": False,
                    "fallback_url": fallback_url, "consent_identity": identity,
                    "reason": {"code": "consent_required"}}
        accepted = bool(self.consent_decider(identity))
        now = self.clock() if self.clock else datetime.now(timezone.utc).isoformat()
        self.repository.put_consent(identity, {
            "decision": "accepted" if accepted else "declined",
            "policy_version": 1, "decided_at": str(now),
        })
        return {"ok": accepted, "state": "ready" if accepted else "fallback",
                "mutated": True, "fallback_url": fallback_url,
                "consent_identity": identity,
                "reason": {"code": "consent_accepted" if accepted else "consent_declined"}}

    def plan_route(self, selection, naming, backend):
        if selection.adapter_id is None:
            return {"ok": False, "state": "fallback", "mutated": False,
                    "reason": {"code": selection.reason_code}}
        spec = self.registry.get(selection.adapter_id)
        if spec is None or not spec.adoptable:
            return {"ok": False, "state": "fallback", "mutated": False,
                    "reason": {"code": "ingress_not_adoptable"}}
        observation = next((item for item in self.detector.observe()
                            if item.adapter_id == selection.adapter_id), None)
        if selection.observation_fingerprint and (
            observation is None or observation.fingerprint != selection.observation_fingerprint
        ):
            return {"ok": False, "state": "drifted", "mutated": False,
                    "reason": {"code": "ingress_owner_changed"}}
        try:
            adapter_plan = spec.adapter.plan_route(
                {"listen": naming["listen"],
                 "protocols": selection.required_protocols}, naming, backend,
            )
            adapter_plan = {
                **adapter_plan,
                "_incumbent_fingerprint": selection.observation_fingerprint,
            }
        except ValueError as exc:
            return {"ok": False, "state": "foreign_collision", "mutated": False,
                    "reason": {"code": "hostname_claimed", "message": str(exc)}}
        route = RouteRecord.create(
            owner=naming["owner"], hostname=naming["hostname"], backend=backend,
            adapter_id=selection.adapter_id, protocols=selection.required_protocols,
            capabilities=selection.required_capabilities, desired=adapter_plan,
        )
        return {"ok": True, "state": "planned", "mutated": False,
                "selection": selection, "adapter_plan": adapter_plan,
                "route": route, "adapter": spec.adapter,
                "consent_identity": self.consent_identity(selection)}

    def apply_route(self, planned, *, interactive=False, fallback_url=""):
        if not planned.get("ok"):
            return planned
        if self.repository is None or self.transaction_runner is None:
            return {"ok": False, "state": "fallback", "mutated": False,
                    "fallback_url": fallback_url,
                    "reason": {"code": "ingress_transaction_unavailable"}}
        authorized = self.authorize(
            planned["selection"], interactive=interactive, fallback_url=fallback_url,
        )
        if not authorized["ok"]:
            return authorized
        existing = self.repository.route(planned["route"].route_id)
        if existing is not None and existing.last_applied is not None:
            observed = planned["adapter"].observe_route(planned["adapter_plan"])
            if digest(observed) != digest(existing.last_applied):
                self.repository.put_recovery(existing.route_id, {
                    "route_id": existing.route_id, "adapter_id": existing.adapter_id,
                    "expected_digest": digest(existing.last_applied),
                    "observed_digest": digest(observed), "reason_code": "route_drifted",
                    "status": "drifted",
                })
                return {"ok": False, "state": "drifted", "mutated": False,
                        "fallback_url": fallback_url,
                        "reason": {"code": "route_drifted"}}
            if digest(existing.desired) == digest(planned["route"].desired):
                return {"ok": True, "state": "ready", "mutated": False,
                        "ingress": existing.adapter_id, "route_id": existing.route_id,
                        "hostname": existing.hostname, "fallback_url": fallback_url,
                        "reason": {"code": "already_applied"}}
        result = self.transaction_runner.run(planned["adapter"], planned["adapter_plan"])
        if not result.get("ok"):
            return {**result, "fallback_url": fallback_url,
                    "reason": {"code": result.get("state", "route_apply_failed")}}
        route = planned["route"].with_applied(result["applied"])
        self.repository.put_route(route)
        return {"ok": True, "state": "ready", "mutated": True,
                "ingress": route.adapter_id, "route_id": route.route_id,
                "hostname": route.hostname, "fallback_url": fallback_url,
                "reason": {"code": "ready"}}

    def reconsider(self, identity):
        removed = self.repository.remove_consent(identity) if self.repository else False
        return {"ok": True, "state": "ready", "mutated": removed,
                "consent_identity": identity}

    def cleanup_owner(self, owner, *, fallback_url=""):
        if self.repository is None:
            return {"ok": False, "state": "cleanup_incomplete", "mutated": False,
                    "fallback_url": fallback_url,
                    "reason": {"code": "ingress_repository_unavailable"}}
        routes = [RouteRecord.from_dict(value)
                  for value in self.repository.snapshot()["routes"].values()
                  if value.get("owner") == owner]
        if not routes:
            return {"ok": True, "state": "ready", "mutated": False,
                    "fallback_url": fallback_url, "cleanup": {"complete": True, "residual": []},
                    "reason": {"code": "already_absent"}}
        residual, mutated = [], False
        for route in routes:
            spec = self.registry.get(route.adapter_id)
            adapter = spec.adapter if spec else None
            if adapter is None:
                self.repository.put_recovery(route.route_id, {
                    "route_id": route.route_id, "adapter_id": route.adapter_id,
                    "expected_digest": digest(route.last_applied or {}),
                    "observed_digest": None, "reason_code": "incumbent_unavailable",
                    "status": "unavailable",
                })
                residual.append(route.route_id); continue
            expected_fingerprint = dict(route.desired).get("_incumbent_fingerprint")
            if expected_fingerprint:
                current = next((item for item in self.detector.observe()
                                if item.adapter_id == route.adapter_id), None)
                if current is None or current.fingerprint != expected_fingerprint:
                    self.repository.put_recovery(route.route_id, {
                        "route_id": route.route_id, "adapter_id": route.adapter_id,
                        "expected_digest": digest(route.last_applied or {}),
                        "observed_digest": None,
                        "reason_code": "incumbent_replaced", "status": "drifted",
                    })
                    residual.append(route.route_id); continue
            observed = adapter.observe_route(dict(route.desired))
            if digest(observed) != digest(route.last_applied or {}):
                self.repository.remove_route_if_unchanged(route.route_id, observed)
                residual.append(route.route_id); continue
            result = adapter.cleanup(route)
            if not result.get("ok"):
                self.repository.put_recovery(route.route_id, {
                    "route_id": route.route_id, "adapter_id": route.adapter_id,
                    "expected_digest": digest(route.last_applied or {}),
                    "observed_digest": digest(observed),
                    "reason_code": "cleanup_failed", "status": "unavailable",
                })
                residual.append(route.route_id); continue
            self.repository.remove_route_if_unchanged(route.route_id, observed)
            mutated = True
        return {"ok": not residual,
                "state": "ready" if not residual else "cleanup_incomplete",
                "mutated": mutated, "fallback_url": fallback_url,
                "cleanup": {"complete": not residual, "residual": residual},
                "reason": {"code": "cleanup_complete" if not residual else "cleanup_incomplete"}}
