"""Transactional execution leases and deterministic isolated workspace labels."""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone


_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class WorkspaceBusy(RuntimeError):
    pass


def matrix_workspace_label(*, project_identity: str, parent_job_id: str,
                           cell: dict[str, object], attempt: int = 1) -> str:
    """Stable <=21-char label, unique across concurrent project/matrix cells."""
    canonical = "|".join(f"{key}={cell[key]!s}" for key in sorted(cell))
    digest = hashlib.sha256(f"{project_identity}|{parent_job_id}|{attempt}|{canonical}".encode()).hexdigest()[:14]
    prefix = re.sub(r"[^a-z0-9]+", "-", "-".join(str(value) for _, value in sorted(cell.items())).lower()).strip("-")[:6] or "cell"
    return f"m-{prefix}-{digest}"[:21]


class JobScheduler:
    def __init__(self, repository, *, max_parallel: int = 4) -> None:
        self.repository = repository
        self.max_parallel = max_parallel

    @staticmethod
    def namespace(row: dict) -> str:
        remote = row.get("remote_name") or "local"
        return f"{row['target_kind']}:{remote}:{row['project_identity']}"

    def acquire(self, row: dict, *, parallel_safe: bool = False) -> None:
        namespace = self.namespace(row)
        now = datetime.now(timezone.utc)
        expiry = (now + timedelta(seconds=max(60, int(row["deadline_seconds"])))).isoformat()
        with self.repository.transaction(immediate=True) as connection:
            active = connection.execute(
                "SELECT l.* FROM workspace_leases l JOIN jobs j ON j.job_id=l.job_id "
                "WHERE l.target_namespace=? AND l.project_identity=? AND l.workspace_label=? "
                "AND j.lifecycle IN ('accepted','queued','running','cancelling')",
                (namespace, row["project_identity"], row["workspace_label"]),
            ).fetchall()
            if active and (not parallel_safe or any(not item["parallel_safe"] for item in active)):
                raise WorkspaceBusy(f"workspace {row['workspace_label']!r} is busy; use an isolated label or wait")
            slots = {int(value[0]) for value in connection.execute("SELECT slot FROM host_capacity_leases").fetchall()}
            slot = next((candidate for candidate in range(1, self.max_parallel + 1) if candidate not in slots), None)
            if slot is None:
                raise WorkspaceBusy("host capacity is busy; retry when a running job finishes")
            lease_id = secrets.token_hex(16)
            timestamp = now.isoformat()
            connection.execute(
                "INSERT INTO workspace_leases(lease_id,target_namespace,project_identity,workspace_label,job_id,mode,parallel_safe,acquired_at,expires_at,heartbeat_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (lease_id, namespace, row["project_identity"], row["workspace_label"], row["job_id"], row["workspace_mode"], int(parallel_safe), timestamp, expiry, timestamp),
            )
            connection.execute("INSERT INTO host_capacity_leases(slot,job_id,acquired_at,heartbeat_at) VALUES(?,?,?,?)",
                               (slot, row["job_id"], timestamp, timestamp))

    def release(self, job_id: str) -> None:
        with self.repository.transaction(immediate=True) as connection:
            connection.execute("DELETE FROM workspace_leases WHERE job_id=?", (job_id,))
            connection.execute("DELETE FROM host_capacity_leases WHERE job_id=?", (job_id,))

    def active(self, *, namespace: str | None = None) -> list[dict]:
        query = "SELECT * FROM workspace_leases"
        args = ()
        if namespace:
            query += " WHERE target_namespace=?"; args = (namespace,)
        return [dict(row) for row in self.repository.connection.execute(query, args).fetchall()]
