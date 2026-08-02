"""Read-only ingress observation and capability-aware selection boundary."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import ipaddress
from pathlib import Path

from sandbox.ingress.models import CredentialReference, IngressSelection, ListenerEndpoint
from sandbox.ingress.models import RouteRecord
from sandbox.ingress.models import digest


PROTOCOL_PORTS = {"http": 80, "https": 443}


class IngressService:
    def __init__(self, *, detector, registry, bind_address="127.0.0.77",
                 bind_probe=None, repository=None, transaction_runner=None,
                 consent_decider=None, route_verifier=None, credential_lookup=None,
                 clock=None, sandbox_owner=None):
        self.detector = detector
        self.registry = registry
        self.bind_address = bind_address
        self.bind_probe = bind_probe
        self.repository = repository
        self.transaction_runner = transaction_runner
        self.consent_decider = consent_decider or (lambda _identity: False)
        self.route_verifier = route_verifier or (lambda *_args: False)
        # The lookup receives a machine-local key, never a credential value.  Keeping
        # it outside the repository prevents route/recovery state from becoming a
        # secret store.
        self.credential_lookup = credential_lookup or (lambda _key: False)
        # True when Sandbox's own ingress already publishes the requested
        # endpoint. A container runtime publishes on the proxy's behalf, so the
        # listening process is the runtime's helper, not Caddy -- without this
        # the service would read its own proxy as a foreign conflict and refuse
        # to reuse it (037 US1 scenario 3, FR-002).
        self.sandbox_owner = sandbox_owner or (lambda _endpoint: False)
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
            owned = bool(self.sandbox_owner(endpoint))
            requested.append({
                "protocol": protocol, "address": self.bind_address, "port": port,
                "kernel_bind": probe,
                "state": "sandbox_owned" if owned else
                         "free" if probe == "free" else
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

    @staticmethod
    def effective_pin(*, pin=None, pin_source=None, project_pin=None,
                      machine_override=None):
        """Resolve pins without silently falling back from an explicit choice."""
        if machine_override is not None:
            return machine_override, "machine_override"
        if project_pin is not None:
            return project_pin, "project"
        return pin, pin_source

    def select(self, *, required_protocols=("http",), required_capabilities=(),
               pin=None, pin_source=None, project_pin=None, machine_override=None):
        pin, pin_source = self.effective_pin(
            pin=pin, pin_source=pin_source, project_pin=project_pin,
            machine_override=machine_override,
        )
        protocols = frozenset(required_protocols)
        capabilities = frozenset(required_capabilities) | protocols
        if pin == "disabled":
            return IngressSelection(
                protocols, capabilities, None, (), "ingress_disabled", None,
                pin, pin_source,
            )
        observations = self.detector.observe()
        endpoint_owners = {}
        protocol_owners = {}
        for observation in observations:
            for protocol in protocols:
                if any(item.port == PROTOCOL_PORTS[protocol]
                       for item in observation.endpoints):
                    protocol_owners.setdefault(protocol, set()).add(observation.adapter_id)
                requested = ListenerEndpoint(
                    self.bind_address, PROTOCOL_PORTS[protocol], "tcp",
                )
                if any(item.overlaps(requested) for item in observation.endpoints):
                    endpoint_owners.setdefault(protocol, set()).add(observation.adapter_id)
        owner_sets = [owners for owners in protocol_owners.values() if owners]
        if len(owner_sets) > 1 and not set.intersection(*owner_sets):
            return IngressSelection(
                protocols, capabilities, None, (), "split_ingress_owners", None,
                pin, pin_source,
            )
        overlapping_owners = set().union(*endpoint_owners.values()) \
            if endpoint_owners else set()
        candidates = [item for item in self.registry.items()
                      if capabilities.issubset(item.declaration.capabilities)]
        if pin:
            candidates = [item for item in candidates if item.adapter_id == pin]
        control_unavailable = False
        foreign_endpoint_owner = False
        for candidate in candidates:
            if not candidate.adoptable:
                continue
            observation = next((item for item in observations
                                if item.adapter_id == candidate.adapter_id), None)
            if candidate.adapter_id == "sandbox-caddy":
                owned_endpoints = all(
                    self.sandbox_owner(ListenerEndpoint(
                        self.bind_address, PROTOCOL_PORTS[protocol], "tcp",
                    )) for protocol in protocols
                )
                if overlapping_owners.difference({"sandbox-caddy"}) and not owned_endpoints:
                    foreign_endpoint_owner = True
                    continue
                if observation is None and not owned_endpoints:
                    if self.bind_probe is None or any(
                        self.bind_probe.check(ListenerEndpoint(
                            self.bind_address, PROTOCOL_PORTS[protocol], "tcp",
                        )) != "free" for protocol in protocols
                    ):
                        foreign_endpoint_owner = True
                        continue
            elif observation is None or any(
                candidate.adapter_id not in protocol_owners.get(protocol, set())
                for protocol in protocols
            ):
                continue
            if any(
                endpoint_owners.get(protocol, {candidate.adapter_id})
                != {candidate.adapter_id}
                for protocol in protocols
            ):
                foreign_endpoint_owner = True
                continue
            if candidate.adapter_id == "system-caddy" and any(
                endpoint.owner_confidence != "proven"
                or not ipaddress.ip_address(endpoint.address).is_loopback
                for endpoint in observation.endpoints
                if endpoint.port in {PROTOCOL_PORTS[protocol] for protocol in protocols}
            ):
                control_unavailable = True
                continue
            if candidate.adapter_id == "system-caddy":
                relevant = tuple(
                    endpoint for endpoint in observation.endpoints
                    if endpoint.port in {PROTOCOL_PORTS[protocol] for protocol in protocols}
                )
                identities = {
                    (str((endpoint.process or {}).get("pid") or ""),
                     str((endpoint.process or {}).get("start") or ""),
                     str((endpoint.process or {}).get("executable") or ""))
                    for endpoint in relevant
                }
                if len(identities) != 1 or any(not endpoint.socket_id for endpoint in relevant):
                    control_unavailable = True
                    continue
            if hasattr(candidate.adapter, "ready") and not candidate.adapter.ready():
                control_unavailable = True
                continue
            accepted = self._accepted_addresses(observation, protocols)
            if candidate.adapter_id == "sandbox-caddy" and observation is None:
                accepted = (self.bind_address,)
            if not accepted:
                continue
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
            reason = "pin_unavailable" if pin else (
                "foreign_endpoint_owner" if foreign_endpoint_owner else
                "ingress_control_unavailable" if control_unavailable else
                "no_live_proven_ingress"
            )
        return IngressSelection(
            protocols, capabilities, None, (), reason, None,
            pin, pin_source,
        )

    def _accepted_addresses(self, observation, protocols):
        """Return concrete addresses served by every requested protocol.

        Wildcard listeners are bind scopes, not DNS answers.  Test a bounded set
        of concrete local addresses against each required port and intersect the
        results so B never receives an address observed only on another protocol.
        """
        if observation is None:
            return ()
        candidates = {self.bind_address, "127.0.0.1", "::1"}
        candidates = {
            address for address in candidates
            if ipaddress.ip_address(address).is_loopback
        }
        accepted = None
        for protocol in protocols:
            port = PROTOCOL_PORTS[protocol]
            endpoints = tuple(item for item in observation.endpoints if item.port == port)
            current = {
                address for address in candidates
                if any(endpoint.overlaps(ListenerEndpoint(address, port, "tcp"))
                       for endpoint in endpoints)
            }
            accepted = current if accepted is None else accepted.intersection(current)
        return tuple(sorted(accepted or ()))

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

    def plan_route(self, selection, naming, backend, *, credential_reference=None):
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
        if credential_reference is not None:
            reference = (credential_reference if isinstance(credential_reference, CredentialReference)
                         else CredentialReference(self.consent_identity(selection), credential_reference))
            if not self.credential_lookup(reference.key):
                return {"ok": False, "state": "pending_credentials", "mutated": False,
                        "credential_reference": reference.key,
                        "reason": {"code": "credential_required"}}
        try:
            authority = None
            if selection.adapter_id == "system-caddy":
                relevant = tuple(
                    endpoint for endpoint in observation.endpoints
                    if endpoint.port in {
                        PROTOCOL_PORTS[protocol]
                        for protocol in selection.required_protocols
                    }
                )
                identities = {
                    (str((endpoint.process or {}).get("pid") or ""),
                     str((endpoint.process or {}).get("start") or ""),
                     str((endpoint.process or {}).get("executable") or ""))
                    for endpoint in relevant
                }
                if len(identities) != 1 or any(not item.socket_id for item in relevant):
                    raise ValueError("selected Caddy socket ownership is not singular")
                pid, start, executable = identities.pop()
                executable_path = Path(executable)
                if not pid.isdigit() or not start.isdigit() or not executable_path.is_absolute():
                    raise ValueError("selected Caddy process identity is incomplete")
                executable_digest = hashlib.sha256(executable_path.read_bytes()).hexdigest()
                authority = {
                    "pid": int(pid), "start": start,
                    "executable_digest": executable_digest,
                    "socket_ids": tuple(sorted(str(item.socket_id) for item in relevant)),
                    "observation_fingerprint": observation.fingerprint,
                }
            adapter_plan = spec.adapter.plan_route(
                {"listen": naming["listen"],
                 "protocols": selection.required_protocols,
                 "authority": authority}, naming, backend,
            )
            if getattr(spec.adapter, "requires_baseline_samples", False):
                baseline_urls = tuple(spec.adapter.baseline_urls(adapter_plan))
                if not baseline_urls:
                    return {"ok": False, "state": "fallback", "mutated": False,
                            "reason": {"code": "baseline_samples_unavailable"}}
                adapter_plan = {**adapter_plan, "_baseline_required": True,
                                "_baseline_urls": baseline_urls}
            adapter_plan = {
                **adapter_plan,
                "_incumbent_fingerprint": selection.observation_fingerprint,
            }
        except (OSError, ValueError) as exc:
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
        privilege = getattr(planned["adapter"], "authorize_plan", None)
        if privilege is not None:
            approved = privilege(planned["adapter_plan"], interactive=interactive)
            if not approved.get("ok"):
                return {**approved, "fallback_url": fallback_url,
                        "reason": {"code": approved.get("state", "pending_privilege")}}
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

    def reconcile_owner(self, owner, *, fallback_url=""):
        """Retry durable route cleanup without requiring live registry identity."""
        result = self.cleanup_owner(owner, fallback_url=fallback_url)
        recovery = () if self.repository is None else tuple(
            value for value in self.repository.snapshot()["recovery"].values()
            if self.repository.route(value["route_id"]) is not None
        )
        return {**result, "operation": "ingress_reconcile",
                "recovery": {"residual": list(recovery)}}

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
