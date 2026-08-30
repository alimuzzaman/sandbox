"""SQLite-backed durable job repository."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from sandbox.services.redaction import require_safe_argv

from .models import Health, JobSubmission, Lifecycle, new_job_id, validate_job_id, validate_transition


SCHEMA_VERSION = 5
MAX_SUBMISSION_SNAPSHOT_BYTES = 65_536
MAX_SUBMISSION_ITEMS = 256
MAX_SUBMISSION_TEXT = 4_096
MAX_DIFFERENCE_DETAIL = 2_048
_INITIALIZATION_LOCK = threading.Lock()


class JobRepositoryError(RuntimeError):
    pass


class RequestIdConflict(JobRepositoryError):
    pass


class JobNotFound(JobRepositoryError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_resource_index(path: str | Path) -> dict[str, list[dict[str, Any]]]:
    """Read bounded job/workspace/artifact retention evidence without writes."""
    database = Path(path).expanduser().resolve()
    if not database.is_file():
        return {"jobs": [], "artifacts": []}
    connection = sqlite3.connect(
        f"{database.as_uri()}?mode=ro",
        uri=True,
        timeout=1,
    )
    connection.row_factory = sqlite3.Row
    try:
        job_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(jobs)")
        }
        workspace_id_column = (
            "workspace_id" if "workspace_id" in job_columns
            else "NULL AS workspace_id"
        )
        jobs = [
            dict(row) for row in connection.execute(
                "SELECT job_id, project_root, project_identity, target_kind, "
                f"remote_name, lifecycle, {workspace_id_column}, workspace_label, workspace_mode, "
                "cleanup_policy, cleanup_state, finished_at "
                "FROM jobs ORDER BY job_id LIMIT 10000"
            )
        ]
        artifacts = [
            dict(row) for row in connection.execute(
                "SELECT a.artifact_id, a.job_id, a.stored_relative_path, "
                "a.display_name, a.size_bytes, a.expires_at, a.status, "
                "j.lifecycle AS job_lifecycle "
                "FROM artifacts a JOIN jobs j ON j.job_id=a.job_id "
                "ORDER BY a.artifact_id LIMIT 10000"
            )
        ]
        return {"jobs": jobs, "artifacts": artifacts}
    finally:
        connection.close()


def _bounded_text(value: object, label: str, *, maximum: int = MAX_SUBMISSION_TEXT,
                  allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or len(value.encode("utf-8")) > maximum or any(
            ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{label} is invalid or exceeds the durable snapshot limit")
    return value


def _bounded_strings(values: object, label: str, *, maximum: int = MAX_SUBMISSION_ITEMS) -> list[str]:
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)) or len(values) > maximum:
        raise ValueError(f"{label} exceeds the durable snapshot limit")
    return [_bounded_text(value, label) for value in values]


def _bounded_argv(values: object) -> list[str]:
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)) or len(values) > MAX_SUBMISSION_ITEMS:
        raise ValueError("command arguments exceed the durable snapshot limit")
    result = []
    for value in values:
        if (not isinstance(value, str) or not value or "\x00" in value or
                len(value.encode("utf-8")) > MAX_SUBMISSION_SNAPSHOT_BYTES):
            raise ValueError("command argument is invalid or exceeds the durable snapshot limit")
        result.append(value)
    return result


def _safe_difference(item: object) -> dict[str, Any]:
    if not isinstance(item, dict) or not item.get("id"):
        raise ValueError("compatibility difference is invalid")
    return {
        "id": _bounded_text(item["id"], "compatibility difference id", maximum=128),
        "workflow": _bounded_text(item.get("workflow", ""), "compatibility workflow", maximum=1_024),
        "location": _bounded_text(item.get("location", ""), "compatibility location", maximum=1_024),
        "severity": _bounded_text(item.get("severity", "notice"), "compatibility severity", maximum=64),
        "accepted": bool(item.get("accepted")),
        "detail": _bounded_text(item.get("detail", item.get("message", "")),
                                "compatibility detail", maximum=MAX_DIFFERENCE_DETAIL),
        "catalog_version": _bounded_text(item.get("catalog_version", "unknown"),
                                         "compatibility catalog version", maximum=128),
    }


def _canonical_submission_snapshot(submission: JobSubmission) -> str:
    raw = submission.as_dict()
    differences = raw.get("compatibility_differences", ())
    if len(differences) > MAX_SUBMISSION_ITEMS:
        raise ValueError("compatibility differences exceed the durable snapshot limit")
    source = raw["source"]
    snapshot = {
        "version": 1,
        "kind": _bounded_text(raw["kind"], "job kind", maximum=64),
        "project_root": _bounded_text(raw["project_root"], "project root"),
        "project_identity": _bounded_text(raw["project_identity"], "project identity"),
        "target_kind": raw["target_kind"],
        "remote_name": _bounded_text(raw.get("remote_name"), "remote name", maximum=64, allow_none=True),
        "workspace_label": _bounded_text(raw["workspace_label"], "workspace label", maximum=64),
        "workspace_mode": raw["workspace_mode"],
        "argv": _bounded_argv(raw["argv"]),
        "cwd_relative": _bounded_text(raw["cwd_relative"], "working directory"),
        "execution_profile": _bounded_text(raw["execution_profile"], "execution profile", maximum=64),
        "output_profile": _bounded_text(raw["output_profile"], "output profile", maximum=64),
        "output_profile_definition": raw.get("output_profile_definition", {}),
        "deadline_seconds": int(raw["deadline_seconds"]),
        "deadline_source": _bounded_text(raw["deadline_source"], "deadline source", maximum=128),
        "deadline_reminder": _bounded_text(raw.get("deadline_reminder"), "deadline reminder",
                                             maximum=1_024, allow_none=True),
        "stall_seconds": int(raw["stall_seconds"]),
        "cancel_grace_seconds": int(raw["cancel_grace_seconds"]),
        "cancel_on_stall": bool(raw["cancel_on_stall"]),
        "cleanup_policy": raw["cleanup_policy"],
        "execution_policy_provenance": {
            _bounded_text(key, "execution policy provenance key", maximum=64):
            _bounded_text(value, "execution policy provenance value", maximum=128)
            for key, value in dict(raw.get("execution_policy_provenance") or {}).items()
        },
        "request_id": _bounded_text(raw.get("request_id"), "request id", maximum=64, allow_none=True),
        "parent_job_id": raw.get("parent_job_id"),
        "retry_of_job_id": raw.get("retry_of_job_id"),
        "attempt": int(raw["attempt"]),
        # Environment values never enter this snapshot; only validated key names do.
        "environment_keys": _bounded_strings(raw.get("environment_keys", ()), "environment key"),
        "artifact_paths": _bounded_strings(raw.get("artifact_paths", ()), "artifact path"),
        "depends_on": _bounded_strings(raw.get("depends_on", ()), "job dependency"),
        "failure_policy": raw["failure_policy"],
        "compatibility_differences": [_safe_difference(item) for item in differences],
        "sync_relationship_id": _bounded_text(
            raw.get("sync_relationship_id"), "synchronization relationship id",
            maximum=160, allow_none=True,
        ),
        "sync_generation_id": _bounded_text(
            raw.get("sync_generation_id"), "synchronization generation id",
            maximum=160, allow_none=True,
        ),
        "source_access": _bounded_text(
            raw.get("source_access"), "source access", maximum=32, allow_none=True,
        ),
        "parallel_safe": bool(raw.get("parallel_safe", False)),
        "source": {
            "identity": _bounded_text(source["identity"], "source identity"),
            "commit": _bounded_text(source.get("commit"), "source commit", allow_none=True),
            "dirty_digest": _bounded_text(source.get("dirty_digest"), "source dirty digest", allow_none=True),
        },
    }
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    if len(payload.encode("utf-8")) > MAX_SUBMISSION_SNAPSHOT_BYTES:
        raise ValueError("canonical submission snapshot exceeds 65536 bytes")
    return payload


class JobRepository:
    def __init__(self, path: str | Path, *, busy_timeout_ms: int = 5_000) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.connection = sqlite3.connect(
            self.path, timeout=busy_timeout_ms / 1000, isolation_level=None,
            check_same_thread=False,
        )
        self.connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with _INITIALIZATION_LOCK:
            self.connection.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
            self.connection.execute("PRAGMA foreign_keys=ON")
            self.connection.execute("PRAGMA synchronous=FULL")
            current_mode = self.connection.execute("PRAGMA journal_mode").fetchone()[0]
            if current_mode.lower() != "wal":
                self.connection.execute("PRAGMA journal_mode=WAL")
            self._migrate()

    def close(self) -> None:
        self.connection.close()

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield self.connection
            except BaseException:
                self.connection.rollback()
                raise
            else:
                self.connection.commit()

    def _migrate(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            request_id TEXT,
            request_digest TEXT NOT NULL,
            parent_job_id TEXT REFERENCES jobs(job_id),
            root_job_id TEXT REFERENCES jobs(job_id),
            retry_of_job_id TEXT REFERENCES jobs(job_id),
            attempt INTEGER NOT NULL CHECK(attempt >= 1),
            kind TEXT NOT NULL,
            project_root TEXT NOT NULL,
            project_identity TEXT NOT NULL,
            target_kind TEXT NOT NULL,
            remote_name TEXT,
            workspace_id TEXT,
            workspace_label TEXT NOT NULL,
            workspace_mode TEXT NOT NULL,
            lifecycle TEXT NOT NULL,
            health TEXT NOT NULL,
            depends_on_json TEXT NOT NULL DEFAULT '[]',
            failure_policy TEXT NOT NULL DEFAULT 'fail-fast',
            queue_reason TEXT,
            queue_position INTEGER,
            command_json TEXT NOT NULL,
            cwd_relative TEXT NOT NULL,
            environment_keys_json TEXT NOT NULL,
            execution_profile TEXT NOT NULL,
            output_profile TEXT NOT NULL,
            deadline_seconds INTEGER NOT NULL CHECK(deadline_seconds > 0),
            deadline_source TEXT NOT NULL,
            deadline_reminder TEXT,
            stall_seconds INTEGER NOT NULL CHECK(stall_seconds > 0),
            cancel_grace_seconds INTEGER NOT NULL DEFAULT 20 CHECK(cancel_grace_seconds > 0),
            cancel_on_stall INTEGER NOT NULL,
            cleanup_policy TEXT NOT NULL,
            execution_policy_provenance_json TEXT NOT NULL DEFAULT '{}',
            source_identity TEXT NOT NULL,
            source_commit TEXT,
            source_dirty_digest TEXT,
            sync_relationship_id TEXT,
            sync_generation_id TEXT,
            source_access TEXT,
            parallel_safe INTEGER NOT NULL DEFAULT 0,
            accepted_at TEXT NOT NULL,
            queued_at TEXT,
            started_at TEXT,
            finished_at TEXT,
            updated_at TEXT NOT NULL,
            exit_code INTEGER,
            termination_reason TEXT,
            output_completeness TEXT NOT NULL DEFAULT 'active',
            cleanup_state TEXT NOT NULL DEFAULT 'not_requested',
            integrity_sha256 TEXT,
            result_json TEXT,
            submission_json TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS jobs_request_identity
            ON jobs(target_kind, IFNULL(remote_name, ''), project_identity, request_id)
            WHERE request_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS jobs_project_time ON jobs(project_identity, accepted_at);
        CREATE INDEX IF NOT EXISTS jobs_workspace_lifecycle ON jobs(workspace_label, lifecycle);
        CREATE INDEX IF NOT EXISTS jobs_parent_attempt ON jobs(parent_job_id, attempt);
        CREATE TABLE IF NOT EXISTS process_identities (
            job_id TEXT PRIMARY KEY REFERENCES jobs(job_id) ON DELETE CASCADE,
            host_boot_id TEXT NOT NULL,
            supervisor_pid INTEGER NOT NULL,
            supervisor_start_identity TEXT NOT NULL,
            supervisor_nonce_hash TEXT NOT NULL,
            child_pid INTEGER,
            child_pgid INTEGER,
            child_start_identity TEXT,
            recorded_at TEXT NOT NULL,
            last_verified_at TEXT
        );
        CREATE TABLE IF NOT EXISTS heartbeats (
            job_id TEXT PRIMARY KEY REFERENCES jobs(job_id) ON DELETE CASCADE,
            supervisor_at TEXT NOT NULL,
            child_observed_at TEXT,
            last_output_at TEXT,
            last_activity_at TEXT,
            last_progress_at TEXT,
            last_metric_at TEXT,
            metric_digest TEXT,
            health_evidence_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS workspace_leases (
            lease_id TEXT PRIMARY KEY,
            target_namespace TEXT NOT NULL,
            project_identity TEXT NOT NULL,
            workspace_label TEXT NOT NULL,
            job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
            mode TEXT NOT NULL,
            parallel_safe INTEGER NOT NULL,
            sync_generation_id TEXT,
            source_access TEXT,
            acquired_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            heartbeat_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS host_capacity_leases (
            slot INTEGER PRIMARY KEY,
            job_id TEXT UNIQUE NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
            acquired_at TEXT NOT NULL,
            heartbeat_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS output_streams (
            job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
            stream TEXT NOT NULL,
            first_sequence INTEGER NOT NULL DEFAULT 0,
            next_sequence INTEGER NOT NULL DEFAULT 0,
            bytes_stored INTEGER NOT NULL DEFAULT 0,
            events_stored INTEGER NOT NULL DEFAULT 0,
            segments INTEGER NOT NULL DEFAULT 0,
            last_segment_bytes INTEGER NOT NULL DEFAULT 0,
            complete INTEGER NOT NULL DEFAULT 0,
            sha256 TEXT,
            available INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(job_id, stream)
        );
        CREATE TABLE IF NOT EXISTS job_events (
            job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
            sequence INTEGER NOT NULL,
            kind TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY(job_id, sequence)
        );
        CREATE TABLE IF NOT EXISTS metrics_index (
            job_id TEXT PRIMARY KEY REFERENCES jobs(job_id) ON DELETE CASCADE,
            samples INTEGER NOT NULL DEFAULT 0,
            first_at TEXT,
            last_at TEXT,
            sha256 TEXT,
            complete INTEGER NOT NULL DEFAULT 0,
            available INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS artifacts (
            artifact_id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
            declared_path TEXT,
            stored_relative_path TEXT NOT NULL,
            display_name TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'file',
            size_bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            media_type TEXT NOT NULL DEFAULT 'application/octet-stream',
            created_at TEXT NOT NULL,
            expires_at TEXT,
            status TEXT NOT NULL DEFAULT 'available',
            reason TEXT
        );
        CREATE TABLE IF NOT EXISTS compatibility_differences (
            job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
            difference_id TEXT NOT NULL,
            workflow_path TEXT NOT NULL,
            location TEXT NOT NULL,
            severity TEXT NOT NULL,
            accepted INTEGER NOT NULL,
            detail TEXT NOT NULL,
            catalog_version TEXT NOT NULL,
            PRIMARY KEY(job_id, difference_id, location)
        );
        """
        with self._lock:
            self.connection.executescript(schema)
        # Additive upgrades and version publication share one full-durability
        # transaction. A crash cannot advertise the new version before every
        # required column exists; old rows remain nullable/backward readable.
        with self.transaction(immediate=True) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
            for name, declaration in (
                ("depends_on_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("failure_policy", "TEXT NOT NULL DEFAULT 'fail-fast'"),
                ("deadline_reminder", "TEXT"),
                ("cancel_grace_seconds", "INTEGER NOT NULL DEFAULT 20"),
                ("execution_policy_provenance_json", "TEXT NOT NULL DEFAULT '{}'") ,
                ("queue_reason", "TEXT"),
                ("queue_position", "INTEGER"),
                ("submission_json", "TEXT"),
                ("sync_relationship_id", "TEXT"),
                ("sync_generation_id", "TEXT"),
                ("source_access", "TEXT"),
                ("parallel_safe", "INTEGER NOT NULL DEFAULT 0"),
                ("workspace_id", "TEXT"),
            ):
                if name not in columns:
                    connection.execute(f"ALTER TABLE jobs ADD COLUMN {name} {declaration}")
            output_columns = {row[1] for row in connection.execute("PRAGMA table_info(output_streams)")}
            if "available" not in output_columns:
                connection.execute("ALTER TABLE output_streams ADD COLUMN available INTEGER NOT NULL DEFAULT 1")
            metric_columns = {row[1] for row in connection.execute("PRAGMA table_info(metrics_index)")}
            if "available" not in metric_columns:
                connection.execute("ALTER TABLE metrics_index ADD COLUMN available INTEGER NOT NULL DEFAULT 1")
            lease_columns = {row[1] for row in connection.execute("PRAGMA table_info(workspace_leases)")}
            for name, declaration in (
                ("sync_generation_id", "TEXT"),
                ("source_access", "TEXT"),
            ):
                if name not in lease_columns:
                    connection.execute(
                        f"ALTER TABLE workspace_leases ADD COLUMN {name} {declaration}"
                    )
            connection.execute(
                "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )

    def schema_version(self) -> int:
        row = self.connection.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        return int(row[0])

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def replay(self, submission: JobSubmission) -> dict[str, Any] | None:
        """Return one exact durable request replay without creating side effects."""
        if submission.request_id is None:
            return None
        row = self.connection.execute(
            "SELECT * FROM jobs WHERE target_kind=? AND IFNULL(remote_name, '')=? "
            "AND project_identity=? AND request_id=?",
            (submission.target_kind, submission.remote_name or "",
             submission.project_identity, submission.request_id),
        ).fetchone()
        if row is None:
            return None
        if row["request_digest"] != submission.canonical_digest():
            raise RequestIdConflict(
                "request id was already used for a different submission")
        return dict(row)

    def accept(self, submission: JobSubmission, *,
               workspace_id: str | None = None) -> tuple[dict[str, Any], bool]:
        # Persisted argv is later executed verbatim. Refuse credential-bearing
        # forms instead of redacting them into a different command.
        require_safe_argv(submission.argv)
        if (workspace_id is not None and
                (not isinstance(workspace_id, str) or
                 not re.fullmatch(r"ws_[0-9a-f]{32}", workspace_id))):
            raise ValueError("workspace id is invalid")
        digest = submission.canonical_digest()
        now = _now()
        with self.transaction(immediate=True) as connection:
            if submission.request_id is not None:
                existing = connection.execute(
                    "SELECT * FROM jobs WHERE target_kind=? AND IFNULL(remote_name, '')=? "
                    "AND project_identity=? AND request_id=?",
                    (submission.target_kind, submission.remote_name or "",
                     submission.project_identity, submission.request_id),
                ).fetchone()
                if existing is not None:
                    if existing["request_digest"] != digest:
                        raise RequestIdConflict("request id was already used for a different submission")
                    if (workspace_id is not None and
                            existing["workspace_id"] != workspace_id):
                        raise JobRepositoryError(
                            "request workspace identity changed")
                    return dict(existing), True
            submission_json = _canonical_submission_snapshot(submission)
            job_id = new_job_id()
            values = (
                job_id, submission.request_id, digest, submission.parent_job_id, None,
                submission.retry_of_job_id, submission.attempt, submission.kind,
                submission.project_root, submission.project_identity, submission.target_kind,
                submission.remote_name, workspace_id, submission.workspace_label, submission.workspace_mode,
                Lifecycle.ACCEPTED.value, Health.UNKNOWN.value,
                json.dumps(list(submission.depends_on)), submission.failure_policy, None, None,
                json.dumps(list(submission.argv)),
                submission.cwd_relative, json.dumps(list(submission.environment_keys)),
                submission.execution_profile, submission.output_profile,
                submission.deadline_seconds, submission.deadline_source,
                submission.deadline_reminder, submission.stall_seconds, submission.cancel_grace_seconds,
                int(submission.cancel_on_stall), submission.cleanup_policy,
                json.dumps(dict(submission.execution_policy_provenance or {}), sort_keys=True),
                submission.source.identity, submission.source.commit,
                submission.source.dirty_digest,
                submission.sync_relationship_id, submission.sync_generation_id,
                submission.source_access, int(submission.parallel_safe),
                now, now, submission_json,
            )
            connection.execute(
                """INSERT INTO jobs(
                    job_id, request_id, request_digest, parent_job_id, root_job_id,
                    retry_of_job_id, attempt, kind, project_root, project_identity,
                    target_kind, remote_name, workspace_id, workspace_label, workspace_mode, lifecycle,
                    health, depends_on_json, failure_policy, queue_reason, queue_position,
                    command_json, cwd_relative, environment_keys_json,
                    execution_profile, output_profile, deadline_seconds, deadline_source,
                    deadline_reminder, stall_seconds, cancel_grace_seconds, cancel_on_stall, cleanup_policy,
                    execution_policy_provenance_json, source_identity,
                    source_commit, source_dirty_digest, sync_relationship_id,
                    sync_generation_id, source_access, parallel_safe,
                    accepted_at, updated_at, submission_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                values,
            )
            root = submission.parent_job_id or job_id
            connection.execute("UPDATE jobs SET root_job_id=? WHERE job_id=?", (root, job_id))
            row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            return dict(row), False

    def get(self, job_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM jobs WHERE job_id=?", (validate_job_id(job_id),)).fetchone()
        if row is None:
            raise JobNotFound(f"job {job_id!r} was not found")
        return dict(row)

    def submission_snapshot(self, job_id: str) -> dict[str, Any] | None:
        """Return bounded canonical input, or None for legacy/corrupt rows."""
        payload = self.get(job_id).get("submission_json")
        if not isinstance(payload, str) or not payload or len(payload.encode("utf-8")) > MAX_SUBMISSION_SNAPSHOT_BYTES:
            return None
        try:
            value = json.loads(payload)
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, dict) and value.get("version") == 1 else None

    def list(self, *, limit: int = 50, project_identity: str | None = None,
             workspace_label: str | None = None, lifecycle: str | None = None,
             kind: str | None = None, active_only: bool = False,
             cursor_job_id: str | None = None) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("job list limit must be between 1 and 200")
        clauses = []
        values: list[Any] = []
        if project_identity is not None:
            clauses.append("project_identity=?")
            values.append(project_identity)
        if workspace_label is not None:
            clauses.append("workspace_label=?")
            values.append(workspace_label)
        if lifecycle is not None:
            clauses.append("lifecycle=?")
            values.append(lifecycle)
        if active_only:
            clauses.append("lifecycle IN ('accepted','queued','running','cancelling')")
        category = kind
        if category is not None:
            clauses.append("kind=?")
            values.append(category)
        if cursor_job_id is not None:
            validate_job_id(cursor_job_id)
            cursor = self.connection.execute(
                "SELECT accepted_at,job_id FROM jobs WHERE job_id=?",
                (cursor_job_id,),
            ).fetchone()
            if cursor is None:
                raise ValueError("job list cursor is stale")
            clauses.append("(accepted_at < ? OR (accepted_at = ? AND job_id < ?))")
            values.extend((cursor["accepted_at"], cursor["accepted_at"], cursor["job_id"]))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self.connection.execute(
            f"SELECT * FROM jobs{where} ORDER BY accepted_at DESC, job_id DESC LIMIT ?",
            (*values, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def children(self, parent_job_id: str) -> list[dict[str, Any]]:
        """Return one parent's children in stable acceptance order."""
        parent_job_id = validate_job_id(parent_job_id)
        rows = self.connection.execute(
            "SELECT * FROM jobs WHERE parent_job_id=? ORDER BY accepted_at ASC, job_id ASC",
            (parent_job_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def transition(self, job_id: str, target: Lifecycle | str, **fields: Any) -> dict[str, Any]:
        target = Lifecycle(target)
        allowed_fields = {"exit_code", "termination_reason", "output_completeness",
                          "cleanup_state", "integrity_sha256", "result_json", "health",
                          "queue_reason", "queue_position"}
        if set(fields) - allowed_fields:
            raise ValueError("unsupported job transition fields")
        with self.transaction(immediate=True) as connection:
            current = connection.execute("SELECT lifecycle FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if current is None:
                raise JobNotFound(f"job {job_id!r} was not found")
            validate_transition(current[0], target)
            now = _now()
            timing = {}
            if target is Lifecycle.QUEUED: timing["queued_at"] = now
            if target is Lifecycle.RUNNING: timing["started_at"] = now
            if target is Lifecycle.RUNNING:
                fields.setdefault("queue_reason", None)
                fields.setdefault("queue_position", None)
            if target.value in {"succeeded", "failed", "timed_out", "cancelled", "interrupted"}:
                timing["finished_at"] = now
                fields.setdefault("health", Health.TERMINAL.value)
            updates = {"lifecycle": target.value, "updated_at": now, **timing, **fields}
            connection.execute(
                f"UPDATE jobs SET {', '.join(f'{key}=?' for key in updates)} WHERE job_id=?",
                (*updates.values(), job_id),
            )
        return self.get(job_id)

    def set_health(self, job_id: str, health: Health | str, evidence: dict) -> None:
        health = Health(health)
        self.connection.execute("UPDATE jobs SET health=?, updated_at=? WHERE job_id=?",
                                (health.value, _now(), validate_job_id(job_id)))
        self.connection.execute(
            "UPDATE heartbeats SET health_evidence_json=? WHERE job_id=?",
            (json.dumps(evidence, sort_keys=True), job_id),
        )

    def put_process_identity(self, job_id: str, *, host_boot_id: str,
                             supervisor_pid: int, supervisor_start_identity: str,
                             supervisor_nonce_hash: str, child_pid: int | None = None,
                             child_pgid: int | None = None,
                             child_start_identity: str | None = None) -> None:
        now = _now()
        self.connection.execute(
            """INSERT INTO process_identities(job_id, host_boot_id, supervisor_pid,
               supervisor_start_identity, supervisor_nonce_hash, child_pid, child_pgid,
               child_start_identity, recorded_at, last_verified_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(job_id) DO UPDATE SET host_boot_id=excluded.host_boot_id,
               supervisor_pid=excluded.supervisor_pid,
               supervisor_start_identity=excluded.supervisor_start_identity,
               supervisor_nonce_hash=excluded.supervisor_nonce_hash,
               child_pid=excluded.child_pid, child_pgid=excluded.child_pgid,
               child_start_identity=excluded.child_start_identity,
               last_verified_at=excluded.last_verified_at""",
            (job_id, host_boot_id, supervisor_pid, supervisor_start_identity,
             supervisor_nonce_hash, child_pid, child_pgid, child_start_identity, now, now),
        )

    def put_heartbeat(self, job_id: str, *, supervisor_at: str,
                      health_evidence: dict, **fields: Any) -> None:
        names = ("child_observed_at", "last_output_at", "last_activity_at",
                 "last_progress_at", "last_metric_at", "metric_digest")
        values = [fields.get(name) for name in names]
        self.connection.execute(
            f"""INSERT INTO heartbeats(job_id, supervisor_at, {', '.join(names)}, health_evidence_json)
                VALUES({', '.join('?' for _ in range(9))})
                ON CONFLICT(job_id) DO UPDATE SET supervisor_at=excluded.supervisor_at,
                {', '.join(f'{name}=COALESCE(excluded.{name}, heartbeats.{name})' for name in names)},
                health_evidence_json=excluded.health_evidence_json""",
            (job_id, supervisor_at, *values, json.dumps(health_evidence, sort_keys=True)),
        )

    def append_event(self, job_id: str, kind: str, payload: dict) -> int:
        with self.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(sequence), -1) + 1 FROM job_events WHERE job_id=?", (job_id,)
            ).fetchone()
            sequence = int(row[0])
            connection.execute(
                "INSERT INTO job_events(job_id, sequence, kind, occurred_at, payload_json) VALUES(?,?,?,?,?)",
                (job_id, sequence, kind, _now(), json.dumps(payload, sort_keys=True)),
            )
        return sequence

    def upsert_output_stream(self, job_id: str, stream: str, *, bytes_stored: int,
                             events_stored: int, next_sequence: int, **fields: Any) -> None:
        if stream not in {"stdout", "stderr", "combined"}:
            raise ValueError("output stream is invalid")
        values = {
            "first_sequence": fields.get("first_sequence", 0), "next_sequence": next_sequence,
            "bytes_stored": bytes_stored, "events_stored": events_stored,
            "segments": fields.get("segments", 0),
            "last_segment_bytes": fields.get("last_segment_bytes", 0),
            "complete": int(fields.get("complete", False)), "sha256": fields.get("sha256"),
            "updated_at": _now(),
        }
        self.connection.execute(
            """INSERT INTO output_streams(job_id, stream, first_sequence, next_sequence,
               bytes_stored, events_stored, segments, last_segment_bytes, complete, sha256, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(job_id, stream) DO UPDATE SET first_sequence=excluded.first_sequence,
               next_sequence=excluded.next_sequence, bytes_stored=excluded.bytes_stored,
               events_stored=excluded.events_stored, segments=excluded.segments,
               last_segment_bytes=excluded.last_segment_bytes, complete=excluded.complete,
               sha256=excluded.sha256, updated_at=excluded.updated_at""",
            (job_id, stream, *values.values()),
        )

    def upsert_metrics_index(self, job_id: str, *, samples: int, **fields: Any) -> None:
        self.connection.execute(
            """INSERT INTO metrics_index(job_id, samples, first_at, last_at, sha256, complete)
               VALUES(?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET samples=excluded.samples,
               first_at=excluded.first_at, last_at=excluded.last_at,
               sha256=excluded.sha256, complete=excluded.complete""",
            (job_id, samples, fields.get("first_at"), fields.get("last_at"),
             fields.get("sha256"), int(fields.get("complete", False))),
        )

    def add_artifact(self, job_id: str, *, artifact_id: str, display_name: str,
                     stored_relative_path: str, size_bytes: int, sha256: str, **fields: Any) -> None:
        self.connection.execute(
            """INSERT INTO artifacts(artifact_id, job_id, declared_path, stored_relative_path,
               display_name, kind, size_bytes, sha256, media_type, created_at, expires_at,
               status, reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (artifact_id, job_id, fields.get("declared_path"), stored_relative_path,
             display_name, fields.get("kind", "file"), size_bytes, sha256,
             fields.get("media_type", "application/octet-stream"), _now(),
             fields.get("expires_at"), fields.get("status", "available"), fields.get("reason")),
        )

    def record_compatibility_differences(self, job_id: str, differences: list[dict] | tuple[dict, ...]) -> None:
        """Persist bounded CI semantic differences alongside the durable child."""
        rows = []
        if len(differences) > MAX_SUBMISSION_ITEMS:
            raise ValueError("compatibility differences exceed the durable snapshot limit")
        for item in differences:
            safe = _safe_difference(item)
            rows.append((validate_job_id(job_id), safe["id"], safe["workflow"],
                         safe["location"], safe["severity"], int(safe["accepted"]),
                         safe["detail"], safe["catalog_version"]))
        with self.transaction(immediate=True) as connection:
            connection.executemany(
                """INSERT OR REPLACE INTO compatibility_differences(
                   job_id, difference_id, workflow_path, location, severity,
                   accepted, detail, catalog_version) VALUES(?,?,?,?,?,?,?,?)""", rows)

    def snapshot(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        process = self._row(self.connection.execute(
            "SELECT * FROM process_identities WHERE job_id=?", (job_id,)
        ).fetchone())
        heartbeat = self._row(self.connection.execute(
            "SELECT * FROM heartbeats WHERE job_id=?", (job_id,)
        ).fetchone())
        if heartbeat:
            heartbeat["health_evidence"] = json.loads(heartbeat.pop("health_evidence_json"))
        output = [dict(row) for row in self.connection.execute(
            "SELECT * FROM output_streams WHERE job_id=? ORDER BY stream", (job_id,)
        ).fetchall()]
        for stream in output:
            stream["available"] = bool(stream.get("available", 1))
        metrics = self._row(self.connection.execute(
            "SELECT * FROM metrics_index WHERE job_id=?", (job_id,)
        ).fetchone())
        if metrics:
            metrics["available"] = bool(metrics.get("available", 1))
        artifacts = [dict(row) for row in self.connection.execute(
            "SELECT * FROM artifacts WHERE job_id=? ORDER BY artifact_id", (job_id,)
        ).fetchall()]
        differences = [dict(row) for row in self.connection.execute(
            "SELECT difference_id, workflow_path, location, severity, accepted, detail, catalog_version "
            "FROM compatibility_differences WHERE job_id=? ORDER BY difference_id, location", (job_id,)
        ).fetchall()]
        return {**job, "process": process, "heartbeat": heartbeat,
                "output": output, "metrics": metrics, "artifacts": artifacts,
                "compatibility_differences": differences,
                "submission": self.submission_snapshot(job_id)}

    def release_leases(self, job_id: str) -> None:
        with self.transaction(immediate=True) as connection:
            connection.execute("DELETE FROM workspace_leases WHERE job_id=?", (job_id,))
            connection.execute("DELETE FROM host_capacity_leases WHERE job_id=?", (job_id,))

    def mark_retained_metadata_unavailable(self, job_id: str, *, logs: bool = False,
                                           artifacts: bool = False,
                                           metrics: bool = False) -> None:
        job_id = validate_job_id(job_id)
        with self.transaction(immediate=True) as connection:
            if logs:
                connection.execute("UPDATE output_streams SET available=0, updated_at=? WHERE job_id=?",
                                   (_now(), job_id))
            if artifacts:
                connection.execute("UPDATE artifacts SET status='expired', reason='cleanup_removed' "
                                   "WHERE job_id=? AND status='available'", (job_id,))
            if metrics:
                connection.execute("UPDATE metrics_index SET available=0 WHERE job_id=?", (job_id,))

    def set_cleanup_state(self, job_id: str, state: str) -> None:
        if not isinstance(state, str) or not state or any(ord(char) < 32 for char in state):
            raise ValueError("cleanup state is invalid")
        self.connection.execute("UPDATE jobs SET cleanup_state=?, updated_at=? WHERE job_id=?",
                                (state, _now(), validate_job_id(job_id)))
