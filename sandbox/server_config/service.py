from __future__ import annotations

import datetime
import hashlib
import secrets
import time
from typing import Sequence
import uuid

from sandbox.server_config.adapters.base import RenderedGeneration, ServerConfigAdapter
from sandbox.server_config.context import Clock
from sandbox.server_config.models import (
    ActivationTransaction,
    FragmentSet,
    InspectionState,
    KnownGoodReceipt,
    Operation,
    OperationResult,
    PhaseEvidence,
    PhaseResult,
    Readiness,
    RuntimeObservation,
    ServerConfigFragment,
    ServerType,
    TerminalOutcome,
    TransactionPhase,
)
from sandbox.server_config.policy import (
    AUTHORITY,
    validate_fragment_bytes,
    validate_fragment_name,
)
from sandbox.server_config.repository import RepositoryMutation, ServerConfigRepository


class ServerConfigService:
    def __init__(
        self,
        *,
        repository: ServerConfigRepository,
        adapter: ServerConfigAdapter,
        clock: Clock,
        instance_authority: object = None,
    ) -> None:
        self.repository = repository
        self.adapter = adapter
        self.clock = clock
        self.instance_authority = instance_authority

    def _operation_deadline(self) -> float:
        return self.clock.monotonic() + 180.0

    def _phase_deadline(self, op_deadline: float) -> float:
        return min(op_deadline, self.clock.monotonic() + 60.0)

    def _read_fragments_from_state(self, state: object) -> list[ServerConfigFragment]:
        if not state or not isinstance(state, dict):
            return []

        frags = []
        for item in state.get("fragments", []):
            frag = ServerConfigFragment(
                name=item["name"],
                authority=item["authority"],
                server_type=ServerType(
                    item.get("server_type", self.adapter.descriptor.server_type)
                ),
                content_id=item["content_id"],
                content_size=item["content_size"],
                content_locator=item["content_locator"],
                instance_incarnation_id=item["instance_incarnation_id"],
                created_at=datetime.datetime.fromisoformat(item["created_at"]),
                activated_at=(
                    datetime.datetime.fromisoformat(item["activated_at"])
                    if item.get("activated_at")
                    else None
                ),
                policy_revision=item["policy_revision"],
            )
            frags.append(frag)
        return frags

    def _read_fragments(self) -> list[ServerConfigFragment]:
        state = self.repository.read_state()
        return self._read_fragments_from_state(state)

    def inspect(self) -> InspectionState:
        with self.repository._root_descriptor(create=False) as root_descriptor:
            if root_descriptor is None:
                return InspectionState.ABSENT

        try:
            state = self.repository.read_state()
        except Exception:
            return InspectionState.RECOVERY_NEEDED

        try:
            tx = self.repository.read_transaction()
        except Exception:
            return InspectionState.RECOVERY_NEEDED

        if tx is not None and isinstance(tx, dict):
            terminal = tx.get("terminal")
            if terminal == "recovery_needed":
                return InspectionState.RECOVERY_NEEDED
            if terminal is None:
                return InspectionState.RECOVERY_NEEDED

        try:
            obs = self.adapter.observe_runtime(
                self.instance_authority, self.clock.monotonic() + 5.0
            )
            if obs is not None:
                if obs.readiness == Readiness.STOPPED:
                    return InspectionState.STOPPED
                if obs.readiness == Readiness.DEGRADED:
                    return InspectionState.DEGRADED
                if obs.readiness == Readiness.UNKNOWN:
                    return InspectionState.UNKNOWN
                if obs.readiness == Readiness.READY:
                    return InspectionState.HEALTHY
        except Exception:
            return InspectionState.DEGRADED

        return InspectionState.HEALTHY

    def reconcile(self) -> OperationResult:
        op_deadline = self._operation_deadline()
        try:
            with self.repository.locked(
                deadline=self._phase_deadline(op_deadline)
            ) as mutation:
                return self._reconcile_under_lock(mutation, op_deadline)
        except ValueError as exc:
            if str(exc) == "operation_conflict":
                return OperationResult(
                    outcome=TerminalOutcome.CONFLICT,
                    code="operation_conflict",
                    mutated=False,
                    instance_incarnation_id=self.repository.incarnation,
                    fragment_name=None,
                    fragment_set_id=None,
                )
            raise

    def _reconcile_under_lock(
        self, mutation: RepositoryMutation, op_deadline: float, *, check_drift: bool = True,
    ) -> OperationResult:
        try:
            tx_raw = mutation.read_transaction()
        except Exception:
            return OperationResult(
                outcome=TerminalOutcome.RECOVERY_NEEDED,
                code="journal_corrupt",
                mutated=None,
                instance_incarnation_id=self.repository.incarnation,
                fragment_name=None,
                fragment_set_id=None,
            )

        if tx_raw is None:
            # Check state and runtime drift
            try:
                state_raw = mutation.read_state()
            except Exception:
                return OperationResult(
                    outcome=TerminalOutcome.RECOVERY_NEEDED,
                    code="state_corrupt",
                    mutated=None,
                    instance_incarnation_id=self.repository.incarnation,
                    fragment_name=None,
                    fragment_set_id=None,
                )

            if state_raw is not None and isinstance(state_raw, dict) and check_drift:
                expected_gen = state_raw.get("generation_id")
                if expected_gen is not None:
                    try:
                        obs = self.adapter.observe_runtime(
                            self.instance_authority, self._phase_deadline(op_deadline)
                        )
                    except Exception:
                        return OperationResult(
                            outcome=TerminalOutcome.RECOVERY_NEEDED,
                            code="runtime_observe_failed",
                            mutated=None,
                            instance_incarnation_id=self.repository.incarnation,
                            fragment_name=None,
                            fragment_set_id=None,
                        )
                    if obs is not None:
                        if obs.readiness == Readiness.STOPPED:
                            return OperationResult(
                                outcome=TerminalOutcome.REFUSED,
                                code="instance_stopped",
                                mutated=False,
                                instance_incarnation_id=self.repository.incarnation,
                                fragment_name=None,
                                fragment_set_id=None,
                            )
                        if obs.readiness != Readiness.READY:
                            return OperationResult(
                                outcome=TerminalOutcome.RECOVERY_NEEDED,
                                code="runtime_not_ready",
                                mutated=None,
                                instance_incarnation_id=self.repository.incarnation,
                                fragment_name=None,
                                fragment_set_id=None,
                            )
                        if obs.observed_generation_id != expected_gen:
                            return OperationResult(
                                outcome=TerminalOutcome.RECOVERY_NEEDED,
                                code="runtime_drifted",
                                mutated=None,
                                instance_incarnation_id=self.repository.incarnation,
                                fragment_name=None,
                                fragment_set_id=None,
                            )
            if state_raw is not None and isinstance(state_raw, dict):
                return OperationResult(
                    outcome=TerminalOutcome.ACTIVE,
                    code="ok",
                    mutated=True,
                    instance_incarnation_id=self.repository.incarnation,
                    fragment_name=None,
                    fragment_set_id=state_raw.get("fragment_set_id"),
                )
            return OperationResult(
                outcome=TerminalOutcome.NO_OP,
                code="ok",
                mutated=False,
                instance_incarnation_id=self.repository.incarnation,
                fragment_name=None,
                fragment_set_id=None,
            )

        try:
            tx = ActivationTransaction.from_record(tx_raw)
        except Exception:
            return OperationResult(
                outcome=TerminalOutcome.RECOVERY_NEEDED,
                code="journal_corrupt",
                mutated=None,
                instance_incarnation_id=self.repository.incarnation,
                fragment_name=None,
                fragment_set_id=None,
            )

        if tx.is_terminal:
            if tx.terminal == TerminalOutcome.RECOVERY_NEEDED:
                return OperationResult(
                    outcome=TerminalOutcome.RECOVERY_NEEDED,
                    code="recovery_needed",
                    mutated=None,
                    instance_incarnation_id=self.repository.incarnation,
                    fragment_name=tx.fragment_name,
                    fragment_set_id=None,
                )
            try:
                mutation.clear_transaction()
            except Exception:
                pass
            expected_mutated = {
                TerminalOutcome.ACTIVE: True,
                TerminalOutcome.NO_OP: False,
                TerminalOutcome.REFUSED: False,
                TerminalOutcome.ROLLED_BACK: True,
                TerminalOutcome.CONFLICT: False,
                TerminalOutcome.RECOVERY_NEEDED: None,
            }
            return OperationResult(
                outcome=tx.terminal,
                code="reconciled_terminal",
                mutated=expected_mutated[tx.terminal],
                instance_incarnation_id=self.repository.incarnation,
                fragment_name=tx.fragment_name,
                fragment_set_id=None,
            )

        if tx.phase in (
            TransactionPhase.REQUESTED,
            TransactionPhase.PREPARED,
            TransactionPhase.VALIDATED,
        ):
            tx = tx.finish(TerminalOutcome.REFUSED)
            mutation.write_transaction(tx.to_record())
            try:
                mutation.clear_transaction()
            except Exception:
                pass
            return OperationResult(
                outcome=TerminalOutcome.NO_OP,
                code="reconciled_pre_activation",
                mutated=False,
                instance_incarnation_id=self.repository.incarnation,
                fragment_name=tx.fragment_name,
                fragment_set_id=None,
            )

        if tx.phase == TransactionPhase.COMMITTED:
            state = mutation.read_state()
            if isinstance(state, dict) and state.get("generation_id") == tx.candidate_generation_id:
                tx = tx.finish(TerminalOutcome.ACTIVE)
                mutation.write_transaction(tx.to_record())
                try:
                    mutation.clear_transaction()
                except Exception:
                    pass
                return OperationResult(
                    outcome=TerminalOutcome.ACTIVE,
                    code="reconciled_committed",
                    mutated=True,
                    instance_incarnation_id=self.repository.incarnation,
                    fragment_name=tx.fragment_name,
                    fragment_set_id=tx.candidate_set_id,
                )

        # Post-activation recovery
        if not mutation.has_generation(tx.prior_generation_id):
            if tx.prior_generation_id != "sha256:" + "0" * 64:
                if not tx.rollback_attempted and tx.phase not in (
                    TransactionPhase.RESTORING_PRIOR,
                    TransactionPhase.RECOVERY_RELOADING,
                    TransactionPhase.RECOVERY_OBSERVING_READY,
                ):
                    tx = tx.begin_rollback(code="missing_generation", at=self.clock.now())
                tx = tx.finish(TerminalOutcome.RECOVERY_NEEDED)
                mutation.write_transaction(tx.to_record())
                return OperationResult(
                    outcome=TerminalOutcome.RECOVERY_NEEDED,
                    code="missing_generation",
                    mutated=None,
                    instance_incarnation_id=self.repository.incarnation,
                    fragment_name=tx.fragment_name,
                    fragment_set_id=None,
                )

        try:
            if not tx.rollback_attempted:
                tx = tx.begin_rollback(
                    code="interrupted_recovery", at=self.clock.now()
                )
                mutation.write_transaction(tx.to_record())

            obs = self.adapter.observe_runtime(
                self.instance_authority, self._phase_deadline(op_deadline)
            )
            self.adapter.restore(
                tx.prior_generation_id, obs, self._phase_deadline(op_deadline)
            )

            tx = tx.transition(TransactionPhase.RECOVERY_RELOADING)
            mutation.write_transaction(tx.to_record())

            self.adapter.reload(obs, self._phase_deadline(op_deadline))

            tx = tx.transition(TransactionPhase.RECOVERY_OBSERVING_READY)
            mutation.write_transaction(tx.to_record())

            ready = self.adapter.observe_ready(
                tx.prior_generation_id, obs, self._phase_deadline(op_deadline)
            )
            if hasattr(ready, "ok") and not ready.ok:
                raise RuntimeError("recovery readiness not ok")
            if hasattr(ready, "readiness") and ready.readiness != Readiness.READY:
                raise RuntimeError("recovery readiness not ready")

            tx = tx.finish(TerminalOutcome.ROLLED_BACK)
            mutation.write_transaction(tx.to_record())
            return OperationResult(
                outcome=TerminalOutcome.ROLLED_BACK,
                code="reconciled_rollback",
                mutated=True,
                instance_incarnation_id=self.repository.incarnation,
                fragment_name=tx.fragment_name,
                fragment_set_id=tx.prior_set_id,
            )
        except Exception:
            tx = tx.finish(TerminalOutcome.RECOVERY_NEEDED)
            mutation.write_transaction(tx.to_record())
            return OperationResult(
                outcome=TerminalOutcome.RECOVERY_NEEDED,
                code="recovery_needed",
                mutated=None,
                instance_incarnation_id=self.repository.incarnation,
                fragment_name=tx.fragment_name,
                fragment_set_id=None,
            )

    def apply(
        self,
        fragment: ServerConfigFragment | None = None,
        *,
        name: str | None = None,
        content: bytes | None = None,
        authority: str = "wordpress-cache-v1",
        instance_authority: object = None,
    ) -> OperationResult:
        auth = instance_authority or self.instance_authority
        if auth is not None and (getattr(auth, "is_running", True) is False or getattr(auth, "status", "ready") != "ready"):
            status_text = getattr(auth, "status", None) or ("stopped" if getattr(auth, "is_running", True) is False else "not ready")
            raise RuntimeError(
                f"apply blocked for instance '{getattr(auth, 'instance_name', 'unknown')}': instance is {status_text}"
            )
        if fragment is not None:
            frag_name = fragment.name
            frag = fragment
        else:
            if name is None or content is None:
                raise ValueError("Must provide fragment or name/content")
            frag_name = name
            validate_fragment_name(frag_name)
            validate_fragment_bytes(content)

            locator = "fragments/" + hashlib.sha256(content).hexdigest() + ".fragment"
            frag = ServerConfigFragment.create(
                name=frag_name,
                authority=authority,
                server_type=ServerType(self.adapter.descriptor.server_type),
                content=content,
                content_locator=locator,
                instance_incarnation_id=self.repository.incarnation,
                created_at=self.clock.now(),
                policy_revision="wordpress-cache-v1/1",
            )

        op_deadline = self._operation_deadline()

        try:
            with self.repository.locked(
                deadline=self._phase_deadline(op_deadline)
            ) as mutation:
                return self._apply_under_lock(frag, mutation, op_deadline)
        except ValueError as exc:
            if str(exc) == "operation_conflict":
                return OperationResult(
                    outcome=TerminalOutcome.CONFLICT,
                    code="operation_conflict",
                    mutated=False,
                    instance_incarnation_id=self.repository.incarnation,
                    fragment_name=frag_name,
                    fragment_set_id=None,
                )
            raise

    def _apply_under_lock(
        self,
        frag: ServerConfigFragment,
        mutation: RepositoryMutation,
        op_deadline: float,
    ) -> OperationResult:
        rec = self._reconcile_under_lock(mutation, op_deadline, check_drift=False)
        if rec.outcome == TerminalOutcome.RECOVERY_NEEDED:
            return rec
        if rec.outcome == TerminalOutcome.REFUSED:
            return rec

        if self.clock.monotonic() >= op_deadline:
            return OperationResult(
                outcome=TerminalOutcome.REFUSED,
                code="deadline_exceeded",
                mutated=False,
                instance_incarnation_id=self.repository.incarnation,
                fragment_name=frag.name,
                fragment_set_id=None,
            )

        try:
            state = mutation.read_state()
        except Exception:
            return OperationResult(
                outcome=TerminalOutcome.RECOVERY_NEEDED,
                code="state_corrupt",
                mutated=None,
                instance_incarnation_id=self.repository.incarnation,
                fragment_name=frag.name,
                fragment_set_id=None,
            )

        prior_gen_id = (
            state.get("generation_id")
            if isinstance(state, dict) and state.get("generation_id")
            else "sha256:" + "0" * 64
        )
        prior_set_id = (
            state.get("fragment_set_id")
            if isinstance(state, dict) and state.get("fragment_set_id")
            else "sha256:" + "0" * 64
        )

        existing = self._read_fragments_from_state(state)
        for ex in existing:
            if ex.name == frag.name and ex.content_id == frag.content_id:
                return OperationResult(
                    outcome=TerminalOutcome.NO_OP,
                    code="ok",
                    mutated=False,
                    instance_incarnation_id=self.repository.incarnation,
                    fragment_name=frag.name,
                    fragment_set_id=prior_set_id,
                )

        try:
            obs = self.adapter.observe_runtime(
                self.instance_authority, self._phase_deadline(op_deadline)
            )
        except Exception:
            return OperationResult(
                outcome=TerminalOutcome.RECOVERY_NEEDED,
                code="runtime_observe_failed",
                mutated=None,
                instance_incarnation_id=self.repository.incarnation,
                fragment_name=frag.name,
                fragment_set_id=None,
            )

        if obs is not None:
            if obs.readiness == Readiness.STOPPED:
                return OperationResult(
                    outcome=TerminalOutcome.REFUSED,
                    code="instance_stopped",
                    mutated=False,
                    instance_incarnation_id=self.repository.incarnation,
                    fragment_name=frag.name,
                    fragment_set_id=None,
                )
            if obs.readiness != Readiness.READY:
                return OperationResult(
                    outcome=TerminalOutcome.RECOVERY_NEEDED,
                    code="runtime_not_ready",
                    mutated=None,
                    instance_incarnation_id=self.repository.incarnation,
                    fragment_name=frag.name,
                    fragment_set_id=None,
                )
            if isinstance(state, dict) and state.get("generation_id"):
                if obs.observed_generation_id != state.get("generation_id"):
                    return OperationResult(
                        outcome=TerminalOutcome.RECOVERY_NEEDED,
                        code="runtime_drifted",
                        mutated=None,
                        instance_incarnation_id=self.repository.incarnation,
                        fragment_name=frag.name,
                        fragment_set_id=None,
                    )

        new_fragments = []
        replaced = False
        for ex in existing:
            if ex.name == frag.name:
                new_fragments.append(frag)
                replaced = True
            else:
                new_fragments.append(ex)
        if not replaced:
            new_fragments.append(frag)
        new_fragments.sort(key=lambda x: x.name)

        if self.clock.monotonic() >= op_deadline:
            return OperationResult(
                outcome=TerminalOutcome.REFUSED,
                code="deadline_exceeded",
                mutated=False,
                instance_incarnation_id=self.repository.incarnation,
                fragment_name=frag.name,
                fragment_set_id=None,
            )

        rendered = self.adapter.render(new_fragments, self.instance_authority)
        if isinstance(rendered, RenderedGeneration):
            files = {rf.name: rf.content for rf in rendered.files}
            candidate_set_id = "sha256:" + hashlib.sha256(
                "".join(f.content_id for f in new_fragments).encode("utf-8")
            ).hexdigest()
            manifest = {
                "schema": 1,
                "fragment_set_id": candidate_set_id,
                "renderer_revision": self.adapter.descriptor.renderer_revision,
            }
        else:
            files = {"fragments.conf": b"# sandbox-fragments\n"}
            candidate_set_id = "sha256:" + hashlib.sha256(
                "".join(f.content_id for f in new_fragments).encode("utf-8")
            ).hexdigest()
            manifest = {
                "schema": 1,
                "fragment_set_id": candidate_set_id,
                "renderer_revision": self.adapter.descriptor.renderer_revision,
            }

        candidate_gen_id = mutation.publish_generation(files, manifest)

        tx = ActivationTransaction.requested(
            transaction_id="txn_" + secrets.token_hex(16),
            operation=Operation.APPLY,
            fragment_name=frag.name,
            instance_incarnation_id=self.repository.incarnation,
            server_type=ServerType(self.adapter.descriptor.server_type),
            prior_set_id=prior_set_id,
            prior_generation_id=prior_gen_id,
            candidate_set_id=candidate_set_id,
            candidate_generation_id=candidate_gen_id,
            runtime_precondition_digest=(
                obs.precondition_digest() if obs is not None else "sha256:" + "0" * 64
            ),
            deadline_at=datetime.datetime.fromtimestamp(
                op_deadline, tz=datetime.timezone.utc
            ),
        )
        mutation.write_transaction(tx.to_record())

        tx = tx.transition(TransactionPhase.PREPARED)
        mutation.write_transaction(tx.to_record())

        # Validation
        val_start = self.clock.monotonic()
        try:
            val_evidence = self.adapter.validate(
                rendered, obs, self._phase_deadline(op_deadline)
            )
            if self.clock.monotonic() - val_start > 60.0 or self.clock.monotonic() >= op_deadline:
                raise TimeoutError("validation phase timed out")
            if (hasattr(val_evidence, "ok") and not val_evidence.ok) or (
                hasattr(val_evidence, "policy")
                and hasattr(val_evidence.policy, "ok")
                and not val_evidence.policy.ok
            ):
                raise RuntimeError("validation rejected")
        except Exception:
            tx = tx.finish(TerminalOutcome.REFUSED)
            mutation.write_transaction(tx.to_record())
            try:
                mutation.clear_transaction()
            except Exception:
                pass
            return OperationResult(
                outcome=TerminalOutcome.REFUSED,
                code="validation_failed",
                mutated=False,
                instance_incarnation_id=self.repository.incarnation,
                fragment_name=frag.name,
                fragment_set_id=None,
            )

        tx = tx.transition(TransactionPhase.VALIDATED)
        mutation.write_transaction(tx.to_record())

        # Live Activation
        tx = tx.transition(TransactionPhase.ACTIVATING)
        mutation.write_transaction(tx.to_record())

        fault = None
        phase_start = self.clock.monotonic()
        try:
            if self.clock.monotonic() >= op_deadline:
                raise TimeoutError("operation deadline exceeded")

            act = self.adapter.activate(
                candidate_gen_id, obs, self._phase_deadline(op_deadline)
            )
            if hasattr(act, "ok") and not act.ok:
                raise RuntimeError("activation failed")
            if self.clock.monotonic() - phase_start > 60.0 or self.clock.monotonic() >= op_deadline:
                raise TimeoutError("activation phase timed out")

            tx = tx.transition(TransactionPhase.RELOADING)
            mutation.write_transaction(tx.to_record())

            phase_start = self.clock.monotonic()
            rel = self.adapter.reload(obs, self._phase_deadline(op_deadline))
            if hasattr(rel, "ok") and not rel.ok:
                raise RuntimeError("reload failed")
            if self.clock.monotonic() - phase_start > 60.0 or self.clock.monotonic() >= op_deadline:
                raise TimeoutError("reload phase timed out")

            tx = tx.transition(TransactionPhase.OBSERVING_READY)
            mutation.write_transaction(tx.to_record())

            phase_start = self.clock.monotonic()
            ready = self.adapter.observe_ready(
                candidate_gen_id, obs, self._phase_deadline(op_deadline)
            )
            if hasattr(ready, "ok") and not ready.ok:
                raise RuntimeError("observe_ready failed")
            if hasattr(ready, "readiness") and ready.readiness != Readiness.READY:
                raise RuntimeError("observe_ready not ready")
            if hasattr(ready, "code") and ready.code == "degraded":
                raise RuntimeError("observe_ready returned degraded")
            if self.clock.monotonic() - phase_start > 60.0 or self.clock.monotonic() >= op_deadline:
                raise TimeoutError("readiness phase timed out")
        except Exception as exc:
            fault = exc

        if fault is not None:
            # Rollback
            tx = tx.begin_rollback(code="fault", at=self.clock.now())
            mutation.write_transaction(tx.to_record())

            rb_start = self.clock.monotonic()
            try:
                if self.clock.monotonic() >= op_deadline:
                    raise TimeoutError("operation deadline exceeded")
                if not mutation.has_generation(prior_gen_id):
                    if prior_gen_id != "sha256:" + "0" * 64:
                        raise RuntimeError("prior generation missing")

                self.adapter.restore(
                    prior_gen_id, obs, self._phase_deadline(op_deadline)
                )
                if self.clock.monotonic() - rb_start > 60.0 or self.clock.monotonic() >= op_deadline:
                    raise TimeoutError("rollback restore timed out")

                tx = tx.transition(TransactionPhase.RECOVERY_RELOADING)
                mutation.write_transaction(tx.to_record())

                self.adapter.reload(obs, self._phase_deadline(op_deadline))
                if self.clock.monotonic() - rb_start > 60.0 or self.clock.monotonic() >= op_deadline:
                    raise TimeoutError("rollback reload timed out")

                tx = tx.transition(TransactionPhase.RECOVERY_OBSERVING_READY)
                mutation.write_transaction(tx.to_record())

                ready = self.adapter.observe_ready(
                    prior_gen_id, obs, self._phase_deadline(op_deadline)
                )
                if hasattr(ready, "ok") and not ready.ok:
                    raise RuntimeError("rollback readiness failed")
                if self.clock.monotonic() - rb_start > 60.0 or self.clock.monotonic() >= op_deadline:
                    raise TimeoutError("rollback observe_ready timed out")

                tx = tx.finish(TerminalOutcome.ROLLED_BACK)
                mutation.write_transaction(tx.to_record())
                return OperationResult(
                    outcome=TerminalOutcome.ROLLED_BACK,
                    code="rolled_back",
                    mutated=True,
                    instance_incarnation_id=self.repository.incarnation,
                    fragment_name=frag.name,
                    fragment_set_id=prior_set_id,
                )
            except Exception:
                tx = tx.finish(TerminalOutcome.RECOVERY_NEEDED)
                mutation.write_transaction(tx.to_record())
                return OperationResult(
                    outcome=TerminalOutcome.RECOVERY_NEEDED,
                    code="recovery_needed",
                    mutated=None,
                    instance_incarnation_id=self.repository.incarnation,
                    fragment_name=frag.name,
                    fragment_set_id=None,
                )

        # Committed
        tx = tx.transition(TransactionPhase.COMMITTED)
        mutation.write_transaction(tx.to_record())

        raw_payload = getattr(frag, "_raw_content", None) or getattr(frag, "content", None)
        if raw_payload:
            mutation.store_fragment(raw_payload)

        state_repr = {
            "schema": 1,
            "instance_incarnation_id": self.repository.incarnation,
            "server_type": self.adapter.descriptor.server_type,
            "fragment_set_id": candidate_set_id,
            "generation_id": candidate_gen_id,
            "runtime_image_id": (
                obs.image_id if obs and obs.image_id else "sha256:" + "0" * 64
            ),
            "mount_id": obs.mount_id if obs and obs.mount_id else "sha256:" + "0" * 64,
            "validation_evidence_id": "sha256:" + "0" * 64,
            "readiness_evidence_id": "sha256:" + "0" * 64,
            "committed_at": self.clock.now().isoformat(),
            "fragments": [
                {
                    "name": f.name,
                    "authority": f.authority,
                    "server_type": f.server_type.value,
                    "content_id": f.content_id,
                    "content_size": f.content_size,
                    "content_locator": f.content_locator,
                    "instance_incarnation_id": f.instance_incarnation_id,
                    "created_at": f.created_at.isoformat(),
                    "activated_at": self.clock.now().isoformat(),
                    "policy_revision": f.policy_revision,
                }
                for f in new_fragments
            ],
        }
        mutation.write_state(state_repr)

        tx = tx.finish(TerminalOutcome.ACTIVE)
        mutation.write_transaction(tx.to_record())
        try:
            mutation.clear_transaction()
        except Exception:
            pass
        mutation.prune_unreferenced_generations()

        return OperationResult(
            outcome=TerminalOutcome.ACTIVE,
            code="ok",
            mutated=True,
            instance_incarnation_id=self.repository.incarnation,
            fragment_name=frag.name,
            fragment_set_id=candidate_set_id,
        )

    def list(self) -> tuple[ServerConfigFragment, ...]:
        return tuple(self._read_fragments())

    def show(self, name: str) -> ServerConfigFragment | None:
        for f in self._read_fragments():
            if f.name == name:
                return f
        return None

    def read_fragment_content(self, name: str) -> bytes:
        frag = self.show(name)
        if frag is None or not frag.content_id:
            raise ValueError("fragment_not_found")
        return self.repository.read_fragment(frag.content_id)

    def revert(
        self,
        name: str,
        *,
        instance_authority: object = None,
    ) -> OperationResult:
        auth = instance_authority or self.instance_authority
        if auth is not None and (getattr(auth, "is_running", True) is False or getattr(auth, "status", "ready") != "ready"):
            status_text = getattr(auth, "status", None) or ("stopped" if getattr(auth, "is_running", True) is False else "not ready")
            raise RuntimeError(
                f"revert blocked for instance '{getattr(auth, 'instance_name', 'unknown')}': instance is {status_text}"
            )
        op_deadline = self._operation_deadline()
        try:
            with self.repository.locked(
                deadline=self._phase_deadline(op_deadline)
            ) as mutation:
                return self._revert_under_lock(name, mutation, op_deadline)
        except ValueError as exc:
            if str(exc) == "operation_conflict":
                return OperationResult(
                    outcome=TerminalOutcome.CONFLICT,
                    code="operation_conflict",
                    mutated=False,
                    instance_incarnation_id=self.repository.incarnation,
                    fragment_name=name,
                    fragment_set_id=None,
                )
            raise

    def _revert_under_lock(
        self,
        name: str,
        mutation: RepositoryMutation,
        op_deadline: float,
    ) -> OperationResult:
        rec = self._reconcile_under_lock(mutation, op_deadline, check_drift=False)
        if rec.outcome == TerminalOutcome.RECOVERY_NEEDED:
            return rec
        if rec.outcome == TerminalOutcome.REFUSED:
            return rec

        try:
            state = mutation.read_state()
        except Exception:
            return OperationResult(
                outcome=TerminalOutcome.RECOVERY_NEEDED,
                code="state_corrupt",
                mutated=None,
                instance_incarnation_id=self.repository.incarnation,
                fragment_name=name,
                fragment_set_id=None,
            )

        prior_gen_id = (
            state.get("generation_id")
            if isinstance(state, dict) and state.get("generation_id")
            else "sha256:" + "0" * 64
        )
        prior_set_id = (
            state.get("fragment_set_id")
            if isinstance(state, dict) and state.get("fragment_set_id")
            else "sha256:" + "0" * 64
        )

        existing = self._read_fragments_from_state(state)
        if not any(ex.name == name for ex in existing):
            return OperationResult(
                outcome=TerminalOutcome.NO_OP,
                code="ok",
                mutated=False,
                instance_incarnation_id=self.repository.incarnation,
                fragment_name=name,
                fragment_set_id=prior_set_id,
            )

        try:
            obs = self.adapter.observe_runtime(
                self.instance_authority, self._phase_deadline(op_deadline)
            )
        except Exception:
            return OperationResult(
                outcome=TerminalOutcome.RECOVERY_NEEDED,
                code="runtime_observe_failed",
                mutated=None,
                instance_incarnation_id=self.repository.incarnation,
                fragment_name=name,
                fragment_set_id=None,
            )

        if obs is not None:
            if obs.readiness == Readiness.STOPPED:
                return OperationResult(
                    outcome=TerminalOutcome.REFUSED,
                    code="instance_stopped",
                    mutated=False,
                    instance_incarnation_id=self.repository.incarnation,
                    fragment_name=name,
                    fragment_set_id=None,
                )
            if obs.readiness != Readiness.READY:
                return OperationResult(
                    outcome=TerminalOutcome.RECOVERY_NEEDED,
                    code="runtime_not_ready",
                    mutated=None,
                    instance_incarnation_id=self.repository.incarnation,
                    fragment_name=name,
                    fragment_set_id=None,
                )

        new_fragments = [ex for ex in existing if ex.name != name]
        new_fragments.sort(key=lambda x: x.name)

        rendered = self.adapter.render(new_fragments, self.instance_authority)
        if isinstance(rendered, RenderedGeneration):
            files = {rf.name: rf.content for rf in rendered.files}
            candidate_set_id = "sha256:" + hashlib.sha256(
                "".join(f.content_id for f in new_fragments).encode("utf-8")
            ).hexdigest()
            manifest = {
                "schema": 1,
                "fragment_set_id": candidate_set_id,
                "renderer_revision": self.adapter.descriptor.renderer_revision,
            }
        else:
            files = {"fragments.conf": b"# sandbox-fragments\n"}
            candidate_set_id = "sha256:" + hashlib.sha256(
                "".join(f.content_id for f in new_fragments).encode("utf-8")
            ).hexdigest()
            manifest = {
                "schema": 1,
                "fragment_set_id": candidate_set_id,
                "renderer_revision": self.adapter.descriptor.renderer_revision,
            }

        candidate_gen_id = mutation.publish_generation(files, manifest)

        tx = ActivationTransaction.requested(
            transaction_id="txn_" + secrets.token_hex(16),
            operation=Operation.REVERT,
            fragment_name=name,
            instance_incarnation_id=self.repository.incarnation,
            server_type=ServerType(self.adapter.descriptor.server_type),
            prior_set_id=prior_set_id,
            prior_generation_id=prior_gen_id,
            candidate_set_id=candidate_set_id,
            candidate_generation_id=candidate_gen_id,
            runtime_precondition_digest=(
                obs.precondition_digest() if obs is not None else "sha256:" + "0" * 64
            ),
            deadline_at=datetime.datetime.fromtimestamp(
                op_deadline, tz=datetime.timezone.utc
            ),
        )
        mutation.write_transaction(tx.to_record())

        tx = tx.transition(TransactionPhase.PREPARED)
        mutation.write_transaction(tx.to_record())

        # Validation
        try:
            val_evidence = self.adapter.validate(
                rendered, obs, self._phase_deadline(op_deadline)
            )
            if (hasattr(val_evidence, "ok") and not val_evidence.ok) or (
                hasattr(val_evidence, "policy")
                and hasattr(val_evidence.policy, "ok")
                and not val_evidence.policy.ok
            ):
                raise RuntimeError("validation rejected")
        except Exception:
            tx = tx.finish(TerminalOutcome.REFUSED)
            mutation.write_transaction(tx.to_record())
            try:
                mutation.clear_transaction()
            except Exception:
                pass
            return OperationResult(
                outcome=TerminalOutcome.REFUSED,
                code="validation_failed",
                mutated=False,
                instance_incarnation_id=self.repository.incarnation,
                fragment_name=name,
                fragment_set_id=None,
            )

        tx = tx.transition(TransactionPhase.VALIDATED)
        mutation.write_transaction(tx.to_record())

        # Live Activation
        tx = tx.transition(TransactionPhase.ACTIVATING)
        mutation.write_transaction(tx.to_record())

        fault = None
        phase_start = self.clock.monotonic()
        try:
            act = self.adapter.activate(
                candidate_gen_id, obs, self._phase_deadline(op_deadline)
            )
            if hasattr(act, "ok") and not act.ok:
                raise RuntimeError("activation failed")
            if self.clock.monotonic() - phase_start > 60.0:
                raise TimeoutError("activation phase timed out")

            tx = tx.transition(TransactionPhase.RELOADING)
            mutation.write_transaction(tx.to_record())

            phase_start = self.clock.monotonic()
            rel = self.adapter.reload(obs, self._phase_deadline(op_deadline))
            if hasattr(rel, "ok") and not rel.ok:
                raise RuntimeError("reload failed")
            if self.clock.monotonic() - phase_start > 60.0:
                raise TimeoutError("reload phase timed out")

            tx = tx.transition(TransactionPhase.OBSERVING_READY)
            mutation.write_transaction(tx.to_record())

            phase_start = self.clock.monotonic()
            ready = self.adapter.observe_ready(
                candidate_gen_id, obs, self._phase_deadline(op_deadline)
            )
            if hasattr(ready, "ok") and not ready.ok:
                raise RuntimeError("observe_ready failed")
            if hasattr(ready, "readiness") and ready.readiness != Readiness.READY:
                raise RuntimeError("observe_ready not ready")
            if self.clock.monotonic() - phase_start > 60.0:
                raise TimeoutError("readiness phase timed out")
        except Exception as exc:
            fault = exc

        if fault is not None:
            # Rollback
            tx = tx.begin_rollback(code="fault", at=self.clock.now())
            mutation.write_transaction(tx.to_record())

            rb_start = self.clock.monotonic()
            try:
                if not mutation.has_generation(prior_gen_id):
                    if prior_gen_id != "sha256:" + "0" * 64:
                        raise RuntimeError("prior generation missing")

                self.adapter.restore(
                    prior_gen_id, obs, self._phase_deadline(op_deadline)
                )
                if self.clock.monotonic() - rb_start > 60.0:
                    raise TimeoutError("rollback restore timed out")

                tx = tx.transition(TransactionPhase.RECOVERY_RELOADING)
                mutation.write_transaction(tx.to_record())

                self.adapter.reload(obs, self._phase_deadline(op_deadline))
                if self.clock.monotonic() - rb_start > 60.0:
                    raise TimeoutError("rollback reload timed out")

                tx = tx.transition(TransactionPhase.RECOVERY_OBSERVING_READY)
                mutation.write_transaction(tx.to_record())

                ready = self.adapter.observe_ready(
                    prior_gen_id, obs, self._phase_deadline(op_deadline)
                )
                if hasattr(ready, "ok") and not ready.ok:
                    raise RuntimeError("rollback readiness failed")
                if self.clock.monotonic() - rb_start > 60.0:
                    raise TimeoutError("rollback observe_ready timed out")

                tx = tx.finish(TerminalOutcome.ROLLED_BACK)
                mutation.write_transaction(tx.to_record())
                return OperationResult(
                    outcome=TerminalOutcome.ROLLED_BACK,
                    code="rolled_back",
                    mutated=True,
                    instance_incarnation_id=self.repository.incarnation,
                    fragment_name=name,
                    fragment_set_id=prior_set_id,
                )
            except Exception:
                tx = tx.finish(TerminalOutcome.RECOVERY_NEEDED)
                mutation.write_transaction(tx.to_record())
                return OperationResult(
                    outcome=TerminalOutcome.RECOVERY_NEEDED,
                    code="recovery_needed",
                    mutated=None,
                    instance_incarnation_id=self.repository.incarnation,
                    fragment_name=name,
                    fragment_set_id=None,
                )

        # Committed
        tx = tx.transition(TransactionPhase.COMMITTED)
        mutation.write_transaction(tx.to_record())

        state_repr = {
            "schema": 1,
            "instance_incarnation_id": self.repository.incarnation,
            "server_type": self.adapter.descriptor.server_type,
            "fragment_set_id": candidate_set_id,
            "generation_id": candidate_gen_id,
            "runtime_image_id": (
                obs.image_id if obs and obs.image_id else "sha256:" + "0" * 64
            ),
            "mount_id": obs.mount_id if obs and obs.mount_id else "sha256:" + "0" * 64,
            "validation_evidence_id": "sha256:" + "0" * 64,
            "readiness_evidence_id": "sha256:" + "0" * 64,
            "committed_at": self.clock.now().isoformat(),
            "fragments": [
                {
                    "name": f.name,
                    "authority": f.authority,
                    "server_type": f.server_type.value,
                    "content_id": f.content_id,
                    "content_size": f.content_size,
                    "content_locator": f.content_locator,
                    "instance_incarnation_id": f.instance_incarnation_id,
                    "created_at": f.created_at.isoformat(),
                    "activated_at": self.clock.now().isoformat(),
                    "policy_revision": f.policy_revision,
                }
                for f in new_fragments
            ],
        }
        mutation.write_state(state_repr)

        tx = tx.finish(TerminalOutcome.ACTIVE)
        mutation.write_transaction(tx.to_record())
        try:
            mutation.clear_transaction()
        except Exception:
            pass
        mutation.prune_unreferenced_generations()

        return OperationResult(
            outcome=TerminalOutcome.ACTIVE,
            code="ok",
            mutated=True,
            instance_incarnation_id=self.repository.incarnation,
            fragment_name=name,
            fragment_set_id=candidate_set_id,
        )
