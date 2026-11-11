"""Transactional execution leases and deterministic isolated workspace labels."""

from __future__ import annotations

import hashlib
import itertools
import os
import re
import secrets
import shutil
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sandbox.config.job_scheduler import (
    DEFAULT_POLICY, JobSchedulerConfigError, JobSchedulerPolicy,
)


_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


# Why one admission was refused (spec 083). A drain continues past refusals
# that concern only that job and stops at the host-wide ones.
REFUSAL_KINDS = frozenset({"admitted", "workspace", "order", "capacity", "floor", "policy"})
HOST_WIDE_REFUSALS = frozenset({"capacity", "floor", "policy"})


class WorkspaceBusy(RuntimeError):
    queue_reason = "workspace_or_capacity_busy"

    def __init__(self, message: str = "", *, kind: str = "workspace") -> None:
        super().__init__(message)
        if kind not in REFUSAL_KINDS:
            raise ValueError(f"unknown admission refusal kind: {kind!r}")
        self.kind = kind


class SchedulerPolicyInvalid(WorkspaceBusy):
    """Admission waits because this host's ``jobs.scheduler`` config is invalid.

    The job stays queued with the config error code as its queue reason, so a
    typo in the host config is visible and never silently widens admission.
    """

    def __init__(self, error: JobSchedulerConfigError) -> None:
        super().__init__(f"{error.code}: {error}", kind="policy")
        self.queue_reason = error.code


def _host_machine_config() -> dict:
    from sandbox.core._config import ConfigParseError, load_config
    try:
        return load_config()
    except (SystemExit, OSError, ConfigParseError):  # die() exits on a missing sandbox.yml.
        raise JobSchedulerConfigError(
            "host machine config is unreadable", "job_policy_config_unreadable") from None


def host_scheduler_policy() -> JobSchedulerPolicy:
    """Read ``jobs.scheduler`` from this host's own machine config."""
    from sandbox.config.job_scheduler import machine_job_scheduler_provider
    return machine_job_scheduler_provider(_host_machine_config())


def matrix_workspace_label(*, project_identity: str, parent_job_id: str,
                           cell: dict[str, object], attempt: int = 1) -> str:
    """Stable <=21-char label, unique across concurrent project/matrix cells."""
    canonical = "|".join(f"{key}={cell[key]!s}" for key in sorted(cell))
    digest = hashlib.sha256(f"{project_identity}|{parent_job_id}|{attempt}|{canonical}".encode()).hexdigest()[:14]
    prefix = re.sub(r"[^a-z0-9]+", "-", "-".join(str(value) for _, value in sorted(cell.items())).lower()).strip("-")[:6] or "cell"
    return f"m-{prefix}-{digest}"[:21]


def check_host_policy_write(policy: JobSchedulerPolicy) -> None:
    """Refuse before a local write when the base config would keep the block invalid.

    ``sandbox.local.yml`` is deep-merged over the checked-in ``sandbox.yml``,
    so a bad key there survives a local write; only fixing that file repairs it.
    """
    from sandbox.config.job_scheduler import machine_job_scheduler_provider
    from sandbox.core._config import CONFIG, deep_merge
    merged = deep_merge(_host_machine_config(), {"jobs": {"scheduler": policy.as_mapping()}})
    try:
        machine_job_scheduler_provider(merged)
    except JobSchedulerConfigError as exc:
        raise JobSchedulerConfigError(
            f"{CONFIG.name} keeps jobs.scheduler invalid ({exc.code}): {exc}; fix it there",
            "job_policy_invalid_base_config") from None


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


_ACTIVE = ("accepted", "queued", "running", "cancelling")


def workspace_compatible(connection, row, *, parallel_safe: bool) -> bool:
    """The workspace-sharing rule of admission, shared with the order gate."""
    row = dict(row)
    active = connection.execute(
        "SELECT l.* FROM workspace_leases l JOIN jobs j ON j.job_id=l.job_id "
        "WHERE l.target_namespace=? AND l.project_identity=? AND l.workspace_label=? "
        "AND j.lifecycle IN (?,?,?,?)",
        (JobScheduler.namespace(row), row["project_identity"], row["workspace_label"], *_ACTIVE),
    ).fetchall()
    if not active:
        return True
    compatible = parallel_safe and all(bool(item["parallel_safe"]) for item in active)
    requested_generation = row.get("sync_generation_id")
    if requested_generation is not None:
        compatible = compatible and all(
            item["sync_generation_id"] == requested_generation
            and item["source_access"] == "managed_read_only"
            for item in active
        )
    return compatible


_CANDIDATES = (
    "SELECT j.* FROM jobs j WHERE j.lifecycle='queued' "
    "AND j.queue_reason='workspace_or_capacity_busy' "
    "AND (j.accepted_at < ? OR (j.accepted_at = ? AND j.job_id < ?)) "
    "AND j.kind NOT IN ('matrix','plan') "
    "AND NOT EXISTS (SELECT 1 FROM jobs c WHERE c.parent_job_id=j.job_id) "
    "AND NOT EXISTS (SELECT 1 FROM workspace_leases l WHERE l.job_id=j.job_id) "
    "AND NOT EXISTS (SELECT 1 FROM host_capacity_leases h WHERE h.job_id=j.job_id) "
    "ORDER BY j.accepted_at, j.job_id"
)


def _older_candidates(connection, row):
    """Older queued jobs that could take a free slot now (contracts/admission.md)."""
    key = (row["accepted_at"], row["accepted_at"], row["job_id"])
    for candidate in connection.execute(_CANDIDATES, key).fetchall():
        own_parallel_safe = bool(candidate["parallel_safe"]) or candidate["workspace_mode"] == "isolated"
        if workspace_compatible(connection, candidate, parallel_safe=own_parallel_safe):
            yield candidate["job_id"]


