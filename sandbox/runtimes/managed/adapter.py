"""Fail-closed managed-native runtime adapter; no Compose/host fallback exists."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sandbox.isolation.models import (
    EgressGrantSet, ManagedIsolationPolicy, canonical_digest,
)
from sandbox.isolation.network import ABSENT_GRANT_DIGEST
from sandbox.runtimes.base import ExecutionRequest, OperationResult


_PROOF_CANDIDATE_SEAL = object()


class _ManagedProofCandidateAuthority:
    """Invocation-local authority for the reviewed Ubuntu proof candidate.

    The value cannot be reconstructed from transport data.  Only the
    composition root converts the exact host-side proof opt-in into this
    authority; it is never accepted in an operation request or persisted.
    """

    __slots__ = ("_seal",)

    def __init__(self, seal):
        if seal is not _PROOF_CANDIDATE_SEAL:
            raise ValueError("managed proof candidate authority is invalid")
        self._seal = seal

    def __reduce__(self):
        raise TypeError("managed proof candidate authority is not serializable")


def _proof_candidate_authority(value):
    """Convert only the exact reviewed environment opt-in into authority."""
    if value != "ubuntu-24.04-systemd-255":
        return None
    return _ManagedProofCandidateAuthority(_PROOF_CANDIDATE_SEAL)


def _is_proof_candidate_authority(value):
    return (type(value) is _ManagedProofCandidateAuthority
            and value._seal is _PROOF_CANDIDATE_SEAL)


@dataclass(frozen=True)
class ManagedRuntimeDependencies:
    """Mechanisms injected at the managed-native composition root.

    Keeping these mechanisms together makes the native adapter's authority
    explicit.  The adapter must never obtain a Compose facade (or a host-global
    service) as a fallback when managed-native was selected.
    """

    process: Any
    http: Any
    paths: Any
    registry: Any
    isolation: Any
    packages: Any
    network: Any
    database: Any
    services: Any | None = None
    credentials: Any | None = None
    plan_builder: Any | None = None
    provisioner: Any | None = None
    verifier: Any | None = None
    launcher: Any | None = None
    cleanup: Any | None = None
    grants: Any | None = None


class ManagedNativeCleanup:
    """Remove only observed, digest-bound managed-native resources.

    The coordinator deliberately knows nothing about hostname routes or shared
    packages.  Those belong to ingress and package-review workflows respectively.
    Every destructive action is preceded by an observation supplied by the
    composition root; an unavailable observer is a retained recovery item, not
    permission to make an educated guess.
    """

    ORDER = ("services", "database", "machine", "network", "mount", "image", "policy")

    def __init__(self, *, repository, services, database, network, machine, image,
                 policy, observe):
        self.repository = repository
        self.services = services
        self.database = database
        self.network = network
        self.machine = machine
        self.image = image
        self.policy = policy
        self.observe = observe

    @staticmethod
    def _owner(request):
        return {"project_root": str(Path(request.project_root).expanduser().absolute()),
                "label": request.label}

    @staticmethod
    def _same_owner(value, owner):
        return value == owner or value == f"{owner['project_root']}::{owner['label']}"

    @staticmethod
    def _public_recovery(name, reason, *, expected=None, observed=None):
        # Digests are safe correlation values; the underlying state may contain
        # paths or credential references and is intentionally never emitted.
        return {"object_type": name, "reason_code": reason, "retry_state": "pending",
                "expected_digest": expected, "observed_digest": observed}

    def _retain(self, machine_id, owner, name, reason, *, expected=None, observed=None):
        recovery = self._public_recovery(name, reason, expected=expected, observed=observed)
        self.repository.put_recovery(
            f"cleanup:{machine_id}:{name}",
            {"owner": owner, "identity": machine_id, **recovery},
        )
        return recovery

    def _clear_owned_recovery(self, key, machine_id, owner):
        """Retire one unchanged recovery record only when attribution matches."""
        record = self.repository.snapshot()["recovery"].get(key)
        if not isinstance(record, dict):
            return "absent"
        if (not self._same_owner(record.get("owner"), owner)
                or record.get("identity") != machine_id):
            return "foreign"
        return self.repository.remove_recovery_if_unchanged(key, record)

    def _clear_resource_recovery(self, machine_id, owner, name):
        """Retire only this owner's unchanged resource-specific cleanup record."""
        return self._clear_owned_recovery(f"cleanup:{machine_id}:{name}", machine_id, owner)

    def _clear_safe_cleanup_recovery(self, machine_id, owner):
        """Remove stale, exact cleanup records after a proven converged state.

        Recovery entries that lack this machine identity or belong to another
        owner are deliberately retained.  They are evidence, not disposable
        bookkeeping.
        """
        for name in (*self.ORDER, "state"):
            self._clear_resource_recovery(machine_id, owner, name)

    @staticmethod
    def _outcome(value):
        if isinstance(value, dict):
            return bool(value.get("ok")), bool(value.get("mutated"))
        return bool(value), bool(value)

    def cleanup(self, request, plan):
        """Compare resource observations then remove in dependency-safe order.

        ``plan['cleanup']`` contains one entry per resource with a secret-free
        expected observation and the component-specific action plan.  ``observe``
        must return the corresponding observation.  This explicit contract keeps
        cleanup fail-closed when a helper or machine is unavailable.
        """
        if not isinstance(plan, dict) or not isinstance(plan.get("policy"), ManagedIsolationPolicy):
            return {"ok": False, "state": "cleanup_incomplete", "mutated": False,
                    "cleanup": {"complete": False, "residual": ("state",)},
                    "reason": {"code": "cleanup_plan_unavailable"}}
        machine_id = plan["policy"].machine_id
        owner = self._owner(request)
        entries = plan.get("cleanup")
        if not isinstance(entries, dict):
            self._retain(machine_id, owner, "state", "cleanup_plan_unavailable")
            return {"ok": False, "state": "cleanup_incomplete", "mutated": True,
                    "cleanup": {"complete": False, "residual": ("state",)},
                    "reason": {"code": "cleanup_plan_unavailable"}}

        # Refuse foreign identity before stopping even an otherwise matching
        # service: a matching machine name alone never proves C ownership.
        state = self.repository.snapshot()
        owned_records = []
        for section in ("backends", "policies", "networks"):
            record = state[section].get(machine_id)
            if record is not None and not self._same_owner(record.get("owner"), owner):
                self._retain(machine_id, owner, "state", "foreign_ownership_collision")
                return {"ok": False, "state": "cleanup_incomplete", "mutated": False,
                        "cleanup": {"complete": False, "residual": ("state",)},
                        "reason": {"code": "cleanup_incomplete"}}
            if record is not None:
                owned_records.append(record)
        if not owned_records:
            self._clear_safe_cleanup_recovery(machine_id, owner)
            self._clear_owned_recovery(f"cleanup-progress:{machine_id}", machine_id, owner)
            return {"ok": True, "state": "ready", "mutated": False,
                    "cleanup": {"complete": True, "removed": (), "residual": ()},
                    "reason": {"code": "cleanup_complete"}}

        components = {
            "services": (self.services, "stop"), "database": (self.database, "remove"),
            "network": (self.network, "remove"), "machine": (self.machine, "stop"),
            "mount": (self.image, "unmount"), "image": (self.image, "remove"),
            "policy": (self.policy, "remove"),
        }
        progress_key = f"cleanup-progress:{machine_id}"
        progress = self.repository.snapshot()["recovery"].get(progress_key, {})
        prior_removed = tuple(progress.get("removed", ())) if (
            isinstance(progress, dict)
            and self._same_owner(progress.get("owner"), owner)
            and progress.get("identity") == machine_id
        ) else ()
        if any(name not in self.ORDER for name in prior_removed): prior_removed = ()
        # Later, exact removals can prove earlier runtime objects absent after a
        # lost response. This lets retry advance to the true residual without
        # treating observer unavailability as proof. Policy removal is strongest:
        # the helper refuses it while any managed runtime resource remains.
        proven_removed = set(prior_removed)
        if "policy" in proven_removed:
            proven_removed.update(self.ORDER)
        if "image" in proven_removed:
            proven_removed.update(("services", "database", "mount", "image"))
        if "machine" in proven_removed:
            proven_removed.update(("services", "machine"))
        prior_removed = tuple(name for name in self.ORDER if name in proven_removed)
        removed, residual, mutated = list(prior_removed), [], False
        for name in self.ORDER:
            if name in prior_removed:
                self._clear_resource_recovery(machine_id, owner, name)
                continue
            entry = entries.get(name)
            component, verb = components[name]
            if not isinstance(entry, dict) or not isinstance(entry.get("expected"), dict):
                residual.append(name)
                self._retain(machine_id, owner, name, "cleanup_observation_unavailable")
                break
            expected = entry["expected"]
            expected_digest = canonical_digest(expected)
            try:
                observed = self.observe(name, entry["plan"])
            except (OSError, RuntimeError, TypeError, ValueError):
                observed = None
            if not isinstance(observed, dict):
                residual.append(name)
                self._retain(machine_id, owner, name, "runtime_unavailable",
                             expected=expected_digest)
                break
            observed_digest = canonical_digest(observed)
            if observed_digest != expected_digest:
                residual.append(name)
                self._retain(machine_id, owner, name, "owned_state_drifted",
                             expected=expected_digest, observed=observed_digest)
                break
            action = getattr(component, verb, None)
            if not callable(action):
                residual.append(name)
                self._retain(machine_id, owner, name, "cleanup_unsupported",
                             expected=expected_digest, observed=observed_digest)
                break
            try:
                ok, changed = self._outcome(action(entry["plan"]))
            except (OSError, RuntimeError, TypeError, ValueError):
                ok, changed = False, False
            mutated = mutated or changed
            if not ok:
                residual.append(name)
                self._retain(machine_id, owner, name, "cleanup_failed",
                             expected=expected_digest, observed=observed_digest)
                break
            removed.append(name)
            self._clear_resource_recovery(machine_id, owner, name)
            self.repository.put_recovery(progress_key, {
                "owner": owner, "object_type": "cleanup_progress",
                "identity": machine_id, "reason_code": "cleanup_in_progress",
                "retry_state": "pending", "removed": tuple(removed),
            })

        if residual:
            return {"ok": False, "state": "cleanup_incomplete", "mutated": mutated,
                    "cleanup": {"complete": False, "removed": tuple(removed),
                                "residual": tuple(residual)},
                    "reason": {"code": "cleanup_incomplete"}}

        # Local identity is removed last, after every owned runtime object is gone.
        # Re-read here: the preflight snapshot must not authorize deletion if
        # another control-plane actor changed local attribution meanwhile.
        state = self.repository.snapshot()
        for section in ("backends", "policies", "networks"):
            record = state[section].get(machine_id)
            if record is not None and not self._same_owner(record.get("owner"), owner):
                self._retain(machine_id, owner, "state", "foreign_ownership_collision")
                return {"ok": False, "state": "cleanup_incomplete", "mutated": mutated,
                        "cleanup": {"complete": False, "removed": tuple(removed),
                                    "residual": ("state",)},
                        "reason": {"code": "cleanup_incomplete"}}
        for section in ("backends", "policies", "networks"):
            record = state[section].get(machine_id)
            if record is None:
                continue
            observed = {key: value for key, value in record.items() if key != "last_applied"}
            status = self.repository.remove_if_unchanged(section, machine_id, observed)
            if status == "drifted":
                self._retain(machine_id, owner, "state", "owned_state_drifted")
                return {"ok": False, "state": "cleanup_incomplete", "mutated": mutated,
                        "cleanup": {"complete": False, "removed": tuple(removed),
                                    "residual": ("state",)},
                        "reason": {"code": "cleanup_incomplete"}}
        self._clear_owned_recovery(progress_key, machine_id, owner)
        self._clear_safe_cleanup_recovery(machine_id, owner)
        return {"ok": True, "state": "ready", "mutated": mutated or bool(removed),
                "cleanup": {"complete": True, "removed": tuple((*removed, "state")),
                            "residual": ()},
                "reason": {"code": "cleanup_complete"}}


