"""Transactional execution leases and deterministic isolated workspace labels."""

from __future__ import annotations

import hashlib
import itertools
import os
import re
import secrets
import shutil
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sandbox.config.job_scheduler import (
    DEFAULT_POLICY, JobSchedulerConfigError, JobSchedulerPolicy,
)


_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class WorkspaceBusy(RuntimeError):
    queue_reason = "workspace_or_capacity_busy"


class SchedulerPolicyInvalid(WorkspaceBusy):
    """Admission waits because this host's ``jobs.scheduler`` config is invalid.

    The job stays queued with the config error code as its queue reason, so a
    typo in the host config is visible and never silently widens admission.
    """

    def __init__(self, error: JobSchedulerConfigError) -> None:
        super().__init__(f"{error.code}: {error}")
        self.queue_reason = error.code


def host_scheduler_policy() -> JobSchedulerPolicy:
    """Read ``jobs.scheduler`` from this host's own machine config."""
    from sandbox.config.job_scheduler import machine_job_scheduler_provider
    from sandbox.core._config import ConfigParseError, load_config
    try:
        raw = load_config()
    except (SystemExit, OSError, ConfigParseError):  # die() exits on a missing sandbox.yml.
        raise JobSchedulerConfigError(
            "host machine config is unreadable", "job_policy_config_unreadable") from None
    return machine_job_scheduler_provider(raw)


def matrix_workspace_label(*, project_identity: str, parent_job_id: str,
                           cell: dict[str, object], attempt: int = 1) -> str:
    """Stable <=21-char label, unique across concurrent project/matrix cells."""
    canonical = "|".join(f"{key}={cell[key]!s}" for key in sorted(cell))
    digest = hashlib.sha256(f"{project_identity}|{parent_job_id}|{attempt}|{canonical}".encode()).hexdigest()[:14]
    prefix = re.sub(r"[^a-z0-9]+", "-", "-".join(str(value) for _, value in sorted(cell.items())).lower()).strip("-")[:6] or "cell"
    return f"m-{prefix}-{digest}"[:21]


def capacity_view(connection, policy: JobSchedulerPolicy,
                  error: JobSchedulerConfigError | None = None) -> dict:
    """Slots held by active jobs against ``policy``; read-only.

    With an invalid host policy admission is refused, so nothing is available.
    """
    lifecycle = ("accepted", "queued", "running", "cancelling")
    in_use = int(connection.execute(
        "SELECT COUNT(*) FROM host_capacity_leases h JOIN jobs j ON j.job_id=h.job_id "
        "WHERE j.lifecycle IN (?,?,?,?)", lifecycle,
    ).fetchone()[0])
    queued = int(connection.execute(
        "SELECT COUNT(*) FROM jobs WHERE lifecycle='queued'").fetchone()[0])
    return {
        **policy.as_mapping(),
        "in_use": in_use,
        "available": 0 if error is not None else max(0, policy.max_parallel - in_use),
        "queued": queued,
        "policy_error": error.code if error is not None else None,
    }


def capacity_line(view: dict) -> str:
    """One human line for a :func:`capacity_view` mapping (possibly remote-derived)."""
    def number(key):
        value = view.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else "?"
    line = (f"job slots: {number('in_use')}/{number('max_parallel')} in use, "
            f"{number('queued')} queued (floors {number('min_free_memory_mb')} MB memory, "
            f"{number('min_free_disk_mb')} MB disk)")
    error = view.get("policy_error")
    if isinstance(error, str) and re.fullmatch(r"[a-z_]{1,64}", error):
        line += f"; admission refused: {error}"
    return line