def older_candidate(connection, row) -> str | None:
    """The oldest job that must be admitted before ``row``, or None."""
    return next(_older_candidates(connection, row), None)


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
        # Per thread: the remote service shares one scheduler across request
        # threads, and a drain reads the refusal of the admission it just ran.
        self._local = threading.local()

    @property
    def last_refusal(self) -> str | None:
        return getattr(self._local, "refusal", None)

    @last_refusal.setter
    def last_refusal(self, kind: str | None) -> None:
        self._local.refusal = kind

    def _refuse(self, error: WorkspaceBusy) -> WorkspaceBusy:
        self.last_refusal = error.kind
        return error

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
        self.last_refusal = None
        self._refresh_policy()
        if self.policy_error is not None:
            raise self._refuse(SchedulerPolicyInvalid(self.policy_error))
        with self.repository.transaction(immediate=True) as connection:
            # Terminal jobs can finish without a later status read. Reap those
            # leases before admission so completed work cannot block a new job.
            self._reap_stale_locked(connection, now=now)
            # A job another caller already admitted is left alone: re-admitting
            # it would fail the unique slot row and the generic error path would
            # then release the winner's leases (spec 083 FR-015).
            current = connection.execute(
                "SELECT lifecycle FROM jobs WHERE job_id=?", (row["job_id"],)).fetchone()
            if (current is not None and current[0] not in _ACTIVE) or connection.execute(
                    "SELECT 1 FROM workspace_leases WHERE job_id=? UNION ALL "
                    "SELECT 1 FROM host_capacity_leases WHERE job_id=? LIMIT 1",
                    (row["job_id"], row["job_id"])).fetchone() is not None:
                raise self._refuse(WorkspaceBusy("job is already admitted", kind="admitted"))
            if not self._resource_floor_available():
                raise self._refuse(WorkspaceBusy("host resource floor is not available", kind="floor"))
            if not workspace_compatible(connection, row, parallel_safe=parallel_safe):
                raise self._refuse(WorkspaceBusy(
                    f"workspace {row['workspace_label']!r} is busy; use an isolated label or wait"))
            if older_candidate(connection, row) is not None:
                raise self._refuse(WorkspaceBusy(
                    "an older queued job is waiting for this host", kind="order"))
            slots = {int(value[0]) for value in connection.execute("SELECT slot FROM host_capacity_leases").fetchall()}
            # Count held slots rather than probing 1..max: after the limit is
            # lowered, high-numbered slots still held must keep counting.
            if len(slots) >= self.max_parallel:
                raise self._refuse(WorkspaceBusy(
                    "host capacity is busy; retry when a running job finishes", kind="capacity"))
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

    def queued_in_order(self, *, after: tuple[str, str] | None = None,
                        limit: int = 500) -> list[tuple[str, str]]:
        """One page of ``(accepted_at, job_id)`` of queued jobs, oldest first."""
        accepted_at, job_id = after or ("", "")
        return [(str(item[0]), str(item[1])) for item in self.repository.connection.execute(
            "SELECT accepted_at, job_id FROM jobs WHERE lifecycle='queued' "
            "AND kind NOT IN ('matrix','plan') "
            "AND (accepted_at > ? OR (accepted_at = ? AND job_id > ?)) "
            "ORDER BY accepted_at, job_id LIMIT ?",
            (accepted_at, accepted_at, job_id, int(limit))).fetchall()]

    def free_slots(self) -> int:
        """Slots free under the last loaded policy; a drain refreshes it first."""
        held = self.repository.connection.execute(
            "SELECT COUNT(*) FROM host_capacity_leases h JOIN jobs j ON j.job_id=h.job_id "
            "WHERE j.lifecycle IN (?,?,?,?)", _ACTIVE).fetchone()[0]
        return max(0, self.max_parallel - int(held))

    def refresh_policy(self) -> None:
        self._refresh_policy()

    def record_queue_reason(self, job_id: str, reason: str) -> None:
        """Record why a still-queued job waits; nothing else changes."""
        with self.repository.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE jobs SET queue_reason=? WHERE job_id=? AND lifecycle='queued' "
                "AND IFNULL(queue_reason,'') <> ?", (reason, job_id, reason))

    def record_snapshot_reason(self, snapshot: dict) -> None:
        """Record a status read's reason; sync launches own theirs (claim/reset)."""
        if snapshot.get("sync_relationship_id") is None and snapshot.get("queue_reason"):
            self.record_queue_reason(snapshot["job_id"], snapshot["queue_reason"])

    def holds_slot(self, job_id: str) -> bool:
        return self.repository.connection.execute(
            "SELECT 1 FROM host_capacity_leases WHERE job_id=?", (job_id,)).fetchone() is not None

    def is_queued(self, job_id: str) -> bool:
        row = self.repository.connection.execute(
            "SELECT lifecycle FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return row is not None and row[0] == "queued"

    def queue_details(self, row: dict) -> dict:
        """Return bounded, advisory scheduler evidence for a queued job."""
        self._refresh_policy()
        namespace = self.namespace(row)
        lifecycle = ("accepted", "queued", "running", "cancelling")
        # Host-wide: the number of older jobs that will be admitted first.
        queued_before = sum(1 for _ in _older_candidates(self.repository.connection, {
            "accepted_at": row.get("accepted_at", ""), "job_id": row["job_id"]}))
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
