"""Recovery orchestration. Observation and receipt commit are the default scope."""

from __future__ import annotations

import time
from contextlib import nullcontext

from .models import RecoveryAction, RecoveryRequest, RecoveryResult
from .policy import classify_observation, validate_edge_request, validate_job_binding


class RecoveryAuthorityError(RuntimeError):
    """A current registered target cannot authorize the durable operation."""


class RecoveryService:
    def __init__(self, *, repository, job_lookup, source_check, observer,
                 edge_adapter=None, governance_check=None, clock=None,
                 evidence_ttl_seconds: int = 300, broker_guard=None,
                 authority_guard=None) -> None:
        self.repository = repository
        self.job_lookup = job_lookup
        self.source_check = source_check
        self.observer = observer
        self.edge_adapter = edge_adapter
        # Edge authority is opt-in. A missing policy verifier can observe, but
        # can never reach the externally mutating adapter.
        self.governance_check = governance_check or (lambda _request: False)
        self.clock = clock or time.time
        self.evidence_ttl_seconds = evidence_ttl_seconds
        self.broker_guard = broker_guard or (lambda _request: nullcontext())
        self.authority_guard = authority_guard or (
            lambda _request, _operation: nullcontext())

    def recover(self, request: RecoveryRequest) -> dict:
        # Immutable terminal replay is state authority in its own right. Read
        # and fully validate it before consulting a job backend that may have
        # expired or be unavailable. A never-seen identity takes no lock and
        # creates no repository artifacts here.
        try:
            initial = self.repository.load()
            existing = (initial.get("hosts") or {}).get(request.target.key)
            if existing is not None:
                if not isinstance(existing, dict):
                    raise ValueError("invalid recovery target state")
                record = self.repository.target(initial, request.target.key)
                replay = self.repository.replay(record, request)
                if replay is not None:
                    return self._replay_result(replay)
        except (OSError, ValueError) as exc:
            code = "binding_mismatch" if str(exc) == "binding_mismatch" else "persistence_failed"
            family = "refused" if code == "binding_mismatch" else "failed"
            return self._uncommitted(request, family, code)
        try:
            job = self.job_lookup(request.job_id)
        except Exception:
            return self._uncommitted(request, "refused", "job_ineligible")
        if not isinstance(job, dict) or job.get("lifecycle") != "failed":
            return self._uncommitted(request, "refused", "job_ineligible")
        submission = job.get("submission")
        if not isinstance(submission, dict) or submission.get("version") != 1:
            # Historical jobs cannot authorize anything and must not create
            # target or broker lock directories merely to record a refusal.
            return self._uncommitted(request, "refused", "legacy_evidence")
        try:
            with self.repository.target_lock(request.target.key):
                state = self.repository.load()
                record = self.repository.target(state, request.target.key)
                replay = self.repository.replay(record, request)
                if replay is not None:
                    return self._replay_result(replay)
                active = record.get("active_operation")
                resuming_provisional = False
                if active is not None:
                    same_safe_observation = (
                        isinstance(active, dict) and
                        request.action is RecoveryAction.OBSERVE_RECONCILE and
                        active.get("request_id") == request.request_id and
                        active.get("request_digest") == request.digest and
                        active.get("action") == request.action.value and
                        active.get("phase") in {
                            "observation_pending", "reconciliation_provisional"} and
                        active.get("effect_entered") is False)
                    if not same_safe_observation:
                        if isinstance(active, dict) and active.get("effect_entered") is True:
                            return self._uncommitted(
                                request, "uncertain", "effect_unknown")
                        # A different crashed owner remains the first fence.
                        return self._uncommitted(request, "refused", "operation_busy")
                    resuming_provisional = (
                        active.get("phase") == "reconciliation_provisional")
                if (request.action is RecoveryAction.CONTINUE_EDGE and
                        isinstance(record.get("recovery_uncertainty"), dict)):
                    return self._uncommitted(request, "uncertain", "effect_unknown")
                consumed = record.get("consumed_observation_authority")
                if (request.action is RecoveryAction.OBSERVE_RECONCILE and
                        isinstance(consumed, dict)):
                    # Exact replay returned above. A distinct identity cannot
                    # consume the same failed apply observation authority twice.
                    return self._commit_refusal(
                        state, record, request, "mutation_required")
                if record["generation"] != request.expected_generation:
                    return self._commit_refusal(state, record, request, "generation_conflict")
                operation = record.get("hosting_operation")
                refusal = validate_job_binding(request, job, operation)
                if refusal:
                    return self._commit_refusal(state, record, request, refusal)
                try:
                    source_exact = self.source_check(operation) is True
                except Exception:
                    source_exact = False
                if not source_exact:
                    return self._commit_refusal(state, record, request, "dirty_source")
                try:
                    with self.authority_guard(request, operation):
                        with self.broker_guard(request):
                            if resuming_provisional:
                                return self._resume_provisional(
                                    state, record, request, operation)
                            self.repository.begin(state, request.target.key, request)
                            if request.action is RecoveryAction.CONTINUE_EDGE:
                                return self._continue_edge(
                                    state, record, request, operation)
                            return self._observe(state, record, request, operation)
                except RecoveryAuthorityError:
                    return self._commit_refusal(
                        state, record, request, "changed_target")
        except TimeoutError:
            return self._uncommitted(request, "refused", "operation_busy")
        except (OSError, ValueError) as exc:
            code = str(exc)
            if code not in {"binding_mismatch", "generation_conflict", "retention_full"}:
                code = "persistence_failed"
            family = "refused" if code != "persistence_failed" else "failed"
            return self._uncommitted(request, family, code)

    def _observe(self, state, record, request, operation):
        try:
            observation = self.observer(request, operation)
        except Exception:
            return self._commit_refusal(state, record, request, "observation_failed", family="failed")
        refusal, evidence = classify_observation(operation, observation)
        if refusal:
            return self._commit_refusal(state, record, request, refusal,
                                        evidence=evidence)
        # Publish only an explicitly non-authorizing provisional marker. It
        # advances no generation, exposes no receipt, and creates no terminal
        # attempt or edge evidence. The post-write observation below is what
        # closes FR-011 through durable commit.
        self.repository.provision_reconciliation(
            state, request.target.key, request,
            operation_digest=operation["digest"],
            evidence_id=evidence["evidence_id"])
        return self._resume_provisional(state, record, request, operation)

    def _resume_provisional(self, state, record, request, operation):
        provisional = record.get("recovery_provisional")
        if (not isinstance(provisional, dict) or
                provisional.get("request_id") != request.request_id or
                provisional.get("request_digest") != request.digest or
                provisional.get("operation_digest") != operation.get("digest") or
                provisional.get("expected_generation") != request.expected_generation or
                provisional.get("authorizing") is not False):
            return self._uncommitted(request, "failed", "persistence_failed")
        try:
            revalidated = self.observer(request, operation)
        except Exception:
            return self._commit_refusal(
                state, record, request, "observation_failed", family="failed")
        revalidation_refusal, fresh_evidence = classify_observation(
            operation, revalidated)
        if (revalidation_refusal or fresh_evidence is None or
                fresh_evidence.get("evidence_id") != provisional.get("evidence_id")):
            return self._commit_refusal(
                state, record, request, "evidence_changed",
                evidence=fresh_evidence)
        evidence = fresh_evidence
        generation = request.expected_generation + 1
        result = RecoveryResult(request, "success", "observation_reconciled",
                                generation, evidence["evidence_id"],
                                tuple([*evidence.get("phases", ()), *(
                                    {"phase": name, "state": "pending"}
                                    for name in evidence.get("pending_phases", ())) ]),
                                evidence_expires_at=(
                                    int(self.clock()) + self.evidence_ttl_seconds))
        committed = self.repository.commit(
            state, request.target.key, request, result,
            receipt={"schema_version": 1,
                     "operation_digest": operation["digest"],
                     "evidence_id": evidence["evidence_id"],
                     "resulting_generation": generation})
        # Stored in the same already-fsynced state object for subsequent
        # commits; commit() persists the receipt and the consumed marker.
        return committed

    def _continue_edge(self, state, record, request, operation):
        observation = next((item for item in record.get("recovery_attempts", [])
                            if item.get("request_id") == request.observation_request_id), None)
        refusal = validate_edge_request(
            request, observation, generation=record["generation"],
            governance_authorized=bool(self.governance_check(request)),
            now=int(self.clock()))
        if refusal:
            return self._commit_refusal(state, record, request, refusal)
        try:
            fresh = self.observer(request, operation)
        except Exception:
            return self._commit_refusal(state, record, request, "observation_failed", family="failed")
        refusal, evidence = classify_observation(operation, fresh)
        if refusal or evidence["evidence_id"] != request.evidence_id:
            return self._commit_refusal(state, record, request,
                                        refusal or "evidence_changed", evidence=evidence)
        if self.edge_adapter is None:
            return self._commit_refusal(state, record, request, "governance_unavailable")
        # Persist the exact effect-entry boundary before calling the adapter.
        # Process death after this write is permanently uncertain, never a
        # resumable observation owner.
        self.repository.mark_effect_entered(
            state, request.target.key, request)
        try:
            completed = self.edge_adapter(request, operation)
        except TimeoutError:
            return self._commit_effect_unknown(state, record, request)
        except Exception:
            # Once the adapter is entered, an exception cannot prove that no
            # external Caddy/DNS/certificate effect occurred. Fence it rather
            # than permit a different request to repeat the effect.
            return self._commit_effect_unknown(state, record, request)
        if completed is not True and not isinstance(completed, dict):
            return self._commit_effect_unknown(state, record, request)
        if isinstance(completed, dict):
            update = completed.get("record")
            if not isinstance(update, dict):
                return self._commit_effect_unknown(state, record, request)
            record.update(update)
        generation = request.expected_generation + 1
        result = RecoveryResult(request, "success", "edge_only_completed",
                                generation, evidence["evidence_id"],
                                tuple(evidence.get("phases", ())))
        try:
            committed = self.repository.commit(state, request.target.key, request, result)
        except (OSError, ValueError):
            # The edge may have changed even though its receipt did not commit.
            # begin() already durably left the active owner as the retry fence.
            return self._uncommitted(request, "uncertain", "effect_unknown")
        return committed

    def _commit_effect_unknown(self, state, record, request):
        try:
            return self._commit_refusal(
                state, record, request, "effect_unknown", family="uncertain")
        except (OSError, ValueError):
            # begin() remains durable and fences every retry even when the
            # uncertainty receipt itself cannot be committed.
            return self._uncommitted(request, "uncertain", "effect_unknown")

    def _commit_refusal(self, state, record, request, result_class,
                        *, family="refused", evidence=None):
        result = RecoveryResult(request, family, result_class,
                                record["generation"],
                                (evidence or {}).get("evidence_id"),
                                tuple((evidence or {}).get("phases", ())))
        return self.repository.commit(state, request.target.key, request, result)

    @staticmethod
    def _uncommitted(request, family, result_class):
        return RecoveryResult(request, family, result_class,
                              request.expected_generation).as_dict()

    @staticmethod
    def _replay_result(replay: dict) -> dict:
        if replay.get("result_family") != "success":
            return replay
        result = dict(replay)
        result["idempotent_replay"] = True
        if replay.get("action") == RecoveryAction.CONTINUE_EDGE.value:
            # An exact edge request replays its recorded terminal result. It
            # must not masquerade as receipt-only reconciliation.
            return result
        result["original_result_class"] = replay.get("result_class")
        result["result_class"] = "already_reconciled"
        return result