class ManagedProvisioner:
    """Provision infrastructure first; activate project services only after proof."""
    def __init__(self, *, policy, apparmor, image, rootfs, machine, network, verifier,
                 credentials, database, services, health, repository, wordpress=None,
                 grants=None):
        self.policy = policy; self.apparmor = apparmor
        self.image = image; self.rootfs = rootfs
        self.machine = machine; self.network = network; self.verifier = verifier
        self.credentials = credentials; self.database = database
        self.wordpress = wordpress
        self.services = services; self.health = health
        self.repository = repository
        self.grants = grants

    @staticmethod
    def _rollback_owner(plan):
        record = plan.get("record")
        return record.get("owner") if isinstance(record, dict) else None

    def _retain_rollback(self, plan, step, *, expected=None, observed=None):
        record = {
            "owner": self._rollback_owner(plan),
            "object_type": step,
            "identity": plan["machine_id"],
            "reason_code": f"provision_rollback_{step}_failed",
            "retry_state": "pending",
        }
        if expected is not None:
            record["expected_digest"] = expected
        if observed is not None:
            record["observed_digest"] = observed
        self.repository.put_recovery(
            f"provision:{plan['machine_id']}:{step}", record,
        )

    def _clear_rollback(self, plan, step):
        remove = getattr(self.repository, "remove_recovery", None)
        if callable(remove):
            remove(f"provision:{plan['machine_id']}:{step}")

    def _persist_incomplete_plan(self, plan, rollback):
        policy = plan["policy"]
        record = {
            "owner": self._rollback_owner(plan),
            "policy": policy.to_dict(),
            "cleanup": plan.get("cleanup", {}),
        }
        record["last_applied"] = canonical_digest(record)
        self.repository.put_owned("policies", plan["machine_id"], record)
        removed = tuple(
            item["step"] for item in rollback
            if item.get("ok") and item.get("step") in ManagedNativeCleanup.ORDER
        )
        self.repository.put_recovery(f"cleanup-progress:{plan['machine_id']}", {
            "owner": self._rollback_owner(plan),
            "object_type": "cleanup_progress",
            "identity": plan["machine_id"],
            "reason_code": "cleanup_in_progress",
            "retry_state": "pending",
            "removed": removed,
        })

    def _rollback_step(self, rollback, plan, step, operation, *, expected=None,
                       observed=None):
        try:
            result = operation()
        except Exception as exc:
            result = {"ok": False, "mutated": False, "error": str(exc)}
        if not isinstance(result, dict):
            result = {"ok": False, "mutated": False,
                      "error": "rollback mechanism returned an invalid result"}
        item = {"step": step, **result}
        rollback.append(item)
        if item.get("ok"):
            try:
                self._clear_rollback(plan, step)
            except Exception:
                # A stale recovery record is conservative; it must not prevent
                # later rollback steps from running.
                item["recovery_clear_failed"] = True
        else:
            try:
                self._retain_rollback(
                    plan, step, expected=expected, observed=observed,
                )
            except Exception:
                item["recovery_persisted"] = False
        return item

    @staticmethod
    def _keep_failed_machine() -> bool:
        """True only when a proof run explicitly asked to retain a failure."""
        import os

        return bool(os.environ.get("SANDBOX_NATIVE_PROOF_CANDIDATE")
                    and os.environ.get("SANDBOX_NATIVE_KEEP_FAILED") == "1")

    def ensure(self, plan):
        completed = []
        try:
            # Installation errors are mutation-uncertain. Record the step before
            # invocation so rollback still performs the policy adapter's CAS
            # removal instead of abandoning a possibly installed policy.
            completed.append("policy")
            self.policy.install(plan["policy"])
            completed.append("apparmor"); self.apparmor.install(plan["apparmor"])
            completed.append("image"); self.image.create(plan["image"])
            completed.append("mount"); self.image.mount(plan["image"])
            completed.append("rootfs"); self.rootfs.configure(plan)
            unmounted = self.image.unmount(plan["image"])
            if isinstance(unmounted, dict) and not unmounted.get("ok"):
                raise RuntimeError("managed image could not be closed after provisioning")
            completed.remove("mount")
            # Start only init/network. Web, PHP, database and cron remain masked,
            # so project files cannot execute before effective isolation proof.
            completed.append("machine"); self.machine.start_minimal(plan)
            completed.append("network"); self.network.apply(plan["network"])
            verified = self.verifier.verify(plan["policy"])
            if not verified.get("ok"):
                # Name the failing checks. "verification failed" alone gave the
                # operator nothing to act on, and the verifier already knows
                # exactly which effective control did not hold.
                failed = [name for name, value in (verified.get("checks") or {}).items()
                          if value is False] or verified.get("reason") or "no detail"
                raise RuntimeError(f"effective isolation verification failed: {failed}")
            completed.append("credentials")
            installed = self.credentials.install(
                machine_id=plan["machine_id"],
                policy_digest=plan["policy"].digest,
                references=plan["database"]["credential_refs"],
            )
            if not installed:
                raise RuntimeError("managed credential installation failed")
            completed.append("database")
            database = self.database.initialize(plan["database"])
            if not isinstance(database, dict) or not database.get("ok"):
                raise RuntimeError("managed database bootstrap failed")
            if self.wordpress is not None:
                completed.append("wordpress")
                wordpress = self.wordpress.initialize(plan["wordpress"])
                if not isinstance(wordpress, dict) or not wordpress.get("ok"):
                    raise RuntimeError("managed WordPress bootstrap failed")
            completed.append("services")
            services = self.services.activate(plan["services"])
            if not isinstance(services, dict) or not services.get("ok"):
                raise RuntimeError("managed service activation failed")
            grant_set = plan.get("grant_set")
            active_grants = bool(isinstance(grant_set, EgressGrantSet) and
                                 any(not grant.revoked for grant in grant_set.grants))
            if self.grants is None:
                if active_grants:
                    raise RuntimeError("managed egress reconciliation is unavailable")
            else:
                # A lost/error response can follow an applied helper mutation.
                # Treat an attempted active grant set as present until a
                # digest-bound reconciliation to the canonical empty set proves
                # otherwise.
                if active_grants:
                    completed.append("grants")
                reconciled = self.grants.reconcile(
                    plan["policy"], grant_set, expected_digest=ABSENT_GRANT_DIGEST,
                )
                if not isinstance(reconciled, dict) or not reconciled.get("ok"):
                    raise RuntimeError("managed egress reconciliation failed")
            health = self.health(plan)
            if not health.get("ok"): raise RuntimeError("managed backend health failed")
            self.repository.put_owned("backends", plan["machine_id"], plan["record"])
            return {"ok": True, "state": "ready", "mutated": True,
                    "backend": plan["services"]["backend"], "health": health}
        except Exception as exc:
            if self._keep_failed_machine():
                # Live-proof escape hatch: keep the half-provisioned machine so
                # the failing control can be inspected in place. Only honoured
                # during an explicit proof-candidate run, never in normal use,
                # and it reports loudly that host state was left behind.
                #
                # The cleanup plan is persisted first regardless. Skipping it
                # left the retained machine, its profile and its firewall table
                # with nothing able to remove them: destroy answered
                # `cleanup_plan_unavailable` and the next ensure refused with
                # drifted owned state, so the operator had to delete host
                # objects by hand.
                self._persist_incomplete_plan(plan, [])
                return {"ok": False, "state": "blocked", "mutated": True,
                        "machine_id": plan.get("machine_id"),
                        "completed": tuple(completed),
                        "reason": {"code": "managed_ensure_failed_machine_retained",
                                   "message": f"{exc}; machine {plan.get('machine_id')} "
                                              "was left running for inspection — "
                                              "run `./sb native cleanup` when done"}}
            rollback = []
            if "services" in completed:
                self._rollback_step(
                    rollback, plan, "services",
                    lambda: self.services.stop(plan["services"]),
                )
            if "database" in completed:
                self._rollback_step(
                    rollback, plan, "database",
                    lambda: self.database.remove(plan["database"]),
                )
            if "network" in completed and hasattr(self.network, "deactivate"):
                self._rollback_step(
                    rollback, plan, "network_deactivate",
                    lambda: self.network.deactivate(plan["network"]),
                )
            if "machine" in completed:
                self._rollback_step(
                    rollback, plan, "machine", lambda: self.machine.stop(plan),
                )
            grant_set = plan.get("grant_set")
            active_grants = bool(
                "grants" in completed and isinstance(grant_set, EgressGrantSet)
                and any(not grant.revoked for grant in grant_set.grants)
            )
            if active_grants:
                empty_grants = EgressGrantSet(
                    plan["policy"].machine_id, plan["policy"].digest,
                )
                grant_rollback = self._rollback_step(
                    rollback, plan, "grants",
                    lambda: self.grants.reconcile(
                        plan["policy"], empty_grants,
                        expected_digest=grant_set.digest,
                    ),
                    expected=grant_set.digest, observed=grant_set.digest,
                )
                if not grant_rollback.get("ok"):
                    persist = getattr(self.repository, "put_grants_if_expected", None)
                    if callable(persist):
                        try:
                            status = persist(
                                plan["machine_id"], owner=self._rollback_owner(plan),
                                policy_digest=plan["policy"].digest,
                                expected_digest=ABSENT_GRANT_DIGEST,
                                grant_set=grant_set,
                            )
                            grant_rollback["grant_state"] = status
                        except Exception:
                            grant_rollback["grant_state"] = "unavailable"
            if "network" in completed:
                self._rollback_step(
                    rollback, plan, "network",
                    lambda: self.network.remove(plan["network"]),
                )
            if "mount" in completed:
                self._rollback_step(
                    rollback, plan, "mount", lambda: self.image.unmount(plan["image"]),
                )
            if "image" in completed:
                self._rollback_step(
                    rollback, plan, "image", lambda: self.image.remove(plan["image"]),
                )
            if "apparmor" in completed:
                self._rollback_step(
                    rollback, plan, "apparmor",
                    lambda: self.apparmor.remove(plan["apparmor"]),
                )
            if "policy" in completed:
                self._rollback_step(
                    rollback, plan, "policy",
                    lambda: self.policy.remove({
                        "machine_id": plan["machine_id"],
                        "policy_digest": plan["policy"].digest,
                    }),
                )
            complete = all(item.get("ok", False) for item in rollback)
            if not complete:
                try:
                    self._persist_incomplete_plan(plan, rollback)
                except Exception:
                    rollback.append({
                        "step": "state", "ok": False, "mutated": False,
                        "error": "incomplete cleanup plan could not be persisted",
                    })
            return {"ok": False, "state": "rollback_complete"
                    if complete else "rollback_incomplete",
                    "mutated": bool(completed), "error": str(exc), "completed": completed,
                    "rollback": rollback}


