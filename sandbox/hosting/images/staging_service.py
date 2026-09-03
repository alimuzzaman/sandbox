"""Feature 050 orchestration from durable admission through proof commit."""
from __future__ import annotations

from sandbox.secrets.models import SecretBrokerError
from sandbox.transports.remote_hosting_images import RemoteImageStageError
from .staging_models import LocalImageObservation, StageRequest, StageResult, StagedImageProof
from .staging_policy import admit_stage_request
from .staging_repository import StageRepository, StageRepositoryError
from .staging_worker import StageWorker, StageWorkerError


class ImageStagingService:
    """Own one staging flow; no activation/runtime/Compose capability is injected."""

    def __init__(self, *, repository: StageRepository, broker, worker: StageWorker) -> None:
        self.repository = repository; self.broker = broker; self.worker = worker

    @staticmethod
    def _failure(request: StageRequest, generation: int, code: str,
                 result_class: str = "failed") -> StageResult:
        return StageResult(1, False, result_class, code, request.request_id, generation)

    def status(self, request: StageRequest) -> StageResult:
        """Read one exact request without starting a helper or reading a secret."""
        current = self.repository.lookup(request.target.target_identity, request.request_id)
        if isinstance(current, StageResult):
            return current
        if isinstance(current, dict):
            return self._failure(request, current.get("generation", request.expected_generation),
                                 "accepted", "in_progress")
        return self._failure(request, request.expected_generation,
                             "acceptance_unknown", "uncertain")

    def stage(self, request: StageRequest, machine_policy) -> StageResult:
        admission = admit_stage_request(request, machine_policy)
        if not admission.ok or admission.policy is None:
            return self._failure(request, request.expected_generation, admission.code, "refused")
        try:
            decision, generation, replay = self.repository.accept(request)
        except StageRepositoryError as exc:
            return self._failure(request, request.expected_generation, exc.code, "refused")
        if replay is not None:
            return replay
        if decision != "accepted":
            code = "target_busy" if decision == "busy" else "request_conflict"
            return self._failure(request, generation, code, "refused")
        return self._execute_accepted(request, admission.policy, generation)

    def _execute_accepted(self, request: StageRequest, policy, generation: int) -> StageResult:
        prepared = None; broker_lease = None
        process_evidence = {"unit_inactive": True, "cgroup_empty_or_removed": True,
                            "not_launched": True}
        cleanup_evidence = {"complete": True}
        try:
            # The exact source snapshot and selected bytes are revision-bound
            # before any helper process or READY channel exists.
            broker_lease = self.broker.prepare_for_stage(
                recipient=policy.broker_recipient, binding_id=policy.broker_binding_id,
                binding_version=policy.broker_binding_version)
            self.repository.transition(request, "credential_pending")
            prepared = self.worker.prepare(request, policy)
            self.repository.transition(request, "helper_running", process={
                "unit_name": getattr(getattr(prepared, "frame", {}), "get", lambda *_: None)("unit_name"),
                "unit_inactive": False, "cgroup_empty_or_removed": False})

            def consume(credential: bytes):
                self.repository.transition(request, "pulling")
                return prepared.deliver(credential)

            observation, process, cleanup = broker_lease.consume(consume)
            broker_lease = None
            process_evidence = process; cleanup_evidence = cleanup
            self.repository.transition(request, "cleanup_pending", process=process, cleanup=cleanup)
            if process.get("unit_inactive") is not True \
                    or process.get("cgroup_empty_or_removed") is not True \
                    or cleanup != {"complete": True}:
                raise StageWorkerError("process_unproven", process=process, cleanup=cleanup)
            self.repository.transition(request, "observing")
            proof = StagedImageProof.create(request, policy, observation, generation)
            return self.repository.commit(request, StageResult(
                1, True, "success", "staged", request.request_id, generation, proof))
        except SecretBrokerError:
            code = "broker_unavailable"
        except RemoteImageStageError as exc:
            code = exc.code if exc.code == "helper_failed" else "helper_failed"
            process_evidence = exc.process or process_evidence
            cleanup_evidence = exc.cleanup or cleanup_evidence
        except StageWorkerError as exc:
            code = exc.code if exc.code in {"pull_failed", "cleanup_unproven",
                "observation_invalid", "process_unproven"} else "helper_failed"
            process_evidence = exc.process or process_evidence
            cleanup_evidence = exc.cleanup or cleanup_evidence
        except StageRepositoryError as exc:
            code = exc.code if exc.code in {"generation_conflict", "request_conflict"} \
                else "unknown_effect"
        except Exception:
            code = "unknown_effect"
        if broker_lease is not None:
            broker_lease.invalidate()
        if prepared is not None:
            try:
                cancelled = prepared.cancel()
            except Exception:
                cancelled = None
            if isinstance(cancelled, dict):
                # A completed helper response already carries authoritative
                # process and workspace-cleanup evidence.  Cancellation is a
                # best-effort second observation; once systemd has unloaded a
                # short-lived unit it may be unable to repeat that proof and
                # must not downgrade an otherwise safe terminal response.
                reported_safe = (
                    isinstance(process_evidence, dict)
                    and process_evidence.get("unit_inactive") is True
                    and process_evidence.get("cgroup_empty_or_removed") is True
                    and cleanup_evidence == {"complete": True}
                )
                cancelled_safe = (
                    cancelled.get("unit_inactive") is True
                    and cancelled.get("cgroup_empty_or_removed") is True
                    and cancelled.get("cleanup_complete") is True
                )
                if cancelled_safe or not reported_safe:
                    process_evidence = cancelled
                    cleanup_evidence = {"complete": cancelled.get("cleanup_complete") is True}
        process_safe = isinstance(process_evidence, dict) \
            and process_evidence.get("unit_inactive") is True \
            and process_evidence.get("cgroup_empty_or_removed") is True
        cleanup_safe = cleanup_evidence == {"complete": True}
        result_class = "failed" if process_safe and cleanup_safe and code != "unknown_effect" \
            else "uncertain"
        terminal_code = code if result_class == "failed" else (
            "cleanup_unproven" if not cleanup_safe else "unknown_effect")
        try:
            self.repository.transition(request, result_class, process=process_evidence,
                                       cleanup=cleanup_evidence)
        except StageRepositoryError:
            pass
        result = self._failure(request, generation, terminal_code, result_class)
        try:
            return self.repository.commit(request, result)
        except StageRepositoryError:
            return result

    def reconcile(self, request: StageRequest, machine_policy, observer) -> StageResult:
        """Resolve existing ownership from fresh evidence without duplicate effects."""
        admission = admit_stage_request(request, machine_policy)
        if not admission.ok or admission.policy is None:
            return self._failure(request, request.expected_generation, admission.code, "refused")
        current = self.repository.record_status(
            request.target.target_identity, request.request_id)
        if not isinstance(current, dict) or current.get("request_digest") != request.request_digest:
            return self._failure(request, request.expected_generation,
                                 "acceptance_unknown", "uncertain")
        generation = current.get("generation", request.expected_generation)
        terminal = self.repository.lookup(request.target.target_identity, request.request_id)
        if isinstance(terminal, StageResult) and terminal.result_class != "uncertain":
            return terminal
        evidence = observer(request, dict(current))
        if not isinstance(evidence, dict) \
                or evidence.get("unit_inactive") is not True \
                or evidence.get("cgroup_empty_or_removed") is not True \
                or evidence.get("cleanup_complete") is not True:
            return terminal if isinstance(terminal, StageResult) \
                else self.repository.fence_possible_effect(request)
        if current.get("effect_entered") is not True and evidence.get("exact_effect") is False:
            return self._execute_accepted(request, admission.policy, generation)
        observed = evidence.get("observation")
        try:
            observation = observed if type(observed) is LocalImageObservation \
                else LocalImageObservation(**observed)
        except (TypeError, ValueError):
            return terminal if isinstance(terminal, StageResult) \
                else self.repository.fence_possible_effect(request)
        if evidence.get("exact_effect") is not True:
            return terminal if isinstance(terminal, StageResult) \
                else self.repository.fence_possible_effect(request)
        proof = StagedImageProof.create(request, admission.policy, observation, generation)
        return self.repository.commit(request, StageResult(
            1, True, "success", "staged", request.request_id, generation, proof))
