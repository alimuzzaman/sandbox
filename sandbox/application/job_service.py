"""Application boundary for durable job use cases.

Concrete submission, observation, cancellation, and retention behavior is added behind
this module so CLI and MCP adapters share one service contract.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from sandbox.jobs.models import (ArtifactQuery, JobSubmission, OutputProfile, OutputQuery, SourceIdentity,
                                 Health, output_profile_from_definition, validate_job_id)
from sandbox.jobs.output import JobOutputStore, present_output
from sandbox.jobs.health import classify
from sandbox.jobs.models import Lifecycle
from sandbox.jobs.process import (ProcessIdentity, capture_process_identity,
                                  signal_owned_process_group, verify_owned_process_identity,
                                  verify_process_identity)
from sandbox.jobs.scheduler import WorkspaceBusy


MAX_AGGREGATE_RESULT_BYTES = 262_144


class JobServiceProtocol(Protocol):
    def submit(self, submission): ...
    def get(self, job_id: str, *, reconcile: bool = True): ...
    def list(self, query): ...
    def read_output(self, job_id: str, query): ...


@dataclass
class JobService:
    """Foundational service composition; execution use cases are added in US1."""

    repository: Any
    storage: Any
    components: Any
    launcher: Any = None
    scheduler: Any = None
    runtime_selector: Any = None
    workspace_registry: Any = None
    sync_gateway: Any = None

    def submit(self, submission: JobSubmission):
        if submission.sync_relationship_id is not None:
            if self.sync_gateway is None:
                raise RuntimeError("synchronized_job_authority_unavailable")
            submission = self.sync_gateway.prepare_submission(submission)
        # Production composition supplies the durable workspace boundary.  It
        # must commit ownership before the job repository can acknowledge an
        # acceptance, otherwise a detached job can outlive the only metadata
        # capable of identifying its workspace.  The dependency stays optional
        # for compatibility adapters and isolated repository tests.
        if self.workspace_registry is not None:
            self.workspace_registry.ensure_submission(submission)
        row, replay = self.repository.accept(submission)
        if replay:
            return self._accepted(row, replay=True)
        if submission.compatibility_differences:
            self.repository.record_compatibility_differences(
                row["job_id"], list(submission.compatibility_differences))
        try:
            self.storage.job_dir(row["job_id"], create=True)
            descriptor = self._descriptor(row, submission)
            descriptor_path = self.storage.write_json_atomic(row["job_id"], "descriptor.json", descriptor)
            dependency_state, dependency_reason = self._dependency_state(row)
            if dependency_state == "blocked":
                row = self.repository.transition(row["job_id"], Lifecycle.CANCELLED,
                    termination_reason="dependency_failed",
                    result_json=__import__("json").dumps({"dependencies": list(submission.depends_on)}, sort_keys=True))
                return self._accepted(row, replay=False)
            if dependency_state != "ready":
                row = self.repository.transition(row["job_id"], Lifecycle.QUEUED,
                    queue_reason=dependency_reason or "dependency")
                return self._accepted(row, replay=False)
            if self.scheduler is not None:
                try:
                    self.scheduler.acquire(
                        row,
                        parallel_safe=(submission.parallel_safe or
                                       submission.workspace_mode == "isolated"),
                    )
                except WorkspaceBusy:
                    queue = self.scheduler.queue_details(row)
                    row = self.repository.transition(
                        row["job_id"], Lifecycle.QUEUED,
                        queue_reason="workspace_or_capacity_busy",
                        queue_position=queue["position"],
                    )
                    row["_queue_details"] = queue
                    return self._accepted(row, replay=False)
            self._launch(descriptor_path)
        except BaseException as exc:
            if self.scheduler is not None:
                self.scheduler.release(row["job_id"])
            self.repository.transition(row["job_id"], "failed", termination_reason="supervisor_launch_failed")
            raise RuntimeError("supervisor_launch_failed") from exc
        return self._accepted(row, replay=False)

    def _dependency_state(self, row: dict) -> tuple[str, str | None]:
        """Resolve label-based edges within a parent without touching child pipes."""
        try:
            labels = tuple(__import__("json").loads(row.get("depends_on_json") or "[]"))
        except (TypeError, ValueError):
            labels = ()
        if not labels:
            return "ready", None
        parent_id = row.get("parent_job_id")
        siblings = self.repository.children(parent_id) if parent_id else []
        by_label = {item["workspace_label"]: item for item in siblings}
        terminal = {item.value for item in (
            Lifecycle.SUCCEEDED, Lifecycle.FAILED, Lifecycle.TIMED_OUT,
            Lifecycle.CANCELLED, Lifecycle.INTERRUPTED,
        )}
        missing = [label for label in labels if label not in by_label]
        if missing:
            return "waiting", "dependency_missing"
        dependencies = [by_label[label] for label in labels]
        if any(item["lifecycle"] not in terminal for item in dependencies):
            return "waiting", "dependency"
        if row.get("failure_policy", "fail-fast") == "fail-fast" and any(
                item["lifecycle"] != Lifecycle.SUCCEEDED.value for item in dependencies):
            return "blocked", "dependency_failed"
        return "ready", None

    def _descriptor(self, row: dict, submission: JobSubmission) -> dict:
        nonce = os.urandom(32)
        # Runtime selection is part of the immutable submission decision.  A
        # supervisor must never reinterpret a managed job as host-executable
        # after a config/import/registry failure.
        execution_runtime = "host"
        if self.runtime_selector is not None:
            execution_runtime = (
                "managed_native" if self.runtime_selector(
                    row["project_root"], label="default",
                ) else "host"
            )
        return {"job_id": row["job_id"], "registry_path": str(self.repository.path),
                "runtime_dir": str(self.storage.root.parent), "argv": __import__("json").loads(row["command_json"]),
                "project_root": row["project_root"], "label": "default",
                "cwd": str(Path(row["project_root"]) / row["cwd_relative"]),
                "deadline_seconds": row["deadline_seconds"],
                "cancel_grace_seconds": row.get("cancel_grace_seconds", 20),
                "stall_seconds": row["stall_seconds"], "cancel_on_stall": bool(row["cancel_on_stall"]),
                "nonce_hash": hashlib.sha256(nonce).hexdigest(), "environment": None,
                "execution_runtime": execution_runtime,
                "artifact_paths": list(submission.artifact_paths),
                "generation": ({
                    "relationship_id": submission.sync_relationship_id,
                    "generation_id": submission.sync_generation_id,
                    "source_access": submission.source_access,
                } if submission.sync_relationship_id is not None else None)}

    def _launch(self, descriptor_path: Path) -> None:
        if self.launcher:
            self.launcher(descriptor_path)
            return
        # The CLI can be invoked through an absolute path over SSH, where the
        # caller's cwd is not the staged Sandbox checkout.  The detached child
        # must start in the package root for ``-m sandbox.jobs.supervisor`` to
        # resolve reliably after the parent exits.
        package_root = Path(__file__).resolve().parents[2]
        process = subprocess.Popen([sys.executable, "-m", "sandbox.jobs.supervisor", str(descriptor_path)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=package_root, start_new_session=True, close_fds=True)
        # This is intentionally a fire-and-forget control-plane process: its
        # durable descriptor and registry own lifecycle after acceptance. Avoid
        # retaining a parent Popen handle whose destructor would falsely warn
        # when the caller exits before the detached supervisor finishes.
        process._child_created = False  # type: ignore[attr-defined]

    @staticmethod
    def _accepted(row: dict, *, replay: bool) -> dict:
        try:
            job_id = validate_job_id(row.get("job_id"))
        except (AttributeError, TypeError, ValueError) as exc:
            # A durable acceptance without a repository identity cannot be
            # resumed, observed, or safely replayed. Fail at the boundary
            # rather than returning a misleading accepted response.
            raise RuntimeError("supervisor_acceptance_missing_job_id") from exc
        source = {
            "identity": row.get("source_identity"),
            "commit": row.get("source_commit"),
            "dirty_digest": row.get("source_dirty_digest"),
        }
        if not isinstance(source["identity"], str) or not source["identity"]:
            raise RuntimeError("supervisor_acceptance_missing_source_identity")
        source["dirty"] = bool(source["dirty_digest"])
        result = {"ok": True, "job_id": job_id, "status": "accepted", "kind": row["kind"],
                "target": {"kind": row["target_kind"], "remote": row["remote_name"]},
                "workspace": row["workspace_label"], "output_profile": row["output_profile"],
                "source": source,
                "deadline": {"seconds": row["deadline_seconds"], "source": row["deadline_source"],
                             "reminder": row.get("deadline_reminder")},
                "execution_policy": {
                    "profile": row["execution_profile"], "stall_seconds": row["stall_seconds"],
                    "deadline_seconds": row["deadline_seconds"],
                    "deadline_source": row["deadline_source"],
                    "deadline_reminder": row.get("deadline_reminder"),
                    "cancel_grace_seconds": row.get("cancel_grace_seconds", 20),
                    "cancel_on_stall": bool(row["cancel_on_stall"]),
                    "cleanup_policy": row["cleanup_policy"],
                    "provenance": json.loads(row.get("execution_policy_provenance_json") or "{}"),
                },
                "cleanup_policy": row["cleanup_policy"],
                "idempotent_replay": replay}
        if row.get("sync_relationship_id") is not None:
            result["generation"] = {
                "relationship_id": row["sync_relationship_id"],
                "generation_id": row.get("sync_generation_id"),
                "source_access": row.get("source_access"),
            }
        if row.get("lifecycle") == Lifecycle.QUEUED.value:
            result["queue"] = row.get("_queue_details") or {
                "reason": row.get("queue_reason") or "queued",
                "position": row.get("queue_position"),
            }
        elif row.get("lifecycle") in {item.value for item in (Lifecycle.CANCELLED, Lifecycle.FAILED)}:
            result["lifecycle"] = row["lifecycle"]
            result["termination_reason"] = row.get("termination_reason")
        return result

    def _is_aggregate(self, snapshot: dict) -> bool:
        return snapshot.get("kind") in {"matrix", "plan"} or bool(
            self.repository.children(snapshot["job_id"]))

    def get(self, job_id: str, *, reconcile: bool = True):
        snapshot = self.repository.snapshot(job_id)
        # A CI matrix's children are themselves kind="ci".  Treat only an
        # actual aggregate (or a dedicated matrix/plan record) as a parent;
        # otherwise a dependency-ready CI child would never reach its
        # scheduler/launcher during status reconciliation.
        if self._is_aggregate(snapshot):
            return self._get_parent(snapshot)
        if snapshot["lifecycle"] == Lifecycle.QUEUED.value:
            dependency_state, dependency_reason = self._dependency_state(snapshot)
            if dependency_state == "blocked":
                snapshot = self.repository.transition(snapshot["job_id"], Lifecycle.CANCELLED,
                    termination_reason="dependency_failed")
            elif dependency_state != "ready":
                snapshot["queue_reason"] = dependency_reason or snapshot.get("queue_reason") or "dependency"
                snapshot["queue"] = {
                    "reason": snapshot["queue_reason"], "position": None,
                    "blocking_jobs": [],
                }
                return snapshot
            if snapshot["lifecycle"] == Lifecycle.QUEUED.value:
                try:
                    if self.scheduler is not None:
                        self.scheduler.acquire(
                            snapshot,
                            parallel_safe=(bool(snapshot.get("parallel_safe")) or
                                           snapshot["workspace_mode"] == "isolated"),
                        )
                    self._launch(self.storage.job_dir(job_id) / "descriptor.json")
                    snapshot = self.repository.snapshot(job_id)
                except WorkspaceBusy:
                    snapshot["queue_reason"] = "workspace_or_capacity_busy"
                    if self.scheduler is not None:
                        snapshot["queue"] = self.scheduler.queue_details(snapshot)
                        snapshot["queue_position"] = snapshot["queue"]["position"]
        if reconcile:
            if self.scheduler is not None and snapshot["lifecycle"] in {"accepted", "queued", "running", "cancelling"}:
                self.scheduler.renew(job_id, deadline_seconds=snapshot["deadline_seconds"])
            health, evidence = classify(snapshot)
            # A fast child can disappear between the classifier's PID probe and
            # its supervisor's terminal transition. If the recorded supervisor
            # still owns its identity, retain ACTIVE/finalizing evidence and let
            # that supervisor publish the authoritative exit code instead of
            # manufacturing an orphaned interruption.
            if health in {Health.ORPHANED, Health.PROCESS_MISSING} and self._supervisor_is_owned(snapshot):
                health = Health.ACTIVE
                evidence = {**evidence, "child_alive": False,
                            "reasons": ["recorded child exited while its verified supervisor finalizes the result"]}
            if snapshot["lifecycle"] not in {item.value for item in (Lifecycle.SUCCEEDED, Lifecycle.FAILED, Lifecycle.TIMED_OUT, Lifecycle.CANCELLED, Lifecycle.INTERRUPTED)}:
                self.repository.set_health(job_id, health, evidence)
                interruption_reasons = {
                    "supervisor_unresponsive": "supervisor_heartbeat_stale",
                    "orphaned": "orphaned_process_identity",
                    "process_missing": "child_process_missing",
                }
                # A cancelling job has already recorded a verified owner intent.
                # Its supervisor must retain control of the terminal outcome after
                # the process group exits, rather than a concurrent status read
                # relabeling it as an unrelated interruption.
                reason = interruption_reasons.get(health.value) if snapshot["lifecycle"] == Lifecycle.RUNNING.value else None
                if reason is not None:
                    try:
                        self.repository.transition(job_id, Lifecycle.INTERRUPTED,
                            termination_reason=reason, output_completeness="partial",
                            result_json=json.dumps({"reconciled": True,
                                "evidence": evidence.get("reasons", [])}, sort_keys=True))
                    except ValueError:
                        pass
                snapshot = self.repository.snapshot(job_id)
            snapshot["health"] = health.value
            snapshot["health_evidence"] = evidence
        if self.scheduler is not None and snapshot["lifecycle"] in {item.value for item in (Lifecycle.SUCCEEDED, Lifecycle.FAILED, Lifecycle.TIMED_OUT, Lifecycle.CANCELLED, Lifecycle.INTERRUPTED)}:
            self.scheduler.release(job_id)
        if self.scheduler is not None and snapshot["lifecycle"] == Lifecycle.QUEUED.value:
            queue = self.scheduler.queue_details(snapshot)
            snapshot["queue"] = queue
            snapshot["queue_position"] = queue["position"]
        return snapshot

    @staticmethod
    def _supervisor_is_owned(snapshot: dict) -> bool:
        process = snapshot.get("process") or {}
        pid = process.get("supervisor_pid")
        start = process.get("supervisor_start_identity")
        boot = process.get("host_boot_id")
        nonce = process.get("supervisor_nonce_hash")
        if not pid or not start or not boot or not nonce:
            return False
        observed = capture_process_identity(int(pid))
        if observed is None:
            return False
        expected = ProcessIdentity(boot, int(pid), start, nonce)
        observed = ProcessIdentity(observed.host_boot_id, observed.pid,
                                   observed.start_identity, nonce,
                                   observed.process_group_id)
        return verify_process_identity(expected, observed)

    def _get_parent(self, snapshot: dict) -> dict:
        """Reconcile original aggregate members; expose retries separately."""
        members = self.repository.children(snapshot["job_id"])
        original_rows = [child for child in members if not child.get("retry_of_job_id")]
        retry_rows = [child for child in members if child.get("retry_of_job_id")]
        children = [self.get(child["job_id"]) for child in original_rows]
        retry_attempts = [self.get(child["job_id"]) for child in retry_rows]
        if snapshot.get("failure_policy", "fail-fast") == "fail-fast" and any(
                child["lifecycle"] in {Lifecycle.FAILED.value, Lifecycle.TIMED_OUT.value,
                                        Lifecycle.INTERRUPTED.value} for child in children):
            for child in children:
                if child["lifecycle"] in {Lifecycle.ACCEPTED.value, Lifecycle.QUEUED.value,
                                           Lifecycle.RUNNING.value, Lifecycle.CANCELLING.value}:
                    try:
                        self.cancel(child["job_id"])
                    except RuntimeError:
                        pass
            children = [self.repository.snapshot(child["job_id"]) for child in original_rows]
        terminal = {item.value for item in (
            Lifecycle.SUCCEEDED, Lifecycle.FAILED, Lifecycle.TIMED_OUT,
            Lifecycle.CANCELLED, Lifecycle.INTERRUPTED,
        )}
        states = [child["lifecycle"] for child in children]
        if not states:
            lifecycle = snapshot["lifecycle"]
        elif all(state in terminal for state in states):
            if any(state == Lifecycle.FAILED.value for state in states):
                lifecycle = Lifecycle.FAILED.value
            elif any(state == Lifecycle.TIMED_OUT.value for state in states):
                lifecycle = Lifecycle.TIMED_OUT.value
            elif any(state == Lifecycle.CANCELLED.value for state in states):
                lifecycle = Lifecycle.CANCELLED.value
            elif any(state == Lifecycle.INTERRUPTED.value for state in states):
                lifecycle = Lifecycle.INTERRUPTED.value
            else:
                lifecycle = Lifecycle.SUCCEEDED.value
        elif any(state in {Lifecycle.RUNNING.value, Lifecycle.CANCELLING.value} for state in states):
            lifecycle = Lifecycle.RUNNING.value
        else:
            lifecycle = Lifecycle.QUEUED.value
        current = snapshot["lifecycle"]
        normalized = self._normalized_aggregate_result(children, lifecycle)
        if current not in terminal and lifecycle != current:
            try:
                # A newly accepted parent can observe already-terminal children
                # on its first read. Preserve lifecycle validation by publishing
                # the intermediate queued state before its terminal result.
                if current == Lifecycle.ACCEPTED.value and lifecycle in terminal:
                    snapshot = self.repository.transition(snapshot["job_id"], Lifecycle.QUEUED)
                fields = {"result_json": json.dumps(normalized, sort_keys=True)} if lifecycle in terminal else {}
                snapshot = self.repository.transition(snapshot["job_id"], lifecycle, **fields)
            except ValueError:
                # A child can finish between the read and transition. The next
                # status request will reconcile from the authoritative children.
                snapshot = self.repository.snapshot(snapshot["job_id"])
        persisted = self._decode_result(snapshot.get("result_json"))
        result = persisted if snapshot["lifecycle"] in terminal and persisted is not None else normalized
        aggregate = self._aggregate_result(children)
        effective_lifecycle = snapshot["lifecycle"]
        return {**snapshot, "children": children, "retry_attempts": retry_attempts,
                "aggregate": aggregate, "result": result,
                "health": "terminal" if effective_lifecycle in terminal else (
                    "active" if effective_lifecycle == "running" else "quiet")}

    @staticmethod
    def _decode_result(value: object) -> dict | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            result = json.loads(value)
        except (TypeError, ValueError):
            return None
        return result if isinstance(result, dict) else None

    @staticmethod
    def _aggregate_result(children: list[dict]) -> dict:
        counts: dict[str, int] = {}
        for child in children:
            state = child["lifecycle"]
            counts[state] = counts.get(state, 0) + 1
        return {"children": len(children), "counts": counts,
                "passed": counts.get(Lifecycle.SUCCEEDED.value, 0),
                "failed": sum(counts.get(state, 0) for state in (
                    Lifecycle.FAILED.value, Lifecycle.TIMED_OUT.value,
                    Lifecycle.CANCELLED.value, Lifecycle.INTERRUPTED.value,
                ))}

    @classmethod
    def _normalized_aggregate_result(cls, children: list[dict], conclusion: str) -> dict:
        """Build bounded terminal summary; detailed children stay outside result_json."""
        summary = cls._aggregate_result(children)
        terminal = {
            Lifecycle.SUCCEEDED.value, Lifecycle.FAILED.value, Lifecycle.TIMED_OUT.value,
            Lifecycle.CANCELLED.value, Lifecycle.INTERRUPTED.value,
        }
        references = []
        for child in children:
            reference = {
                "job_id": child["job_id"],
                "retry_of_job_id": child.get("retry_of_job_id"),
                "attempt": child.get("attempt"),
                "kind": child.get("kind"),
                "workspace": child.get("workspace_label"),
                "lifecycle": child.get("lifecycle"),
                "exit_code": child.get("exit_code"),
                "termination_reason": child.get("termination_reason"),
                "output_completeness": child.get("output_completeness"),
                "artifact_count": len(child.get("artifacts", ())),
                "compatibility_difference_count": len(child.get("compatibility_differences", ())),
                "cleanup": {"policy": child.get("cleanup_policy"), "state": child.get("cleanup_state")},
            }
            references.append(reference)
        base = {**summary, "kind": "aggregate", "conclusion": conclusion,
                "complete": all(item.get("lifecycle") in terminal for item in children)}
        if any(item.get("kind") == "ci" for item in children):
            base["context"] = cls._ci_result_context(children)

        def candidate(count: int) -> dict:
            return {**base, "child_outcomes": references[:count],
                    "child_outcomes_truncated": count < len(references),
                    "child_outcomes_returned": count}

        low, high = 0, len(references)
        while low < high:
            middle = (low + high + 1) // 2
            encoded = json.dumps(candidate(middle), sort_keys=True).encode()
            if len(encoded) <= MAX_AGGREGATE_RESULT_BYTES:
                low = middle
            else:
                high = middle - 1
        result = candidate(low)
        if len(json.dumps(result, sort_keys=True).encode()) > MAX_AGGREGATE_RESULT_BYTES:
            raise RuntimeError("aggregate_result_limit_exceeded")
        return result

    @staticmethod
    def _ci_result_context(children: list[dict]) -> dict:
        """Keep the CI execution facts needed to interpret a retained parent result.

        This is deliberately a small, immutable summary rather than a duplicate of
        each child snapshot.  The child records remain available through normal job
        status calls, while the terminal parent result survives later cleanup.
        """
        ci_children = [item for item in children if item.get("kind") == "ci"]
        first = ci_children[0] if ci_children else {}
        source = {"identity": first.get("source_identity")}
        if first.get("source_commit") is not None:
            source["commit"] = first["source_commit"]
        if first.get("source_dirty_digest") is not None:
            source["dirty_digest"] = first["source_dirty_digest"]

        workflows: list[str] = []
        accepted: list[str] = []
        safe_mode_skips: list[str] = []
        graph_children = []
        for child in ci_children:
            try:
                argv = json.loads(child.get("command_json") or "[]")
            except (TypeError, ValueError):
                argv = []
            for value in argv:
                if (isinstance(value, str) and ".github/workflows/" in value and
                        value.endswith((".yml", ".yaml")) and value not in workflows):
                    workflows.append(value)
            for difference in child.get("compatibility_differences", ()):
                identifier = difference.get("difference_id") or difference.get("id")
                if not identifier or not difference.get("accepted"):
                    continue
                if identifier not in accepted:
                    accepted.append(identifier)
                if identifier.startswith("safe-mode:") and identifier not in safe_mode_skips:
                    safe_mode_skips.append(identifier)
            if len(graph_children) < 512:
                graph_children.append({"job_id": child["job_id"],
                                       "workspace": child.get("workspace_label")})

        # These caps leave room for the separately bounded child-outcome list.
        return {
            "engine": {"name": "act", "version": "unobserved"},
            "workflows": workflows[:128],
            "source": source,
            "graph": {"children": graph_children,
                      "children_truncated": len(ci_children) > len(graph_children)},
            "accepted_differences": accepted[:512],
            "safe_mode_skips": safe_mode_skips[:512],
        }

    def list(self, query=None):
        query = dict(query or {})
        return self.repository.list(**query)

    def reconcile_startup(self, *, limit: int = 200) -> dict:
        """Reconcile active jobs whose supervisor or child identity is gone.

        A process that survives a supervisor restart is not automatically healthy:
        without a matching durable ownership record it is unsafe to claim success or
        send signals to it.  The best available output remains retained and the job
        is explicitly interrupted for later inspection.
        """
        interrupted = []
        for row in self.repository.list(limit=limit):
            if row["lifecycle"] not in {Lifecycle.RUNNING.value, Lifecycle.CANCELLING.value}:
                continue
            process = self.repository.snapshot(row["job_id"]).get("process") or {}
            supervisor_pid = process.get("supervisor_pid")
            if not supervisor_pid or not process.get("supervisor_start_identity"):
                try:
                    self.repository.transition(row["job_id"], Lifecycle.INTERRUPTED,
                        termination_reason="missing_supervisor_identity", output_completeness="unknown",
                        result_json=__import__("json").dumps({
                            "reconciled": True, "evidence": "active job has no durable supervisor identity"
                        }, sort_keys=True))
                    if self.scheduler is not None:
                        self.scheduler.release(row["job_id"])
                    interrupted.append(row["job_id"])
                except ValueError:
                    pass
                continue
            observed = capture_process_identity(int(supervisor_pid))
            if observed is not None:
                observed = ProcessIdentity(observed.host_boot_id, observed.pid,
                    observed.start_identity, process["supervisor_nonce_hash"], observed.process_group_id)
            expected = ProcessIdentity(process["host_boot_id"], int(supervisor_pid),
                process["supervisor_start_identity"], process["supervisor_nonce_hash"])
            if verify_process_identity(expected, observed):
                child_pid = process.get("child_pid")
                child_start = process.get("child_start_identity")
                if not child_pid or not child_start:
                    continue
                child_observed = capture_process_identity(int(child_pid))
                if child_observed is not None:
                    child_observed = ProcessIdentity(child_observed.host_boot_id, child_observed.pid,
                        child_observed.start_identity, process["supervisor_nonce_hash"],
                        child_observed.process_group_id)
                child_expected = ProcessIdentity(process["host_boot_id"], int(child_pid), child_start,
                    process["supervisor_nonce_hash"], process.get("child_pgid"))
                if verify_process_identity(child_expected, child_observed):
                    continue
                # A fast child can exit after the verified supervisor records its
                # identity but before that supervisor publishes the terminal
                # result. The live supervisor remains the only safe owner of that
                # transition. A different live process at the recorded PID is a
                # real ownership mismatch and must still interrupt immediately.
                if child_observed is None:
                    continue
                reason = "child_process_identity_mismatch"
                try:
                    self.repository.transition(row["job_id"], Lifecycle.INTERRUPTED,
                        termination_reason=reason, output_completeness="partial",
                        result_json=__import__("json").dumps({
                            "reconciled": True,
                            "evidence": "recorded child identity no longer matches",
                        }, sort_keys=True))
                    if self.scheduler is not None:
                        self.scheduler.release(row["job_id"])
                    interrupted.append(row["job_id"])
                except ValueError:
                    pass
                continue
            try:
                self.repository.transition(row["job_id"], Lifecycle.INTERRUPTED,
                    termination_reason="supervisor_lost", output_completeness="partial",
                    result_json=__import__("json").dumps({
                        "reconciled": True, "evidence": "recorded supervisor identity no longer matches"
                    }, sort_keys=True))
                if self.scheduler is not None:
                    self.scheduler.release(row["job_id"])
                interrupted.append(row["job_id"])
            except ValueError:
                pass
        stale = self.scheduler.reconcile_stale() if self.scheduler is not None else []
        return {"ok": True, "interrupted": interrupted, "released_leases": stale}

    def read_output(self, job_id: str, query: OutputQuery | None = None):
        query = query or OutputQuery()
        snapshot = self.repository.snapshot(job_id)
        if any(not stream.get("available", True) for stream in snapshot.get("output", ())):
            raise RuntimeError("output_unavailable")
        # Resolve presentation policy before opening retained bytes.  A custom
        # profile's byte/event budget is an observation cap, not merely a
        # post-render truncation hint; this keeps source reads bounded as well.
        profile = self._output_profile(snapshot, query.profile)
        bounded = replace(
            query,
            max_bytes=min(query.max_bytes, profile.max_bytes),
            max_events=min(query.max_events, profile.max_events),
        )
        page = JobOutputStore(self.storage, self.repository, job_id).read(bounded)
        return present_output(page, profile)

    def _output_profile(self, snapshot: dict, name: str) -> OutputProfile:
        """Resolve a declarative presentation profile from the job composition."""
        from sandbox.config.runtime import BUILTIN_OUTPUT_PROFILES

        definitions = dict(BUILTIN_OUTPUT_PROFILES)
        if self.components is not None:
            spec = self.components.get("profiles") if hasattr(self.components, "get") else None
            configured = getattr(spec, "component", {}).get("output", {}) if spec is not None else {}
            if isinstance(configured, dict):
                definitions.update(configured)
        canonical = self.repository.submission_snapshot(snapshot["job_id"])
        if canonical and name == canonical.get("output_profile"):
            definition = canonical.get("output_profile_definition")
            if definition:
                return output_profile_from_definition(name, definition)
        definition = definitions.get(name)
        if not isinstance(definition, dict):
            raise RuntimeError("unknown_output_profile")
        return output_profile_from_definition(name, definition)

    def cancel(self, job_id: str, *, force: bool = False):
        snapshot = self.repository.snapshot(job_id)
        if self._is_aggregate(snapshot):
            children = self.repository.children(job_id)
            cancelled = []
            for child in children:
                if child["lifecycle"] not in {item.value for item in (
                    Lifecycle.SUCCEEDED, Lifecycle.FAILED, Lifecycle.TIMED_OUT,
                    Lifecycle.CANCELLED, Lifecycle.INTERRUPTED,
                )}:
                    cancelled.append(self.cancel(child["job_id"], force=force))
            return self._get_parent(self.repository.snapshot(job_id)) | {"cancelled_children": cancelled}
        if snapshot["lifecycle"] in {item.value for item in (Lifecycle.SUCCEEDED, Lifecycle.FAILED, Lifecycle.TIMED_OUT, Lifecycle.CANCELLED, Lifecycle.INTERRUPTED)}:
            raise RuntimeError("already_terminal")
        process = snapshot.get("process") or {}
        if not process.get("child_pid") or not process.get("child_pgid"):
            if snapshot["lifecycle"] in {Lifecycle.ACCEPTED.value, Lifecycle.QUEUED.value}:
                return self.repository.transition(job_id, Lifecycle.CANCELLED,
                    termination_reason="cancelled_before_process_start")
            raise RuntimeError("process_identity_mismatch")
        identity = ProcessIdentity(process["host_boot_id"], int(process["child_pid"]),
            process["child_start_identity"], process["supervisor_nonce_hash"], int(process["child_pgid"]))
        # Verify before publishing cancellation intent.  Publishing it first lets the
        # supervisor classify a concurrently reaped child as cancelled, rather than
        # racing from ``failed`` back to ``cancelling`` after signal delivery.
        if not verify_owned_process_identity(identity):
            raise RuntimeError("process_identity_mismatch")
        self.repository.transition(job_id, Lifecycle.CANCELLING)
        # A child can still exit in the short interval after verification.  The
        # supervisor sees the persisted intent and finalizes it as cancelled.
        signal_owned_process_group(identity, 9 if force else 15)
        return self.repository.snapshot(job_id)

    def list_artifacts(self, job_id: str):
        return self.repository.snapshot(job_id)["artifacts"]

    def read_metrics(self, job_id: str, *, limit: int = 500):
        from sandbox.jobs.metrics import read
        metrics = self.repository.snapshot(job_id).get("metrics")
        if metrics is not None and not metrics.get("available", True):
            raise RuntimeError("metrics_unavailable")
        return {"ok": True, "job_id": job_id, "samples": read(self.storage, job_id, limit=limit)}

    def get_artifact(self, job_id: str, artifact_id: str, *, offset: int = 0, max_bytes: int = 1_048_576) -> bytes:
        query = ArtifactQuery(artifact_id=artifact_id, offset=offset, max_bytes=max_bytes, encoding="bytes")
        for artifact in self.list_artifacts(job_id):
            if artifact["artifact_id"] == artifact_id:
                if artifact.get("status", "available") != "available":
                    raise RuntimeError("artifact_unavailable")
                path = self.storage.job_dir(job_id) / artifact["stored_relative_path"]
                with path.open("rb") as handle:
                    handle.seek(query.offset)
                    return handle.read(query.max_bytes)
        raise RuntimeError("artifact_not_found")

    def retry(self, job_id: str, *, request_id: str | None = None):
        previous = self.repository.get(job_id)
        if previous["kind"] in {"matrix", "plan"} or self.repository.children(job_id):
            raise RuntimeError("aggregate_retry_unsupported")
        if previous["lifecycle"] not in {item.value for item in (
                Lifecycle.SUCCEEDED, Lifecycle.FAILED, Lifecycle.TIMED_OUT,
                Lifecycle.CANCELLED, Lifecycle.INTERRUPTED)}:
            raise RuntimeError("job_not_terminal")
        canonical = self.repository.submission_snapshot(job_id)
        if canonical is not None:
            source = canonical["source"]
            submission = JobSubmission(
                kind=canonical["kind"], project_root=canonical["project_root"],
                project_identity=canonical["project_identity"], target_kind=canonical["target_kind"],
                remote_name=canonical.get("remote_name"), workspace_label=canonical["workspace_label"],
                workspace_mode=canonical["workspace_mode"], argv=tuple(canonical["argv"]),
                deadline_seconds=canonical["deadline_seconds"],
                source=SourceIdentity(source["identity"], source.get("commit"), source.get("dirty_digest")),
                request_id=request_id, retry_of_job_id=job_id,
                # Standalone jobs remain standalone. CI/matrix children retain
                # their actual parent, never a guessed root/self relationship.
                parent_job_id=previous.get("parent_job_id"),
                attempt=int(previous["attempt"]) + 1, cwd_relative=canonical["cwd_relative"],
                execution_profile=canonical["execution_profile"], output_profile=canonical["output_profile"],
                output_profile_definition=canonical.get("output_profile_definition"),
                deadline_source=canonical["deadline_source"], stall_seconds=canonical["stall_seconds"],
                deadline_reminder=canonical.get("deadline_reminder"),
                cancel_grace_seconds=canonical.get("cancel_grace_seconds", 20),
                cancel_on_stall=bool(canonical["cancel_on_stall"]),
                cleanup_policy=canonical["cleanup_policy"],
                execution_policy_provenance=canonical.get("execution_policy_provenance"),
                environment_keys=tuple(canonical.get("environment_keys", ())),
                artifact_paths=tuple(canonical.get("artifact_paths", ())),
                depends_on=tuple(canonical.get("depends_on", ())),
                failure_policy=canonical.get("failure_policy", "fail-fast"),
                compatibility_differences=tuple(canonical.get("compatibility_differences", ())),
            )
        else:
            # Safe legacy fallback for rows accepted before schema v3. Only
            # fields historically persisted in bounded columns/tables can be
            # reconstructed; absent artifact declarations are not invented.
            snapshot = self.repository.snapshot(job_id)
            differences = tuple({
                "id": item["difference_id"], "workflow": item.get("workflow_path", ""),
                "location": item.get("location", ""), "severity": item.get("severity", "notice"),
                "accepted": bool(item.get("accepted")), "detail": item.get("detail", ""),
                "catalog_version": item.get("catalog_version", "unknown"),
            } for item in snapshot.get("compatibility_differences", ()))
            submission = JobSubmission(
                kind=previous["kind"], project_root=previous["project_root"],
                project_identity=previous["project_identity"], target_kind=previous["target_kind"],
                remote_name=previous["remote_name"], workspace_label=previous["workspace_label"],
                workspace_mode=previous["workspace_mode"], argv=tuple(json.loads(previous["command_json"])),
                deadline_seconds=previous["deadline_seconds"],
                source=SourceIdentity(previous["source_identity"], previous["source_commit"],
                                      previous["source_dirty_digest"]),
                request_id=request_id, retry_of_job_id=job_id,
                parent_job_id=previous.get("parent_job_id"), attempt=int(previous["attempt"]) + 1,
                cwd_relative=previous["cwd_relative"], execution_profile=previous["execution_profile"],
                output_profile=previous["output_profile"], deadline_source=previous["deadline_source"],
                deadline_reminder=previous.get("deadline_reminder"), stall_seconds=previous["stall_seconds"],
                cancel_grace_seconds=previous.get("cancel_grace_seconds", 20),
                cancel_on_stall=bool(previous["cancel_on_stall"]),
                cleanup_policy=previous["cleanup_policy"],
                execution_policy_provenance=json.loads(previous.get("execution_policy_provenance_json") or "{}"),
                environment_keys=tuple(json.loads(previous["environment_keys_json"])),
                depends_on=tuple(json.loads(previous.get("depends_on_json") or "[]")),
                failure_policy=previous.get("failure_policy", "fail-fast"),
                compatibility_differences=differences,
            )
        return self.submit(submission)

    def cleanup(self, job_id: str, *, logs: bool = True, artifacts: bool = True,
                metrics: bool = True) -> dict:
        import shutil
        state = self.repository.get(job_id)
        if state["lifecycle"] not in {item.value for item in (Lifecycle.SUCCEEDED, Lifecycle.FAILED, Lifecycle.TIMED_OUT, Lifecycle.CANCELLED, Lifecycle.INTERRUPTED)}:
            raise RuntimeError("active_job_protected")
        directory = self.storage.job_dir(job_id)
        removed = []
        if logs:
            output = directory / "output"
            if output.exists(): shutil.rmtree(output); removed.append("logs")
        if artifacts:
            artifact_dir = directory / "artifacts"
            if artifact_dir.exists(): shutil.rmtree(artifact_dir); removed.append("artifacts")
        if metrics:
            metric_file = directory / "metrics.jsonl"
            metric_dir = directory / "metrics"
            metric_removed = False
            if metric_file.exists():
                metric_file.unlink()
                metric_removed = True
            if metric_dir.exists():
                shutil.rmtree(metric_dir)
                metric_removed = True
            if metric_removed:
                removed.append("metrics")
        self.repository.mark_retained_metadata_unavailable(
            job_id, logs=logs, artifacts=artifacts, metrics=metrics)
        remaining = any((directory / name).exists() for name in ("output", "artifacts", "metrics", "metrics.jsonl"))
        cleanup_state = "retained" if remaining else "completed"
        self.repository.set_cleanup_state(job_id, cleanup_state)
        return {"ok": True, "job_id": job_id, "removed": removed, "cleanup_state": cleanup_state}

    def retention_sweep(self, *, retention_days: int = 7, limit: int = 200,
                        storage_pressure: bool = False) -> dict:
        if isinstance(retention_days, bool) or not isinstance(retention_days, int) or retention_days < 0:
            raise ValueError("retention_days must be a non-negative whole number")
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        cleaned = []
        rows = self.repository.list(limit=limit)
        if storage_pressure and not self.storage.is_under_pressure():
            storage_pressure = False
        if storage_pressure:
            rows.sort(key=lambda item: item.get("finished_at") or item.get("accepted_at") or "")
        for row in rows:
            if row["lifecycle"] not in {item.value for item in (
                Lifecycle.SUCCEEDED, Lifecycle.FAILED, Lifecycle.TIMED_OUT,
                Lifecycle.CANCELLED, Lifecycle.INTERRUPTED,
            )} or not row.get("finished_at"):
                continue
            try:
                finished = datetime.fromisoformat(row["finished_at"].replace("Z", "+00:00"))
            except ValueError:
                continue
            if (storage_pressure or finished <= cutoff) and row.get("cleanup_state") != "completed":
                cleaned.append(self.cleanup(row["job_id"]))
            if storage_pressure and not self.storage.is_under_pressure():
                break
        return {"ok": True, "retention_days": retention_days,
                "storage_pressure": storage_pressure, "cleaned": cleaned}

    def submit_matrix(self, submissions: list[JobSubmission], *, allow_project_variants: bool = False) -> dict:
        if not submissions:
            raise ValueError("matrix requires at least one child submission")
        first = submissions[0]
        if any(item.target_kind != first.target_kind or item.remote_name != first.remote_name or
               (not allow_project_variants and item.project_root != first.project_root)
               for item in submissions):
            raise ValueError("matrix children must share one target and project")
        submissions = self._order_matrix_submissions(submissions)
        parent = JobSubmission(
            kind="matrix", project_root=first.project_root,
            project_identity=first.project_identity, target_kind=first.target_kind,
            remote_name=first.remote_name, workspace_label="matrix-parent",
            argv=("sandbox-matrix-parent",), deadline_seconds=max(item.deadline_seconds for item in submissions),
            source=first.source, workspace_mode="persistent", output_profile=first.output_profile,
            execution_profile=first.execution_profile, deadline_source=first.deadline_source,
            failure_policy="continue" if all(item.failure_policy == "continue" for item in submissions)
            else "fail-fast",
        )
        parent_row, replay = self.repository.accept(parent)
        if replay:
            return self._get_parent(parent_row)
        self.storage.job_dir(parent_row["job_id"], create=True)
        accepted = []
        for submission in submissions:
            if submission.workspace_mode != "isolated":
                raise ValueError("matrix children require isolated workspaces")
            accepted.append(self.submit(replace(submission, parent_job_id=parent_row["job_id"])))
        return {"ok": True, "status": "accepted", "kind": "matrix", "parent_job_id": parent_row["job_id"],
                "children": accepted, "summary": {"submitted": len(accepted)}}

    @staticmethod
    def _order_matrix_submissions(submissions: list[JobSubmission]) -> list[JobSubmission]:
        """Topologically order label-based matrix edges before acceptance.

        The registry stores edges as stable workspace labels so a remote control
        request can be replayed without exposing internal job IDs. Ordering the
        batch first also handles YAML where a dependent job is declared before
        its prerequisite.
        """
        by_label = {item.workspace_label: item for item in submissions}
        if len(by_label) != len(submissions):
            raise ValueError("matrix workspace labels must be unique")
        pending = list(submissions)
        ordered: list[JobSubmission] = []
        completed: set[str] = set()
        while pending:
            if any(label not in by_label for item in pending for label in item.depends_on):
                raise ValueError("matrix dependency references an unknown workspace label")
            ready = [item for item in pending if all(label in completed for label in item.depends_on)]
            if not ready:
                raise ValueError("matrix dependency graph contains a cycle")
            for item in ready:
                ordered.append(item)
                completed.add(item.workspace_label)
                pending.remove(item)
        return ordered