class ManagedNativeAdapter:
    adapter_id = "ubuntu-nspawn"
    capabilities = frozenset({
        "preflight", "ensure", "status", "health", "open", "wordpress_cli",
        "exec", "test", "apply", "destroy",
    })

    def __init__(self, *, preflight, repository, dependencies=None, launcher=None,
                 evidence_id=None, proof_candidate_authority=None):
        self.preflight = preflight
        self.repository = repository
        self.dependencies = dependencies
        self.launcher = launcher
        self.evidence_id = evidence_id
        self.proof_candidate = _is_proof_candidate_authority(
            proof_candidate_authority,
        )

    @staticmethod
    def _owner(request):
        return {"project_root": str(Path(request.project_root).expanduser().absolute()),
                "label": request.label}

    @staticmethod
    def _same_owner(value, owner):
        return value == owner or value == f"{owner['project_root']}::{owner['label']}"

    def _owned_state(self, request):
        owner = self._owner(request); state = self.repository.snapshot()
        backend = next((value for value in state["backends"].values()
                        if self._same_owner(value.get("owner"), owner)), None)
        policy_record = next((value for value in state["policies"].values()
                              if self._same_owner(value.get("owner"), owner)), None)
        policy = None
        if policy_record is not None:
            try:
                policy = ManagedIsolationPolicy(**dict(policy_record["policy"]))
            except (KeyError, TypeError, ValueError):
                policy = None
        return owner, backend, policy, state

    def _persisted_grants(self, owner, state, policy):
        """Load only this instance's reconciled, digest-bound grant document."""
        if policy is None:
            return None
        record = state.get("grants", {}).get(policy.machine_id)
        if record is None:
            return EgressGrantSet(policy.machine_id, policy.digest)
        if not self._same_owner(record.get("owner"), owner):
            return None
        try:
            grants = EgressGrantSet.from_dict(record["grant_set"])
        except (KeyError, TypeError, ValueError):
            return None
        if (grants.machine_id != policy.machine_id or
                grants.base_policy_digest != policy.digest or
                record.get("policy_digest") != policy.digest or
                record.get("grant_digest") != grants.digest):
            return None
        return grants

    @staticmethod
    def _verify(verifier, policy, grants):
        if verifier is None:
            return {"ok": False}
        # Preserve the stable observer API for the no-grants baseline.  An
        # active capability deliberately requires the grant-aware verifier.
        active = bool(grants and any(not grant.revoked for grant in grants.grants))
        return verifier.verify(policy, grants=grants) if active else verifier.verify(policy)

    def _reconcile_grants(self, *, owner, policy, desired, current):
        dependencies = self.dependencies
        reconciler = dependencies.grants if dependencies is not None else None
        if not isinstance(desired, EgressGrantSet):
            return {"ok": False, "mutated": False,
                    "reason": {"code": "egress_grant_set_invalid"}}
        if current is None:
            return {"ok": False, "mutated": False,
                    "reason": {"code": "egress_grant_state_drift"}}
        if desired.digest == current.digest:
            return {"ok": True, "mutated": False, "grant_digest": current.digest}
        if reconciler is None:
            return {"ok": False, "mutated": False,
                    "reason": {"code": "egress_grant_reconciler_unavailable"}}
        try:
            result = reconciler.reconcile(policy, desired, expected_digest=current.digest)
        except (OSError, RuntimeError, TypeError, ValueError):
            return {"ok": False, "mutated": False,
                    "reason": {"code": "egress_grant_reconcile_failed"}}
        if not isinstance(result, dict) or not result.get("ok"):
            return {"ok": False, "mutated": bool(isinstance(result, dict) and result.get("mutated")),
                    "reason": {"code": "egress_grant_reconcile_failed"}}
        status = self.repository.put_grants_if_expected(
            policy.machine_id, owner=owner, policy_digest=policy.digest,
            expected_digest=current.digest, grant_set=desired,
        )
        if status != "stored":
            return {"ok": False, "mutated": True,
                    "reason": {"code": "egress_grant_state_drift"}}
        return {"ok": True, "mutated": bool(result.get("mutated")),
                "grant_digest": desired.digest}

    def _recovery(self, owner, state):
        """Expose only retry-safe recovery metadata (never paths or secrets)."""
        pending = []
        for value in state.get("recovery", {}).values():
            if not isinstance(value, dict) or not self._same_owner(value.get("owner"), owner):
                continue
            pending.append({key: value.get(key) for key in
                            ("object_type", "reason_code", "retry_state")})
        return tuple(pending)

    def _retain_grant_cleanup(self, owner, policy, reason):
        self.repository.put_recovery(f"cleanup:{policy.machine_id}:grants", {
            "owner": owner, "object_type": "grants", "identity": policy.machine_id,
            "reason_code": reason, "retry_state": "pending",
        })

    def _clear_provision_recovery(self, owner, machine_id):
        state = self.repository.snapshot()
        for key, record in state.get("recovery", {}).items():
            if (not key.startswith(f"provision:{machine_id}:")
                    or not isinstance(record, dict)
                    or not self._same_owner(record.get("owner"), owner)
                    or record.get("identity") != machine_id):
                continue
            self.repository.remove_recovery_if_unchanged(key, record)

    def _cleanup_grants(self, owner, policy, state):
        record = state.get("grants", {}).get(policy.machine_id)
        if record is None:
            return {"ok": True, "mutated": False}
        grants = self._persisted_grants(owner, state, policy)
        if grants is None:
            self._retain_grant_cleanup(owner, policy, "egress_grant_state_drift")
            return {"ok": False, "mutated": False}
        empty = EgressGrantSet(policy.machine_id, policy.digest)
        mutated = False
        if grants.digest != empty.digest:
            reconciled = self._reconcile_grants(
                owner=owner, policy=policy, desired=empty, current=grants,
            )
            if not reconciled.get("ok"):
                self._retain_grant_cleanup(
                    owner, policy,
                    reconciled.get("reason", {}).get(
                        "code", "egress_grant_reconcile_failed",
                    ),
                )
                return {"ok": False, "mutated": bool(reconciled.get("mutated"))}
            mutated = bool(reconciled.get("mutated"))
        current = self.repository.snapshot()["grants"].get(policy.machine_id)
        if current is not None:
            observed = {key: value for key, value in current.items()
                        if key != "last_applied"}
            if self.repository.remove_if_unchanged(
                    "grants", policy.machine_id, observed) == "drifted":
                self._retain_grant_cleanup(owner, policy, "egress_grant_state_drift")
                return {"ok": False, "mutated": mutated}
        recovery = self.repository.snapshot()["recovery"].get(
            f"cleanup:{policy.machine_id}:grants",
        )
        if (isinstance(recovery, dict)
                and self._same_owner(recovery.get("owner"), owner)
                and recovery.get("identity") == policy.machine_id):
            self.repository.remove_recovery_if_unchanged(
                f"cleanup:{policy.machine_id}:grants", recovery,
            )
        return {"ok": True, "mutated": mutated}

    def _ensure(self, request):
        dependencies = self.dependencies
        if dependencies is None or not callable(dependencies.plan_builder):
            return {"ok": False, "state": "blocked", "mutated": False,
                    "reason": {"code": "managed_runtime_not_installed"}}
        plan = dependencies.plan_builder(request)
        if not isinstance(plan, dict) or not isinstance(plan.get("policy"), ManagedIsolationPolicy):
            return {"ok": False, "state": "blocked", "mutated": False,
                    "reason": {"code": "isolation_policy_invalid"}}
        policy = plan["policy"]
        if plan.get("machine_id") != policy.machine_id or \
                plan.get("policy_digest", policy.digest) != policy.digest:
            return {"ok": False, "state": "blocked", "mutated": False,
                    "reason": {"code": "isolation_policy_drift"}}
        desired_grants = plan.get("grant_set", EgressGrantSet(policy.machine_id, policy.digest))
        if not isinstance(desired_grants, EgressGrantSet) or \
                desired_grants.base_policy_digest != policy.digest:
            return {"ok": False, "state": "blocked", "mutated": False,
                    "reason": {"code": "egress_grant_set_invalid"}}
        owner, backend, applied, state = self._owned_state(request)
        current_grants = self._persisted_grants(owner, state, applied)
        if backend is not None:
            if (applied is None or applied.digest != policy.digest
                    or backend.get("policy_digest") != policy.digest):
                return {"ok": False, "state": "drifted", "mutated": False,
                        "reason": {"code": "isolation_policy_drift"}}
            verified = self._verify(dependencies.verifier, applied, current_grants)
            if not verified.get("ok"):
                return {"ok": False, "state": "drifted", "mutated": False,
                        "health": verified,
                        "reason": {"code": "isolation_policy_drift"}}
            reconciled = self._reconcile_grants(
                owner=owner, policy=applied, desired=desired_grants,
                current=current_grants,
            )
            if not reconciled.get("ok"):
                return {**reconciled, "state": "drifted",
                        "backend": backend.get("backend")}
            verified = self._verify(dependencies.verifier, applied, desired_grants)
            if not verified.get("ok"):
                return {"ok": False, "state": "drifted", "mutated": reconciled["mutated"],
                        "health": verified,
                        "reason": {"code": "isolation_policy_drift"}}
            return {"ok": True, "state": "ready", "mutated": reconciled["mutated"],
                    "backend": backend.get("backend"), "health": verified,
                    "reason": {"code": "ready"}}
        if dependencies.provisioner is None:
            return {"ok": False, "state": "blocked", "mutated": False,
                    "reason": {"code": "managed_runtime_not_installed"}}
        result = dependencies.provisioner.ensure(plan)
        if result.get("ok"):
            record = {"owner": owner, "policy": policy.to_dict(),
                      "cleanup": plan.get("cleanup", {})}
            record["last_applied"] = canonical_digest(record)
            self.repository.put_owned("policies", policy.machine_id, record)
            # Provisioning has already completed a helper-side reconcile from
            # the canonical empty grant set.  Persist it only after that full
            # transaction succeeds; a failed provision cannot create local
            # authority for egress.
            status = self.repository.put_grants_if_expected(
                policy.machine_id, owner=owner, policy_digest=policy.digest,
                expected_digest=ABSENT_GRANT_DIGEST, grant_set=desired_grants,
            )
            if status != "stored":
                return {"ok": False, "state": "drifted", "mutated": True,
                        "reason": {"code": "egress_grant_state_drift"}}
        return result

    def _status(self, request):
        owner, backend, policy, state = self._owned_state(request)
        recovery = self._recovery(owner, state)
        if backend is None:
            return {"ok": False, "state": "absent", "backend": None,
                    "mutated": False, "recovery": recovery,
                    "reason": {"code": "native_backend_absent"}}
        if policy is None or self.dependencies is None or self.dependencies.verifier is None:
            return {"ok": False, "state": "drifted", "backend": backend,
                    "mutated": False, "recovery": recovery,
                    "reason": {"code": "isolation_policy_drift"}}
        grants = self._persisted_grants(owner, state, policy)
        if grants is None:
            return {"ok": False, "state": "drifted", "backend": backend,
                    "mutated": False, "recovery": recovery,
                    "reason": {"code": "egress_grant_state_drift"}}
        verified = self._verify(self.dependencies.verifier, policy, grants)
        return {"ok": bool(verified.get("ok")),
                "state": "ready" if verified.get("ok") else "drifted",
                "backend": backend, "health": verified, "mutated": False,
                "recovery": recovery,
                "reason": verified.get("reason", {"code": "isolation_policy_drift"})}

    def _cleanup(self, request):
        owner, backend, policy, state = self._owned_state(request)
        cleanup = self.dependencies.cleanup if self.dependencies else None
        if cleanup is None:
            # Retain this before any caller is allowed to remove its registry or
            # local identity.  It deliberately contains no runtime paths or refs.
            key = f"cleanup:{canonical_digest(owner)[:16]}"
            self.repository.put_recovery(key, {
                "owner": owner, "object_type": "state", "identity": "managed-native",
                "reason_code": "runtime_cleanup_unavailable", "retry_state": "pending",
            })
            return {"ok": False, "state": "cleanup_incomplete", "mutated": True,
                    "recovery": self._recovery(owner, self.repository.snapshot()),
                    "cleanup": {"complete": False, "residual": ("state",)},
                    "reason": {"code": "runtime_cleanup_unavailable"}}
        policy_record = next((value for value in state["policies"].values()
                              if self._same_owner(value.get("owner"), owner)), None)
        if policy is not None:
            grant_cleanup = self._cleanup_grants(owner, policy, state)
            if not grant_cleanup.get("ok"):
                return {
                    "ok": False, "state": "cleanup_incomplete",
                    "mutated": bool(grant_cleanup.get("mutated")),
                    "recovery": self._recovery(owner, self.repository.snapshot()),
                    "cleanup": {"complete": False, "residual": ("grants",)},
                    "reason": {"code": "egress_grant_cleanup_failed"},
                }
        cleanup_plan = {"policy": policy,
                        "cleanup": policy_record.get("cleanup") if policy_record else None}
        try:
            result = (cleanup(request, cleanup_plan) if callable(cleanup)
                      else cleanup.cleanup(request, cleanup_plan))
        except (OSError, RuntimeError, TypeError, ValueError):
            result = None
        if not isinstance(result, dict):
            key = f"cleanup:{canonical_digest(owner)[:16]}"
            self.repository.put_recovery(key, {
                "owner": owner, "object_type": "state", "identity": "managed-native",
                "reason_code": "runtime_cleanup_unavailable", "retry_state": "pending",
            })
            result = {"ok": False, "state": "cleanup_incomplete", "mutated": True,
                      "cleanup": {"complete": False, "residual": ("state",)},
                      "reason": {"code": "runtime_cleanup_unavailable"}}
        if result.get("ok") and policy is not None:
            self._clear_provision_recovery(owner, policy.machine_id)
        return {**result, "recovery": self._recovery(owner, self.repository.snapshot())}

    def _launch(self, request):
        owner, backend, policy, state = self._owned_state(request)
        launcher = self.launcher or (self.dependencies.launcher if self.dependencies else None)
        if backend is None or policy is None:
            return {"ok": False, "state": "absent", "mutated": False,
                    "reason": {"code": "native_backend_absent"}}
        if launcher is None:
            return {"ok": False, "state": "blocked", "mutated": False,
                    "reason": {"code": "isolation_gateway_unavailable"}}
        grants = self._persisted_grants(owner, state, policy)
        if grants is None:
            return {"ok": False, "state": "drifted", "mutated": False,
                    "reason": {"code": "egress_grant_state_drift"}}
        execution = request.arguments.get("execution")
        if execution is not None:
            if (not isinstance(execution, ExecutionRequest) or
                    Path(execution.project_root).expanduser().absolute() !=
                    Path(request.project_root).expanduser().absolute() or
                    execution.label != request.label):
                return {"ok": False, "state": "blocked", "mutated": False,
                        "reason": {"code": "invalid_isolated_execution_request"}}
            command = execution.argv
            entry_path = execution.entry_path
            timeout = execution.timeout
        else:
            command = request.arguments.get("command", request.arguments.get("argv"))
            entry_path = {"wordpress_cli": "wordpress_cli", "exec": "exec",
                          "test": "phpunit"}[request.operation]
            timeout = request.arguments.get("timeout", 300)
        if (not isinstance(command, (list, tuple)) or not command or
                any(not isinstance(value, str) or not value or "\x00" in value for value in command)):
            return {"ok": False, "state": "blocked", "mutated": False,
                    "reason": {"code": "invalid_isolated_command"}}
        command = tuple(command)
        if entry_path == "wordpress_cli":
            if command[0] not in {"wp", "/usr/local/bin/wp"}:
                return {"ok": False, "state": "blocked", "mutated": False,
                        "reason": {"code": "invalid_isolated_command"}}
            command = ("/usr/local/bin/wp", *command[1:], "--path=/var/www/html")
        return launcher.launch(
            policy, entry_path=entry_path, command=tuple(command),
            environment=request.arguments.get("environment", {}),
            credential_refs=request.arguments.get("credential_refs", ()),
            timeout=timeout, grants=grants,
        )

    def invoke(self, request):
        if request.operation == "preflight":
            result = self.preflight.inspect()
        elif request.operation in {"status", "health", "open"}:
            result = self._status(request)
        elif request.operation == "destroy":
            # Recovery must remain possible even after evidence or a preflight
            # prerequisite is unavailable; the cleanup implementation stays
            # compare-before-remove and fail-closed.
            result = self._cleanup(request)
        else:
            gate = self.preflight.inspect()
            if not gate.get("ok"):
                result = {**gate, "reason": {
                    "code": "isolation_prerequisite_missing",
                    "missing": gate.get("reason", {}).get("missing", ()),
                }}
            elif not self.evidence_id and not self.proof_candidate:
                result = {"ok": False, "state": "blocked", "mutated": False,
                          "reason": {"code": "managed_runtime_unproven",
                                     "message": "Live hostile-matrix evidence is required."}}
            elif request.operation in {"ensure", "apply"}:
                result = self._ensure(request)
            elif request.operation in {"wordpress_cli", "exec", "test"}:
                result = self._launch(request)
            else:
                result = {"ok": False, "state": "blocked", "mutated": False,
                          "reason": {"code": "managed_runtime_not_installed"}}
        proof_state = ({"proof_candidate": True, "adoptable": False}
                       if self.proof_candidate else {})
        return OperationResult(
            bool(result.get("ok")), request.operation, request.project_root, "wordpress",
            {"runtime": {"mode": "managed_native", "adapter": self.adapter_id,
                         "isolation": "managed_container", **proof_state},
             **proof_state, **result},
        )
