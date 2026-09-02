"""Fail-closed orchestration for additive v2 batch staging."""
from __future__ import annotations

from sandbox.secrets.models import SecretBrokerError
from sandbox.transports.remote_hosting_images import RemoteImageStageError

from .staging_repository import StageRepositoryError
from .staging_v2 import (
    StageRequestSet, StageResultSet, StagedImageProofSet, admit_stage_request_set,
)
from .staging_worker import StageWorkerError


class ImagePlanSetStagingService:
    def __init__(self, *, repository, broker, worker) -> None:
        self.repository = repository; self.broker = broker; self.worker = worker

    @staticmethod
    def _failure(request: StageRequestSet, generation: int, code: str,
                 result_class: str = "failed") -> StageResultSet:
        return StageResultSet(2, False, result_class, code, request.request_id, generation)

    def status(self, request: StageRequestSet) -> StageResultSet:
        current = self.repository.lookup_for_request(request)
        if isinstance(current, StageResultSet):
            return current
        if isinstance(current, dict):
            return self._failure(request, current.get("generation", request.expected_generation),
                                 "accepted", "in_progress")
        return self._failure(request, request.expected_generation,
                             "acceptance_unknown", "uncertain")

    def stage(self, request: StageRequestSet, machine_policy) -> StageResultSet:
        policy, code = admit_stage_request_set(request, machine_policy)
        if policy is None:
            return self._failure(request, request.expected_generation, code, "refused")
        try:
            decision, generation, replay = self.repository.accept(request)
        except StageRepositoryError as exc:
            return self._failure(request, request.expected_generation, exc.code, "refused")
        if replay is not None:
            if isinstance(replay, StageResultSet): return replay
            return self._failure(request, generation, replay.code, replay.result_class)
        if decision != "accepted":
            return self._failure(request, generation,
                "target_busy" if decision == "busy" else "request_conflict", "refused")
        return self._execute_accepted(request, policy, generation)

    def _execute_accepted(self, request, policy, generation):
        prepared = None; broker_lease = None
        process = {"unit_inactive": True, "cgroup_empty_or_removed": True,
                   "not_launched": True}
        cleanup = {"complete": True}
        try:
            broker_lease = self.broker.prepare_for_stage(
                recipient=policy.broker_recipient, binding_id=policy.broker_binding_id,
                binding_version=policy.broker_binding_version)
            self.repository.transition(request, "credential_pending")
            prepared = self.worker.prepare(request, policy)
            self.repository.transition(request, "helper_running", process={
                "unit_name": prepared.frame.get("unit_name"), "unit_inactive": False,
                "cgroup_empty_or_removed": False})

            def consume(credential: bytes):
                self.repository.transition(request, "pulling")
                return prepared.deliver(credential)

            observation, process, cleanup = broker_lease.consume(consume)
            broker_lease = None
            self.repository.transition(request, "cleanup_pending", process=process, cleanup=cleanup)
            if process.get("unit_inactive") is not True \
                    or process.get("cgroup_empty_or_removed") is not True \
                    or cleanup != {"complete": True}:
                raise StageWorkerError("process_unproven", process=process, cleanup=cleanup)
            self.repository.transition(request, "observing")
            proof = StagedImageProofSet.create(request, policy, observation, generation)
            return self.repository.commit(request, StageResultSet(
                2, True, "success", "staged", request.request_id, generation, proof))
        except SecretBrokerError:
            code = "broker_unavailable"
        except RemoteImageStageError:
            code = "helper_failed"
        except StageWorkerError as exc:
            code = exc.code if exc.code in {"pull_failed", "cleanup_unproven",
                "observation_invalid", "process_unproven"} else "helper_failed"
            process = exc.process or process; cleanup = exc.cleanup or cleanup
        except StageRepositoryError as exc:
            code = exc.code if exc.code in {"generation_conflict", "request_conflict"} \
                else "unknown_effect"
        except Exception:
            code = "unknown_effect"
        if broker_lease is not None: broker_lease.invalidate()
        if prepared is not None:
            try: cancelled = prepared.cancel()
            except Exception: cancelled = None
            if isinstance(cancelled, dict):
                process = cancelled
                cleanup = {"complete": cancelled.get("cleanup_complete") is True}
        safe_process = isinstance(process, dict) \
            and process.get("unit_inactive") is True \
            and process.get("cgroup_empty_or_removed") is True
        safe_cleanup = cleanup == {"complete": True}
        result_class = "failed" if safe_process and safe_cleanup and code != "unknown_effect" \
            else "uncertain"
        terminal_code = code if result_class == "failed" else (
            "cleanup_unproven" if not safe_cleanup else "unknown_effect")
        try: self.repository.transition(request, result_class, process=process, cleanup=cleanup)
        except StageRepositoryError: pass
        result = self._failure(request, generation, terminal_code, result_class)
        try: return self.repository.commit(request, result)
        except StageRepositoryError: return result
