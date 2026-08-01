"""Application boundary for local naming; mutations arrive through adapters only."""

from __future__ import annotations

from pathlib import Path
import sys
from datetime import datetime, timezone
from typing import Any, Callable

from sandbox.config.domains import normalize_hostname
from sandbox.network.models import (
    ConsentRecord, DomainResult, ResolutionBinding,
)


class DomainContextError(ValueError):
    pass


class DomainService:
    def __init__(
        self, *, config_loader: Callable, project_registry: Any,
        adapters: Any, repository: Any, process: Any, http: Any, endpoints: Any,
        observer: Callable | None = None, ingress_offer: Callable | None = None,
        clock: Any | None = None, authority: Any | None = None,
        verifier: Callable | None = None, consent_decider: Callable | None = None,
        platform: str | None = None, identity_persister: Callable | None = None,
    ) -> None:
        self.config_loader = config_loader
        self.project_registry = project_registry
        self.adapters = adapters
        self.repository = repository
        self.process = process
        self.http = http
        self.endpoints = endpoints
        self.observer = observer
        self.ingress_offer = ingress_offer
        self.clock = clock
        self.authority = authority
        self.verifier = verifier or (lambda _hostname, _addresses, _fallback: False)
        self.consent_decider = consent_decider or (lambda _owner: False)
        self.platform = platform or ("darwin" if sys.platform == "darwin" else "linux")
        self.identity_persister = identity_persister

    def support(self) -> dict[str, Any]:
        return {
            "ok": True, "operation": "support", "state": "ready",
            "adapters": [{
                "adapter_id": item.adapter_id,
                "managers": list(item.managers),
                "platforms": list(item.platforms),
                "support_tier": item.support_tier,
                "capabilities": sorted(item.capabilities),
                "evidence_id": item.evidence_id,
                "adoptable": item.adoptable,
            } for item in self.adapters.items()],
            "mutated": False,
        }

    def _context(self, project_dir: str, label: str) -> tuple[dict, dict, str, str]:
        config = self.config_loader(project_dir, label=label)
        root = str(Path(config["root"]).resolve())
        policy = config["domains"]
        record = self.project_registry.registry_get(root, label=label) or {}
        if not record:
            raise DomainContextError(
                "No Sandbox instance is registered for this project and label. "
                "Run `./sb ensure --project-dir <project>` first.",
            )
        hostname = policy.get("hostname")
        if not hostname:
            stem = record.get("instance") or config.get("slug") or Path(root).name
            safe_stem = "".join(
                char.lower() if char.isalnum() else "-" for char in str(stem)
            ).strip("-") or "sandbox"
            hostname = normalize_hostname(f"{safe_stem}.{policy['tld']}")
        fallback = record.get("url")
        if not fallback:
            port = record.get("wordpress_port") or record.get("http_port")
            fallback = f"http://localhost:{port}" if port else "http://localhost"
        return config, policy, hostname, fallback

    def status(self, project_dir: str, *, label: str = "default") -> DomainResult:
        try:
            _config, policy, hostname, fallback = self._context(project_dir, label)
        except DomainContextError as exc:
            return DomainResult(
                ok=False, state="invalid", hostname=None,
                hostname_source="default", strategy=None,
                strategy_source="default",
                resolver={"owner": "none", "tier": "unavailable"},
                actual_answers=(), expected_addresses=(), ownership="none",
                health="fallback", fallback_url="",
                reason={"code": "project_not_registered", "message": str(exc)},
                mutated=False,
            )
        observation = self.observer(hostname) if self.observer else None
        resolver = (
            {"owner": observation.owner_id, "tier": observation.support_tier,
             "manager": observation.manager}
            if observation is not None else
            {"owner": "unobserved", "tier": "unavailable"}
        )
        answers = observation.current_answers if observation is not None else ()
        return DomainResult(
            ok=False, state="fallback", hostname=hostname,
            hostname_source=policy["hostnameSource"],
            strategy=policy.get("strategy"),
            strategy_source=policy["strategySource"], resolver=resolver,
            actual_answers=tuple(answers), expected_addresses=(), ownership="none",
            health="fallback", fallback_url=fallback,
            reason={
                "code": "resolver_not_selected",
                "message": "No live-proven scoped resolver adapter is selected.",
            }, mutated=False,
        )

    def _result(self, *, state: str, hostname: str, policy: dict, observation,
                expected: tuple[str, ...], fallback: str, reason_code: str,
                message: str, ok: bool = False, mutated: bool = False,
                health: str = "fallback", ownership: str = "none") -> DomainResult:
        return DomainResult(
            ok=ok, state=state, hostname=hostname,
            hostname_source=policy["hostnameSource"],
            strategy=policy.get("strategy") or observation.manager,
            strategy_source=policy["strategySource"] if policy.get("strategy") else "detected",
            resolver={"owner": observation.owner_id, "tier": observation.support_tier,
                      "manager": observation.manager},
            actual_answers=observation.current_answers,
            expected_addresses=expected, ownership=ownership, health=health,
            fallback_url=fallback,
            reason={"code": reason_code, "message": message}, mutated=mutated,
        )

    def _prepare(self, project_dir: str, label: str):
        try:
            config, policy, hostname, fallback = self._context(project_dir, label)
        except DomainContextError:
            return None, self.status(project_dir, label=label)
        if self.observer is None or self.ingress_offer is None:
            return None, self.status(project_dir, label=label)
        observation = self.observer(hostname)
        offer = self.ingress_offer(config["root"], label)
        accepted = tuple(offer.get("accepted_addresses") or ())
        fallback = offer.get("fallback_url") or fallback
        if not accepted:
            return None, self._result(
                state="fallback", hostname=hostname, policy=policy,
                observation=observation, expected=(), fallback=fallback,
                reason_code="ingress_address_unavailable",
                message="Ingress supplied no acceptable local listener address.",
            )
        if policy.get("suffixClass") == "public":
            if set(observation.current_answers).intersection(accepted) and self.verifier(
                hostname, accepted, fallback,
            ):
                return None, self._result(
                    state="ready", hostname=hostname, policy=policy,
                    observation=observation, expected=accepted, fallback=fallback,
                    reason_code="external_answer_verified",
                    message="Public DNS already resolves to the selected ingress.",
                    ok=True, mutated=False, health="healthy", ownership="external",
                )
            return None, self._result(
                state="incompatible_identity", hostname=hostname, policy=policy,
                observation=observation, expected=accepted, fallback=fallback,
                reason_code="external_answer_incompatible",
                message="Public DNS does not resolve to an accepted ingress address.",
            )
        if policy.get("migrationState") == "required":
            return None, self._result(
                state="incompatible_identity", hostname=hostname, policy=policy,
                observation=observation, expected=accepted, fallback=fallback,
                reason_code="legacy_identity_requires_migration",
                message="The persisted hostname is preserved but requires an explicit migration.",
            )
        pin = policy.get("strategy")
        if pin:
            spec = self.adapters.get(pin)
            if spec is None:
                return None, self._result(
                    state="unsupported", hostname=hostname, policy=policy,
                    observation=observation, expected=accepted, fallback=fallback,
                    reason_code="pinned_resolver_unavailable",
                    message=f"Pinned resolver {pin!r} is not available in this build.",
                )
        else:
            matches = self.adapters.matching(observation.manager, self.platform)
            spec = matches[0] if matches else None
        if spec is None or not spec.adoptable or spec.adapter is None:
            return None, self._result(
                state="unsupported", hostname=hostname, policy=policy,
                observation=observation, expected=accepted, fallback=fallback,
                reason_code="resolver_not_adoptable",
                message="The observed resolver is not live-proven for scoped adoption.",
            )
        if policy.get("wildcard") and "zone" not in spec.capabilities:
            return None, self._result(
                state="unsupported", hostname=hostname, policy=policy,
                observation=observation, expected=accepted, fallback=fallback,
                reason_code="wildcard_unsupported",
                message="The selected resolver does not support a scoped wildcard zone.",
            )
        address, port = self.endpoints.allocate()
        target = accepted[0]
        suffix = policy["tld"]
        owner = f"{Path(config['root']).resolve()}::{label}"
        kind = "zone" if policy.get("wildcard") else "exact"
        binding_name = f"*.{hostname}" if kind == "zone" else hostname
        adapter_plan = (
            spec.adapter.plan(suffix, address, port)
            if hasattr(spec.adapter, "plan") and observation.manager == "resolved"
            else {"kind": kind, "hostname": hostname, "address": target,
                  "suffix": suffix, "port": port}
        )
        binding = ResolutionBinding.create(
            kind=kind, name=binding_name, target=target,
            adapter_id=spec.adapter_id, owners=(owner,), desired=adapter_plan,
        )
        return {
            "config": config, "policy": policy, "hostname": hostname,
            "fallback": fallback, "observation": observation, "offer": offer,
            "accepted": accepted, "spec": spec, "address": address, "port": port,
            "binding": binding, "adapter_plan": {
                **adapter_plan, "hostname": hostname, "target": target,
                "binding_id": binding.binding_id,
                "observation_fingerprint": observation.fingerprint,
            },
        }, None

    def plan(self, project_dir: str, *, label: str = "default") -> DomainResult:
        prepared, result = self._prepare(project_dir, label)
        if result is not None:
            return result
        return self._result(
            state="pending_consent", hostname=prepared["hostname"],
            policy=prepared["policy"], observation=prepared["observation"],
            expected=prepared["accepted"], fallback=prepared["fallback"],
            reason_code="consent_required",
            message="Run interactively to review resolver adoption.",
        )

    def apply(self, project_dir: str, *, label: str = "default",
              interactive: bool = False) -> DomainResult:
        prepared, result = self._prepare(project_dir, label)
        if result is not None:
            return result
        observation = prepared["observation"]
        consent = self.repository.snapshot()["consents"].get(observation.owner_id)
        if not consent or consent.get("decision") != "accepted":
            if not interactive:
                return self._result(
                    state="pending_consent", hostname=prepared["hostname"],
                    policy=prepared["policy"], observation=observation,
                    expected=prepared["accepted"], fallback=prepared["fallback"],
                    reason_code="consent_required",
                    message="Run interactively to review resolver adoption.",
                )
            if not self.consent_decider(observation.owner_id):
                return self._result(
                    state="fallback", hostname=prepared["hostname"],
                    policy=prepared["policy"], observation=observation,
                    expected=prepared["accepted"], fallback=prepared["fallback"],
                    reason_code="consent_declined", message="Resolver adoption was declined.",
                )
            now = self.clock() if callable(self.clock) else datetime.now(timezone.utc)
            decided_at = now.isoformat().replace("+00:00", "Z")
            self.repository.put_consent(ConsentRecord(
                observation.owner_id, "accepted", decided_at, 1,
            ))
        current = self.observer(prepared["hostname"])
        if current.fingerprint != observation.fingerprint:
            return self._result(
                state="fallback", hostname=prepared["hostname"],
                policy=prepared["policy"], observation=current,
                expected=prepared["accepted"], fallback=prepared["fallback"],
                reason_code="resolver_changed",
                message="Resolver ownership changed after planning; retry from observation.",
            )
        if self.authority is None:
            return self._result(
                state="fallback", hostname=prepared["hostname"],
                policy=prepared["policy"], observation=current,
                expected=prepared["accepted"], fallback=prepared["fallback"],
                reason_code="authority_unavailable",
                message="The scoped answering authority is unavailable.",
            )
        self.authority.ensure(
            (prepared["binding"],), address=prepared["address"], port=prepared["port"],
        )
        applied = prepared["spec"].adapter.apply(prepared["adapter_plan"])
        if not applied.get("ok"):
            self.authority.remove(prepared["binding"].binding_id)
            return self._result(
                state="fallback", hostname=prepared["hostname"], policy=prepared["policy"],
                observation=current, expected=prepared["accepted"], fallback=prepared["fallback"],
                reason_code="resolver_apply_failed", message=applied.get("error", "Resolver apply failed."),
                mutated=bool(applied.get("mutated")),
            )
        plan = {**prepared["adapter_plan"], "applied": applied.get("applied") or {}}
        if not self.verifier(prepared["hostname"], prepared["accepted"], prepared["fallback"]):
            prepared["spec"].adapter.rollback(plan)
            self.authority.remove(prepared["binding"].binding_id)
            return self._result(
                state="fallback", hostname=prepared["hostname"], policy=prepared["policy"],
                observation=current, expected=prepared["accepted"], fallback=prepared["fallback"],
                reason_code="verification_failed",
                message="Fresh DNS and ingress verification failed; prior state was restored.",
                mutated=True,
            )
        binding = prepared["binding"].with_applied(applied.get("applied") or {})
        self.repository.put_binding(binding)
        if self.identity_persister is not None:
            self.identity_persister(
                prepared["config"]["root"], label, prepared["hostname"],
                prepared["policy"]["hostnameSource"],
            )
        return self._result(
            state="ready", hostname=prepared["hostname"], policy=prepared["policy"],
            observation=current, expected=prepared["accepted"], fallback=prepared["fallback"],
            reason_code="ready", message="Hostname resolution is verified.", ok=True,
            mutated=True, health="healthy", ownership="owned",
        )

    def cleanup(self, project_dir: str, *, label: str = "default",
                interactive: bool = False) -> DomainResult:
        return self.status(project_dir, label=label)

    def reconsider(self, resolver_id: str | None) -> dict[str, Any]:
        if not resolver_id:
            return {
                "ok": False, "operation": "reconsider", "state": "invalid",
                "reason": {"code": "resolver_required",
                           "message": "Pass --resolver with an observed resolver identity."},
                "mutated": False,
            }
        removed = self.repository.remove_consent(resolver_id)
        return {
            "ok": True, "operation": "reconsider", "state": "ready",
            "resolver": {"owner": resolver_id}, "removed": removed, "mutated": removed,
        }
