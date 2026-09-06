"""Fail-closed orchestration for additive v2 batch staging."""
from __future__ import annotations

from sandbox.secrets.models import SecretBrokerError
from sandbox.transports.remote_hosting_images import RemoteImageStageError

from .staging_repository import StageRepositoryError
from .staging_v2 import (
    PullFailure, StageRequestSet, StageResultSet, StagedImageProofSet, admit_stage_request_set,
)
from .staging_models import StagingContractError
from .staging_worker import StageDeliveryFailure, StageWorkerError
from .staging_worker import unit_name


class ImagePlanSetStagingService:
    def __init__(self, *, repository, broker, worker) -> None:
        self.repository = repository; self.broker = broker; self.worker = worker

    @staticmethod
    def _failure(request: StageRequestSet, generation: int, code: str,
                 result_class: str = "failed", pull_failure=None) -> StageResultSet:
        return StageResultSet(2, False, result_class, code, request.request_id, generation,
                              pull_failure=pull_failure)

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

    def reconcile_precredential_failure(self, request: StageRequestSet,
                                        machine_policy, observer) -> StageResultSet:
        """Safely close one exact pre-effect uncertainty; never replay its plan."""
        policy, code = admit_stage_request_set(request, machine_policy)
        if policy is None:
            return self._failure(request, request.expected_generation, code, "refused")
        current = self.repository.record_status(
            request.target.target_identity, request.request_id)
        terminal = self.repository.lookup_for_request(request)
        if isinstance(terminal, StageResultSet) and terminal.result_class != "uncertain":
            return terminal
        if type(current) is not dict or type(terminal) is not StageResultSet \
                or terminal.result_class != "uncertain" \
                or current.get("phase") != "uncertain" \
                or current.get("effect_entered") is not False \
                or current.get("request_id") != request.request_id \
                or current.get("request_digest") != request.request_digest \
                or current.get("generation") != terminal.generation \
                or type(current.get("ledger_revision")) is not int:
            return terminal if isinstance(terminal, StageResultSet) else self._failure(
                request, request.expected_generation, "acceptance_unknown", "uncertain")
        try:
            evidence = observer(request, dict(current))
        except Exception:
            return terminal
        expected_unit = unit_name(request.request_id, request.request_digest)
        expected = {"schema_version": 1, "request_id": request.request_id,
            "request_digest": request.request_digest, "generation": current["generation"],
            "ledger_revision": current["ledger_revision"], "unit_name": expected_unit,
            "load_state": "not-found", "active_state": "inactive", "sub_state": "dead",
            "description": expected_unit, "main_pid": "0", "control_group": "",
            "exact_effect": False, "unit_inactive": True,
            "cgroup_empty_or_removed": True, "cleanup_complete": True}
        alternate = {**expected, "description": ""}
        if type(evidence) is not dict or evidence not in (expected, alternate):
            return terminal
        try:
            return self.repository.close_precredential_uncertain(
                request, expected_ledger_revision=current["ledger_revision"])
        except (StageRepositoryError, OSError):
            return terminal

    def reconcile_posteffect_cleanup(self, request: StageRequestSet,
                                     machine_policy, observer) -> StageResultSet:
        """Close proven cleanup-only uncertainty without replaying any effect."""
        policy, code = admit_stage_request_set(request, machine_policy)
        if policy is None:
            return self._failure(request, request.expected_generation, code, "refused")
        current = self.repository.record_status(
            request.target.target_identity, request.request_id)
        terminal = self.repository.lookup_for_request(request)
        if isinstance(terminal, StageResultSet) and terminal.result_class != "uncertain":
            return terminal
        if type(current) is not dict or type(terminal) is not StageResultSet \
                or terminal.result_class != "uncertain" \
                or current.get("phase") != "uncertain" \
                or current.get("effect_entered") is not True \
                or current.get("request_id") != request.request_id \
                or current.get("request_digest") != request.request_digest \
                or current.get("generation") != terminal.generation \
                or type(current.get("ledger_revision")) is not int:
            return terminal if isinstance(terminal, StageResultSet) else self._failure(
                request, request.expected_generation, "acceptance_unknown", "uncertain")
        try:
            evidence = observer(request, dict(current))
        except Exception:
            return terminal
        expected = {"unit_inactive": True, "cgroup_empty_or_removed": True,
                    "workspace_absent": True}
        if type(evidence) is not dict or evidence != expected:
            return terminal
        try:
            return self.repository.close_posteffect_uncertain(
                request, expected_ledger_revision=current["ledger_revision"])
        except (StageRepositoryError, OSError):
            return terminal

    def reconcile_uncertain_failure(self, request: StageRequestSet, machine_policy,
                                    precredential_observer,
                                    posteffect_observer) -> StageResultSet:
        """Select the close-only observer allowed by the durable effect fence."""
        current = self.repository.record_status(
            request.target.target_identity, request.request_id)
        if type(current) is dict and current.get("effect_entered") is True:
            return self.reconcile_posteffect_cleanup(
                request, machine_policy, posteffect_observer)
        return self.reconcile_precredential_failure(
            request, machine_policy, precredential_observer)

    def _execute_accepted(self, request, policy, generation):
        prepared = None; broker_lease = None
        pull_failure = None
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
                try:
                    self.repository.transition(request, "pulling")
                    return prepared.deliver(credential)
                except RemoteImageStageError as exc:
                    return StageDeliveryFailure("remote", exc.code, exc.process, exc.cleanup)
                except StageWorkerError as exc:
                    return StageDeliveryFailure("worker", exc.code, exc.process, exc.cleanup,
                                                exc.pull_failure)

            delivered = broker_lease.consume(consume)
            if isinstance(delivered, StageDeliveryFailure):
                process = delivered.process or process
                cleanup = delivered.cleanup or cleanup
                if delivered.kind == "remote":
                    raise RemoteImageStageError(delivered.code,
                        process=delivered.process, cleanup=delivered.cleanup)
                raise StageWorkerError(delivered.code,
                    process=delivered.process, cleanup=delivered.cleanup,
                    pull_failure=delivered.pull_failure)
            observation, process, cleanup = delivered
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
        except RemoteImageStageError as exc:
            code = "helper_failed"
            process = exc.process or process; cleanup = exc.cleanup or cleanup
        except StageWorkerError as exc:
            code = exc.code if exc.code in {"pull_failed", "cleanup_unproven",
                "observation_invalid", "process_unproven"} else "helper_failed"
            if code == "pull_failed" and exc.pull_failure is not None:
                try: pull_failure = PullFailure.from_mapping(exc.pull_failure)
                except (StagingContractError, TypeError, ValueError): code = "helper_failed"
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
                # The helper response is the authoritative cleanup receipt.
                # Cancellation is only a recovery observation and can lose
                # the transient unit after a short-lived helper exits.  Do
                # not turn a safe, fully-proven response into uncertainty just
                # because that second observation cannot repeat its proof.
                reported_safe = (
                    isinstance(process, dict)
                    and process.get("unit_inactive") is True
                    and process.get("cgroup_empty_or_removed") is True
                    and cleanup == {"complete": True}
                )
                cancelled_safe = (
                    cancelled.get("unit_inactive") is True
                    and cancelled.get("cgroup_empty_or_removed") is True
                    and cancelled.get("cleanup_complete") is True
                )
                if cancelled_safe or not reported_safe:
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
        if result_class != "failed" or terminal_code != "pull_failed":
            pull_failure = None
        result = self._failure(request, generation, terminal_code, result_class,
                               pull_failure)
        try: return self.repository.commit(request, result)
        except StageRepositoryError: return result
