"""Application boundary for local naming; mutations arrive through adapters only."""

from __future__ import annotations

from pathlib import Path
import sys
from datetime import datetime, timezone
from typing import Any, Callable

from sandbox.config.domains import normalize_hostname
from sandbox.network.models import (
    CleanupRecovery, ConsentRecord, DomainResult, ResolutionBinding,
    canonical_digest,
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
        binding_observer: Callable | None = None,
        authority_observer: Callable | None = None,
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
        self.binding_observer = binding_observer
        self.authority_observer = authority_observer

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

    def ingress_policy(self, project_dir: str, *, label: str = "default") -> dict[str, Any]:
        """Return only ingress pin intent/provenance; never inspect or mutate a host."""
        config = self.config_loader(project_dir, label=label)
        policy = config["domains"]
        return {"pin": policy.get("ingress"),
                "pin_source": policy.get("ingressSource", "default"),
                "project_root": str(Path(config["root"]).resolve())}

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
        if observation is None:
            expected = ()
        else:
            offer = self.ingress_offer(_config["root"], label) \
                if self.ingress_offer is not None else {}
            expected = tuple(offer.get("accepted_addresses") or ())
            fallback = offer.get("fallback_url") or fallback
            owner = f"{Path(_config['root']).resolve()}::{label}"
            bindings = [
                ResolutionBinding.from_dict(value)
                for value in self.repository.snapshot()["bindings"].values()
                if owner in (value.get("owners") or ())
            ]
            if bindings:
                binding = next((item for item in bindings if
                                item.name.removeprefix("*.") in hostname), bindings[0])
                spec = self.adapters.get(binding.adapter_id)
                adapter = spec.adapter if spec is not None else None
                base = dict(
                    state="drifted", hostname=hostname, policy=policy,
                    observation=observation, expected=expected, fallback=fallback,
                    mutated=False, ownership="residual", health="degraded",
                )
                if spec is None or observation.manager not in spec.managers:
                    return self._result(
                        **base, reason_code="resolver_owner_changed",
                        message="The active resolver no longer matches the owned binding.",
                    )
                observed = (
                    self.binding_observer(binding, adapter)
                    if self.binding_observer is not None and adapter is not None else None
                )
                if observed is None:
                    return self._result(
                        **base, reason_code="binding_unavailable",
                        message="The owned resolver binding could not be observed.",
                    )
                if canonical_digest(observed) != binding.last_applied_digest:
                    return self._result(
                        **base, reason_code="binding_drifted",
                        message="The observed resolver binding differs from its owned receipt.",
                    )
                authority = self.authority_observer() if self.authority_observer else None
                if authority is not None and authority.get("health") != "healthy":
                    return self._result(
                        **base, reason_code="authority_unhealthy",
                        message="The scoped answering authority is not healthy.",
                    )
                if set(answers) != set(expected):
                    return self._result(
                        **base, reason_code="answer_mismatch",
                        message="Fresh resolver answers do not match the ingress offer; a stale cache or route is possible.",
                    )
                if expected and not self.verifier(hostname, expected, fallback):
                    return self._result(
                        **base, reason_code="verification_failed",
                        message="DNS matched, but the hostname did not reach the expected ingress.",
                    )
                return self._result(
                    state="ready", hostname=hostname, policy=policy,
                    observation=observation, expected=expected, fallback=fallback,
                    reason_code="ready", message="Owned hostname resolution is healthy.",
                    ok=True, mutated=False, health="healthy", ownership="owned",
                )
        return DomainResult(
            ok=False, state="fallback", hostname=hostname,
            hostname_source=policy["hostnameSource"],
            strategy=policy.get("strategy"),
            strategy_source=policy["strategySource"], resolver=resolver,
            actual_answers=tuple(answers), expected_addresses=tuple(expected), ownership="none",
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

    def _prepare(self, project_dir: str, label: str, offer_override=None):
        try:
            config, policy, hostname, fallback = self._context(project_dir, label)
        except DomainContextError:
            return None, self.status(project_dir, label=label)
        if self.observer is None or self.ingress_offer is None:
            return None, self.status(project_dir, label=label)
        observation = self.observer(hostname)
        offer = offer_override or self.ingress_offer(config["root"], label)
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
        authority_status = self.authority.status() if self.authority is not None and hasattr(
            self.authority, "status"
        ) else {}
        if authority_status.get("address") and authority_status.get("port"):
            address = authority_status["address"]
            port = int(authority_status["port"])
            endpoint_existing = True
        else:
            address, port = self.endpoints.allocate()
            endpoint_existing = False
        target = accepted[0]
        suffix = policy["tld"]
        owner = f"{Path(config['root']).resolve()}::{label}"
        kind = "zone" if policy.get("wildcard") else "exact"
        binding_name = f"*.{hostname}" if kind == "zone" else hostname
        for raw in self.repository.snapshot()["bindings"].values():
            owned = ResolutionBinding.from_dict(raw)
            owned_name = owned.name.removeprefix("*.")
            requested_name = binding_name.removeprefix("*.")
            overlaps = (
                owned.name == binding_name
                or (owned.kind == "zone" and (
                    requested_name == owned_name or requested_name.endswith("." + owned_name)
                ))
                or (kind == "zone" and (
                    owned_name == requested_name or owned_name.endswith("." + requested_name)
                ))
            )
            if overlaps and (owned.target != target or owned.adapter_id != spec.adapter_id):
                return None, self._result(
                    state="foreign_collision", hostname=hostname, policy=policy,
                    observation=observation, expected=accepted, fallback=fallback,
                    reason_code="owned_namespace_collision",
                    message="An existing owned DNS namespace points at another backend.",
                )
        adapter_plan = (
            spec.adapter.plan(suffix, address, port)
            if hasattr(spec.adapter, "plan") and observation.manager == "resolved"
            else {"kind": kind, "hostname": hostname, "address": target,
                  "suffix": suffix, "port": port}
        )
        adapter_plan = {**adapter_plan, "owner_digest": canonical_digest(owner)}
        binding = ResolutionBinding.create(
            kind=kind, name=binding_name, target=target,
            adapter_id=spec.adapter_id, owners=(owner,), desired=adapter_plan,
        )
        return {
            "config": config, "policy": policy, "hostname": hostname,
            "fallback": fallback, "observation": observation, "offer": offer,
            "accepted": accepted, "spec": spec, "address": address, "port": port,
            "endpoint_existing": endpoint_existing,
            "binding": binding, "adapter_plan": {
                **adapter_plan, "hostname": hostname, "target": target,
                "binding_id": binding.binding_id,
                "observation_fingerprint": observation.fingerprint,
            },
        }, None

    def plan(self, project_dir: str, *, label: str = "default",
             offer_override=None) -> DomainResult:
        prepared, result = self._prepare(project_dir, label, offer_override)
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
              interactive: bool = False, offer_override=None) -> DomainResult:
        operation = getattr(self.repository, "operation", None)
        if operation is None:
            return self._apply(project_dir, label=label, interactive=interactive,
                               offer_override=offer_override)
        with operation():
            return self._apply(project_dir, label=label, interactive=interactive,
                               offer_override=offer_override)

    def _apply(self, project_dir: str, *, label: str = "default",
               interactive: bool = False, offer_override=None) -> DomainResult:
        prepared, result = self._prepare(project_dir, label, offer_override)
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
        existing = self.repository.binding(prepared["binding"].binding_id)
        if existing is not None and existing.last_applied is not None:
            if self.authority is None:
                return self._result(
                    state="fallback", hostname=prepared["hostname"],
                    policy=prepared["policy"], observation=current,
                    expected=prepared["accepted"], fallback=prepared["fallback"],
                    reason_code="authority_unavailable",
                    message="The scoped answering authority is unavailable.",
                )
            if hasattr(prepared["spec"].adapter, "ensure_helper"):
                helper = prepared["spec"].adapter.ensure_helper(interactive=interactive)
                if not helper.get("ok"):
                    return self._result(
                        state="pending_privilege", hostname=prepared["hostname"],
                        policy=prepared["policy"], observation=current,
                        expected=prepared["accepted"], fallback=prepared["fallback"],
                        reason_code="resolver_helper_required",
                        message=helper.get("error", "The scoped resolver helper is unavailable."),
                        mutated=bool(helper.get("mutated")),
                    )
            if hasattr(prepared["spec"].adapter, "ensure_authorized"):
                authorized = prepared["spec"].adapter.ensure_authorized(
                    prepared["adapter_plan"], interactive=interactive,
                )
                if not authorized.get("ok"):
                    return self._result(
                        state="pending_privilege", hostname=prepared["hostname"],
                        policy=prepared["policy"], observation=current,
                        expected=prepared["accepted"], fallback=prepared["fallback"],
                        reason_code="resolver_authorization_required",
                        message=authorized.get("error", "Exact resolver authorization is unavailable."),
                        mutated=bool(authorized.get("mutated")),
                    )
            joined_applied = None
            if hasattr(prepared["spec"].adapter, "release_owner"):
                joined_applied = prepared["spec"].adapter.apply(
                    prepared["adapter_plan"],
                )
                if not joined_applied.get("ok"):
                    return self._result(
                        state="cleanup_incomplete" if joined_applied.get("rollback_failed")
                        else "fallback",
                        hostname=prepared["hostname"], policy=prepared["policy"],
                        observation=current, expected=prepared["accepted"],
                        fallback=prepared["fallback"],
                        reason_code="shared_binding_receipt_failed",
                        message=joined_applied.get(
                            "error", "Shared resolver ownership could not be recorded.",
                        ),
                        mutated=bool(joined_applied.get("mutated")),
                    )
            if not self.verifier(
                prepared["hostname"], prepared["accepted"], prepared["fallback"],
            ):
                if joined_applied is not None:
                    rollback = prepared["spec"].adapter.rollback({
                        **prepared["adapter_plan"],
                        "applied": joined_applied.get("applied") or {},
                    })
                    if not rollback.get("ok"):
                        self.repository.put_binding(prepared["binding"])
                        self.repository.put_recovery(CleanupRecovery(
                            prepared["binding"].binding_id,
                            prepared["binding"].adapter_id,
                            existing.last_applied_digest, None,
                            "shared_verification_rollback_failed", None,
                            "unavailable",
                        ))
                        return self._result(
                            state="cleanup_incomplete", hostname=prepared["hostname"],
                            policy=prepared["policy"], observation=current,
                            expected=prepared["accepted"], fallback=prepared["fallback"],
                            reason_code="shared_verification_rollback_failed",
                            message="Shared verification failed and owner receipt rollback did not complete.",
                            mutated=True, ownership="residual",
                        )
                return self._result(
                    state="drifted", hostname=prepared["hostname"],
                    policy=prepared["policy"], observation=current,
                    expected=prepared["accepted"], fallback=prepared["fallback"],
                    reason_code="shared_binding_unhealthy",
                    message="The existing shared resolver binding failed fresh verification.",
                    mutated=False, health="degraded", ownership="residual",
                )
            self.repository.put_binding(prepared["binding"])
            if self.identity_persister is not None:
                self.identity_persister(
                    prepared["config"]["root"], label, prepared["hostname"],
                    prepared["policy"]["hostnameSource"],
                )
            return self._result(
                state="ready", hostname=prepared["hostname"],
                policy=prepared["policy"], observation=current,
                expected=prepared["accepted"], fallback=prepared["fallback"],
                reason_code="shared_binding_joined",
                message="Joined an existing healthy owned resolver binding.",
                ok=True, mutated=True, health="healthy", ownership="shared",
            )
        if self.authority is None:
            return self._result(
                state="fallback", hostname=prepared["hostname"],
                policy=prepared["policy"], observation=current,
                expected=prepared["accepted"], fallback=prepared["fallback"],
                reason_code="authority_unavailable",
                message="The scoped answering authority is unavailable.",
            )
        if hasattr(prepared["spec"].adapter, "ensure_helper"):
            helper = prepared["spec"].adapter.ensure_helper(interactive=interactive)
            if not helper.get("ok"):
                return self._result(
                    state="pending_privilege", hostname=prepared["hostname"],
                    policy=prepared["policy"], observation=current,
                    expected=prepared["accepted"], fallback=prepared["fallback"],
                    reason_code="resolver_helper_required",
                    message=helper.get("error", "The scoped resolver helper is unavailable."),
                    mutated=bool(helper.get("mutated")),
                )
        if hasattr(prepared["spec"].adapter, "ensure_authorized"):
            authorized = prepared["spec"].adapter.ensure_authorized(
                prepared["adapter_plan"], interactive=interactive,
            )
            if not authorized.get("ok"):
                return self._result(
                    state="pending_privilege", hostname=prepared["hostname"],
                    policy=prepared["policy"], observation=current,
                    expected=prepared["accepted"], fallback=prepared["fallback"],
                    reason_code="resolver_authorization_required",
                    message=authorized.get(
                        "error", "Exact resolver authorization is unavailable.",
                    ),
                    mutated=bool(authorized.get("mutated")),
                )
        existing_bindings = tuple(
            ResolutionBinding.from_dict(value)
            for value in self.repository.snapshot()["bindings"].values()
        )
        reservation = None
        if not prepared["endpoint_existing"] and hasattr(self.endpoints, "reserve"):
            try:
                reservation = self.endpoints.reserve(prepared["port"])
            except (OSError, RuntimeError, ValueError) as exc:
                if hasattr(prepared["spec"].adapter, "revoke_authorization"):
                    prepared["spec"].adapter.revoke_authorization(prepared["adapter_plan"])
                return self._result(
                    state="foreign_collision", hostname=prepared["hostname"],
                    policy=prepared["policy"], observation=current,
                    expected=prepared["accepted"], fallback=prepared["fallback"],
                    reason_code="authority_endpoint_collision", message=str(exc),
                )
        try:
            self.authority.ensure(
                (*existing_bindings, prepared["binding"]),
                address=prepared["address"], port=prepared["port"],
                reservation=reservation,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            if hasattr(prepared["spec"].adapter, "revoke_authorization"):
                prepared["spec"].adapter.revoke_authorization(
                    prepared["adapter_plan"],
                )
            return self._result(
                state="foreign_collision", hostname=prepared["hostname"],
                policy=prepared["policy"], observation=current,
                expected=prepared["accepted"], fallback=prepared["fallback"],
                reason_code="authority_endpoint_changed",
                message=str(exc), mutated=False,
            )
        finally:
            if reservation is not None:
                try:
                    reservation.release()
                except OSError:
                    pass
        applied = prepared["spec"].adapter.apply(prepared["adapter_plan"])
        if not applied.get("ok"):
            rollback_failed = bool(applied.get("rollback_failed"))
            if rollback_failed:
                residual = prepared["binding"].with_applied(
                    applied.get("applied") or prepared["binding"].desired,
                )
                self.repository.put_binding(residual)
                self.repository.put_recovery(CleanupRecovery(
                    prepared["binding"].binding_id,
                    prepared["binding"].adapter_id,
                    residual.last_applied_digest, None,
                    "resolver_apply_rollback_failed", None, "unavailable",
                ))
            else:
                try:
                    authority_removed = self.authority.remove(
                        prepared["binding"].binding_id,
                    )
                except (OSError, RuntimeError, ValueError):
                    authority_removed = False
                if not authority_removed:
                    rollback_failed = True
                    residual = prepared["binding"].with_applied(
                        applied.get("applied") or prepared["binding"].desired,
                    )
                    self.repository.put_binding(residual)
                    self.repository.put_recovery(CleanupRecovery(
                        residual.binding_id, residual.adapter_id,
                        residual.last_applied_digest, None,
                        "authority_cleanup_failed", None, "unavailable",
                    ))
            return self._result(
                state="cleanup_incomplete" if rollback_failed else "fallback",
                hostname=prepared["hostname"], policy=prepared["policy"],
                observation=current, expected=prepared["accepted"], fallback=prepared["fallback"],
                reason_code=(
                    "authority_cleanup_failed"
                    if rollback_failed and not applied.get("rollback_failed")
                    else ("resolver_apply_rollback_failed" if rollback_failed
                          else "resolver_apply_failed")
                ),
                message=applied.get("error", "Resolver apply failed."),
                mutated=bool(applied.get("mutated")) or rollback_failed,
                ownership="residual" if rollback_failed else "none",
            )
        plan = {**prepared["adapter_plan"], "applied": applied.get("applied") or {}}
        if not self.verifier(prepared["hostname"], prepared["accepted"], prepared["fallback"]):
            rollback = prepared["spec"].adapter.rollback(plan)
            if not rollback.get("ok"):
                self.repository.put_binding(
                    prepared["binding"].with_applied(applied.get("applied") or {}),
                )
                self.repository.put_recovery(CleanupRecovery(
                    prepared["binding"].binding_id,
                    prepared["binding"].adapter_id,
                    canonical_digest(prepared["binding"].desired), None,
                    "verification_rollback_failed", None, "unavailable",
                ))
                return self._result(
                    state="cleanup_incomplete", hostname=prepared["hostname"],
                    policy=prepared["policy"], observation=current,
                    expected=prepared["accepted"], fallback=prepared["fallback"],
                    reason_code="verification_rollback_failed",
                    message="Verification failed and resolver rollback did not complete; recovery was retained.",
                    mutated=True, ownership="residual",
                )
            try:
                authority_removed = self.authority.remove(
                    prepared["binding"].binding_id,
                )
            except (OSError, RuntimeError, ValueError):
                authority_removed = False
            if not authority_removed:
                residual = prepared["binding"].with_applied(applied.get("applied") or {})
                self.repository.put_binding(residual)
                self.repository.put_recovery(CleanupRecovery(
                    residual.binding_id, residual.adapter_id,
                    residual.last_applied_digest, None,
                    "authority_cleanup_failed", None, "unavailable",
                ))
                return self._result(
                    state="cleanup_incomplete", hostname=prepared["hostname"],
                    policy=prepared["policy"], observation=current,
                    expected=prepared["accepted"], fallback=prepared["fallback"],
                    reason_code="authority_cleanup_failed",
                    message="Verification failed and authority cleanup did not complete; recovery was retained.",
                    mutated=True, ownership="residual",
                )
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
        operation = getattr(self.repository, "operation", None)
        if operation is None:
            return self._cleanup(project_dir, label=label, interactive=interactive)
        with operation():
            return self._cleanup(project_dir, label=label, interactive=interactive)

    def _cleanup(self, project_dir: str, *, label: str = "default",
                 interactive: bool = False) -> DomainResult:
        try:
            config, policy, hostname, fallback = self._context(project_dir, label)
        except DomainContextError:
            # Cleanup receipts intentionally outlive the instance registry row.
            # Recover identity from the project root plus persisted binding so a
            # failed cleanup remains independently retryable after deletion.
            config = self.config_loader(project_dir, label=label)
            owner = f"{Path(config['root']).resolve()}::{label}"
            retained = [
                ResolutionBinding.from_dict(value)
                for value in self.repository.snapshot()["bindings"].values()
                if owner in (value.get("owners") or ())
            ]
            if not retained:
                return self.status(project_dir, label=label)
            policy = config["domains"]
            hostname = retained[0].name.removeprefix("*.")
            fallback = ""
        observation = self.observer(hostname) if self.observer else None
        if observation is None:
            return self.status(project_dir, label=label)
        owner = f"{Path(config['root']).resolve()}::{label}"
        bindings = [
            ResolutionBinding.from_dict(value)
            for value in self.repository.snapshot()["bindings"].values()
            if owner in (value.get("owners") or ())
        ]
        if not bindings:
            return self._result(
                state="ready", hostname=hostname, policy=policy,
                observation=observation, expected=(), fallback=fallback,
                reason_code="already_absent", message="No owned resolver binding remains.",
                ok=True, mutated=False, health="fallback", ownership="none",
            )
        incomplete = False
        mutated = False
        for binding in bindings:
            recovery = self.repository.snapshot()["recovery"].get(binding.binding_id) or {}
            if recovery.get("reason_code") == "authority_cleanup_failed":
                try:
                    removed = self.authority is not None and self.authority.remove(
                        binding.binding_id,
                    )
                except (OSError, RuntimeError, ValueError):
                    removed = False
                if not removed:
                    incomplete = True
                    continue
                self.repository.remove_binding_if_unchanged(
                    binding.binding_id, binding.last_applied_digest,
                )
                mutated = True
                continue
            spec = self.adapters.get(binding.adapter_id)
            adapter = spec.adapter if spec is not None else None
            observed = (
                self.binding_observer(binding, adapter)
                if self.binding_observer is not None and adapter is not None else None
            )
            if observed is None or adapter is None:
                self.repository.put_recovery(CleanupRecovery(
                    binding.binding_id, binding.adapter_id,
                    binding.last_applied_digest or canonical_digest(binding.desired),
                    None, "resolver_unavailable", None, "unavailable",
                ))
                incomplete = True
                continue
            observed_digest = canonical_digest(observed)
            if observed_digest != binding.last_applied_digest:
                self.repository.remove_binding_if_unchanged(
                    binding.binding_id, observed_digest,
                )
                incomplete = True
                continue
            if len(binding.owners) > 1 and not hasattr(adapter, "release_owner"):
                if self.repository.release_binding_owner(binding.binding_id, owner) == "retained":
                    mutated = True
                    continue
            all_bindings = tuple(
                ResolutionBinding.from_dict(value)
                for value in self.repository.snapshot()["bindings"].values()
            )
            shared_route = any(
                item.binding_id != binding.binding_id
                and item.adapter_id == binding.adapter_id
                and item.desired.get("suffix") == binding.desired.get("suffix")
                for item in all_bindings
            )
            result = None
            if hasattr(adapter, "release_owner"):
                result = adapter.release_owner(binding, canonical_digest(owner))
            elif not shared_route:
                result = adapter.cleanup(binding)
            if result is not None:
                if not result.get("ok"):
                    self.repository.put_recovery(CleanupRecovery(
                        binding.binding_id, binding.adapter_id,
                        binding.last_applied_digest, observed_digest,
                        "resolver_cleanup_failed", None, "unavailable",
                    ))
                    incomplete = True
                    continue
            if len(binding.owners) > 1:
                if self.repository.release_binding_owner(binding.binding_id, owner) == "retained":
                    mutated = True
                    continue
            if self.authority is not None:
                try:
                    authority_removed = self.authority.remove(binding.binding_id)
                except (OSError, RuntimeError, ValueError):
                    authority_removed = False
                if not authority_removed:
                    self.repository.put_recovery(CleanupRecovery(
                        binding.binding_id, binding.adapter_id,
                        binding.last_applied_digest, observed_digest,
                        "authority_cleanup_failed", None, "unavailable",
                    ))
                    incomplete = True
                    continue
            self.repository.remove_binding_if_unchanged(binding.binding_id, observed_digest)
            mutated = True
        if incomplete:
            return self._result(
                state="cleanup_incomplete", hostname=hostname, policy=policy,
                observation=observation, expected=(), fallback=fallback,
                reason_code="cleanup_incomplete",
                message="One or more resolver bindings drifted or were unavailable; retry state was retained.",
                mutated=mutated, ownership="residual",
            )
        return self._result(
            state="ready", hostname=hostname, policy=policy,
            observation=observation, expected=(), fallback=fallback,
            reason_code="cleanup_complete", message="Owned resolver bindings were removed.",
            ok=True, mutated=mutated, ownership="none",
        )

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