class JobScheduler:
    def __init__(self, repository, *, max_parallel: int = DEFAULT_POLICY.max_parallel,
                 min_free_memory_mb: int = DEFAULT_POLICY.min_free_memory_mb,
                 min_free_disk_mb: int = DEFAULT_POLICY.min_free_disk_mb,
                 policy_loader: Callable[[], JobSchedulerPolicy] | None = None) -> None:
        self.repository = repository
        self.max_parallel = max_parallel
        self.min_free_memory_mb = min_free_memory_mb
        self.min_free_disk_mb = min_free_disk_mb
        # With a loader, the policy is re-read before every admission and view,
        # so a config change applies to the next admission without a restart.
        self.policy_loader = policy_loader
        self.policy_error: JobSchedulerConfigError | None = None

    def _refresh_policy(self) -> None:
        if self.policy_loader is None:
            return
        try:
            policy = self.policy_loader()
        except JobSchedulerConfigError as exc:
            self.policy_error = exc
            return
        self.policy_error = None
        self.max_parallel = policy.max_parallel
        self.min_free_memory_mb = policy.min_free_memory_mb
        self.min_free_disk_mb = policy.min_free_disk_mb

    def capacity(self) -> dict:
        """Effective slot policy and slots in use, for list and diagnostics views."""
        self._refresh_policy()
        policy = JobSchedulerPolicy(self.max_parallel, self.min_free_memory_mb,
                                    self.min_free_disk_mb)
        return capacity_view(self.repository.connection, policy, self.policy_error)

    def _resource_floor_available(self) -> bool:
        """Conservative host admission check; Docker gets stricter instance limits."""
        try:
            disk_mb = shutil.disk_usage(self.repository.path.parent).free // (1024 * 1024)
            if disk_mb < self.min_free_disk_mb:
                return False
        except OSError:
            return False
        try:
            available = next((line for line in open("/proc/meminfo") if line.startswith("MemAvailable:")), None)
            if available is not None and int(available.split()[1]) // 1024 < self.min_free_memory_mb:
                return False
        except (OSError, ValueError):
            # Non-Linux hosts do not expose MemAvailable; disk and the slot
            # limit remain active there.
            pass
        return True

    @staticmethod
    def namespace(row: dict) -> str:
        remote = row.get("remote_name") or "local"
        return f"{row['target_kind']}:{remote}:{row['project_identity']}"

    def acquire(self, row: dict, *, parallel_safe: bool = False) -> None:
        namespace = self.namespace(row)
        now = datetime.now(timezone.utc)
        expiry = (now + timedelta(seconds=max(60, int(row["deadline_seconds"])))).isoformat()
        self._refresh_policy()
        if self.policy_error is not None:
            raise SchedulerPolicyInvalid(self.policy_error)
        with self.repository.transaction(immediate=True) as connection:
            # Terminal jobs can finish without a later status read. Reap those
            # leases before admission so completed work cannot block a new job.
            self._reap_stale_locked(connection, now=now)
            if not self._resource_floor_available():
                raise WorkspaceBusy("host resource floor is not available")
            active = connection.execute(
                "SELECT l.* FROM workspace_leases l JOIN jobs j ON j.job_id=l.job_id "
                "WHERE l.target_namespace=? AND l.project_identity=? AND l.workspace_label=? "
                "AND j.lifecycle IN ('accepted','queued','running','cancelling')",
                (namespace, row["project_identity"], row["workspace_label"]),
            ).fetchall()
            if active:
                compatible = parallel_safe and all(bool(item["parallel_safe"]) for item in active)
                requested_generation = row.get("sync_generation_id")
                if requested_generation is not None:
                    compatible = compatible and all(
                        item["sync_generation_id"] == requested_generation
                        and item["source_access"] == "managed_read_only"
                        for item in active
                    )
                if not compatible:
                    raise WorkspaceBusy(
                        f"workspace {row['workspace_label']!r} is busy; use an isolated label or wait"
                    )
            slots = {int(value[0]) for value in connection.execute("SELECT slot FROM host_capacity_leases").fetchall()}
            # Count held slots rather than probing 1..max: after the limit is
            # lowered, high-numbered slots still held must keep counting.
            if len(slots) >= self.max_parallel:
                raise WorkspaceBusy("host capacity is busy; retry when a running job finishes")
            slot = next(candidate for candidate in itertools.count(1) if candidate not in slots)
            lease_id = secrets.token_hex(16)
            timestamp = now.isoformat()
            connection.execute(
                "INSERT INTO workspace_leases(lease_id,target_namespace,project_identity,workspace_label,job_id,mode,parallel_safe,sync_generation_id,source_access,acquired_at,expires_at,heartbeat_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (lease_id, namespace, row["project_identity"], row["workspace_label"],
                 row["job_id"], row["workspace_mode"], int(parallel_safe),
                 row.get("sync_generation_id"), row.get("source_access"),
                 timestamp, expiry, timestamp),
            )
            connection.execute("INSERT INTO host_capacity_leases(slot,job_id,acquired_at,heartbeat_at) VALUES(?,?,?,?)",
                               (slot, row["job_id"], timestamp, timestamp))

    def queue_details(self, row: dict) -> dict:
        """Return bounded, advisory scheduler evidence for a queued job."""
        self._refresh_policy()
        namespace = self.namespace(row)
        lifecycle = ("accepted", "queued", "running", "cancelling")
        queued_before = self.repository.connection.execute(
            "SELECT COUNT(*) FROM jobs WHERE target_kind=? AND IFNULL(remote_name,'')=? "
            "AND project_identity=? AND lifecycle='queued' "
            "AND (accepted_at < ? OR (accepted_at = ? AND job_id < ?))",
            (row["target_kind"], row.get("remote_name") or "", row["project_identity"],
             row.get("accepted_at", ""), row.get("accepted_at", ""), row["job_id"]),
        ).fetchone()[0]
        workspace_rows = self.repository.connection.execute(
            "SELECT l.job_id, l.workspace_label FROM workspace_leases l "
            "JOIN jobs j ON j.job_id=l.job_id "
            "WHERE l.target_namespace=? AND l.project_identity=? "
            "AND l.workspace_label=? AND j.lifecycle IN (?,?,?,?) "
            "ORDER BY l.acquired_at, l.job_id LIMIT 8",
            (namespace, row["project_identity"], row["workspace_label"], *lifecycle),
        ).fetchall()
        active_slots = self.repository.connection.execute(
            "SELECT h.job_id, j.workspace_label FROM host_capacity_leases h "
            "JOIN jobs j ON j.job_id=h.job_id "
            "WHERE j.lifecycle IN (?,?,?,?) ORDER BY h.acquired_at, h.job_id LIMIT 8",
            lifecycle,
        ).fetchall()
        blockers = []
        seen = set()
        candidates = (
            *workspace_rows,
            # Under an invalid policy the limit is unknown; the queue reason
            # carries the config error instead of naming slot holders.
            *(active_slots if self.policy_error is None
              and len(active_slots) >= self.max_parallel else ()),
        )
        for blocker in candidates:
            job_id = blocker[0]
            if job_id in seen or job_id == row.get("job_id"):
                continue
            seen.add(job_id)
            blockers.append({"job_id": job_id, "workspace": blocker[1]})
        return {
            "reason": row.get("queue_reason") or "queued",
            "position": int(queued_before) + 1,
            "blocking_jobs": blockers,
            "namespace": namespace,
        }

    def _reap_stale_locked(self, connection, *, now: datetime) -> list[str]:
        # Expiration cannot prove that an active process stopped using its
        # workspace. Job ownership reconciliation establishes terminal state;
        # until then keep both workspace and capacity leases, even when stale.
        rows = connection.execute(
            "SELECT l.job_id FROM workspace_leases l LEFT JOIN jobs j ON j.job_id=l.job_id "
            "WHERE j.job_id IS NULL OR j.lifecycle IN "
            "('succeeded','failed','timed_out','cancelled','interrupted')",
        ).fetchall()
        ids = [row[0] for row in rows]
        for job_id in ids:
            connection.execute("DELETE FROM workspace_leases WHERE job_id=?", (job_id,))
            connection.execute("DELETE FROM host_capacity_leases WHERE job_id=?", (job_id,))
        return ids

    def release(self, job_id: str) -> None:
        self.repository.release_leases(job_id)

    def renew(self, job_id: str, *, deadline_seconds: int) -> bool:
        """Extend an owned workspace/capacity lease while its job is observed."""
        now = datetime.now(timezone.utc)
        timestamp = now.isoformat()
        expiry = (now + timedelta(seconds=max(60, int(deadline_seconds)))).isoformat()
        with self.repository.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT lease_id FROM workspace_leases WHERE job_id=?", (job_id,)
            ).fetchone()
            if row is None:
                return False
            connection.execute(
                "UPDATE workspace_leases SET expires_at=?, heartbeat_at=? WHERE job_id=?",
                (expiry, timestamp, job_id),
            )
            connection.execute(
                "UPDATE host_capacity_leases SET heartbeat_at=? WHERE job_id=?",
                (timestamp, job_id),
            )
        return True

    def reconcile_stale(self, *, now: datetime | None = None) -> list[str]:
        """Release terminal or missing-job leases, never unresolved active work."""
        now = now or datetime.now(timezone.utc)
        with self.repository.transaction(immediate=True) as connection:
            return self._reap_stale_locked(connection, now=now)

    def active(self, *, namespace: str | None = None) -> list[dict]:
        query = "SELECT * FROM workspace_leases"
        args = ()
        if namespace:
            query += " WHERE target_namespace=?"; args = (namespace,)
        return [dict(row) for row in self.repository.connection.execute(query, args).fetchall()]
