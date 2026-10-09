"""Receipt samples for the control-protocol shape guard (spec 061 FR-006).

Source fingerprints miss keys that a runtime receipt takes from elsewhere: a
job snapshot's columns come from the registry schema, and a materialization
refusal's ``detail`` from a helper. So the guard also runs the installed
runtime's real producers against a throwaway home and records each receipt's
key paths (``a``, ``a.b``, ``a[].c``). A key renamed anywhere along the way
changes the recorded shape and needs a protocol bump. Every child record a
receipt can carry (process, heartbeat, output, metrics, artifacts,
compatibility differences) is seeded first, so SQL-derived keys are sampled
populated rather than as empty lists or nulls.
"""
from __future__ import annotations

import contextlib
import errno
import io
import json
import tempfile
from pathlib import Path


def key_paths(value, prefix: str = "") -> set[str]:
    """Every key path in a JSON value; list items share one ``[]`` path."""
    paths: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            paths.add(path)
            paths |= key_paths(item, path)
    elif isinstance(value, (list, tuple)):
        for item in value:
            paths |= key_paths(item, prefix + "[]")
    return paths


def _job_receipts(home: Path) -> dict[str, object]:
    from sandbox.application.job_service import JobService
    from sandbox.jobs.listing import job_page
    from sandbox.jobs.models import JobSubmission, SourceIdentity
    from sandbox.jobs.registry import JobRepository
    from sandbox.jobs.storage import JobStorage

    repository = JobRepository(home / "registry.sqlite")
    try:
        service = JobService(repository, JobStorage(str(home), free_disk_reserve=0), None,
                             launcher=lambda _descriptor: None)
        accepted = service.submit(JobSubmission(
            "test", str(home), "p", "local", "default", ("echo", "ok"), 60,
            SourceIdentity("source"), parallel_safe=True,
        ))
        job_id = accepted["job_id"]
        _seed_child_records(repository, job_id)
        snapshot = service.get(job_id, reconcile=False)
        page = job_page([snapshot], limit=1, has_more=False)
        artifacts = {"ok": True, "artifacts": service.list_artifacts(job_id)}
    finally:
        repository.close()
    return {"job_accepted": accepted, "job_snapshot": snapshot, "job_list_page": page,
            "job_artifacts": artifacts}


def _seed_child_records(repository, job_id: str) -> None:
    """Populate every SQL-derived snapshot branch, so its columns are sampled (Sol R9-1)."""
    digest = "0" * 64
    repository.put_process_identity(
        job_id, host_boot_id="boot", supervisor_pid=1, supervisor_start_identity="start",
        supervisor_nonce_hash=digest, child_pid=2, child_pgid=2,
        child_cgroup_path="/cgroup", child_start_identity="child")
    repository.put_heartbeat(
        job_id, supervisor_at="2026-01-01T00:00:00Z", health_evidence={"state": "ok"},
        child_observed_at="2026-01-01T00:00:00Z", last_output_at="2026-01-01T00:00:00Z",
        last_activity_at="2026-01-01T00:00:00Z", last_progress_at="2026-01-01T00:00:00Z",
        last_metric_at="2026-01-01T00:00:00Z", metric_digest=digest)
    repository.upsert_output_stream(job_id, "stdout", bytes_stored=1, events_stored=1,
                                    next_sequence=1, sha256=digest)
    repository.upsert_metrics_index(job_id, samples=1, first_at="2026-01-01T00:00:00Z",
                                    last_at="2026-01-01T00:00:00Z", sha256=digest)
    repository.add_artifact(job_id, artifact_id="artifact", display_name="report.txt",
                            stored_relative_path="artifacts/report.txt", size_bytes=1,
                            sha256=digest, declared_path="report.txt")
    repository.record_compatibility_differences(job_id, [{
        "id": "difference", "workflow": "ci.yml", "location": "jobs.test",
        "severity": "notice", "accepted": True, "detail": "detail",
        "catalog_version": "1"}])


def _materialization_receipts(home: Path) -> dict[str, object]:
    from sandbox.workspaces import checkout

    source = home / "source"
    source.mkdir()
    (source / "file.txt").write_text("x", encoding="utf-8")
    owned = source / "file.txt"

    def run(argv) -> object:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            checkout._main(argv)
        return json.loads(out.getvalue().strip().splitlines()[-1])

    argv = ["materialize", "--source", str(source), "--workspace", str(home / "workspace")]
    succeeded = run(argv)

    def refuse(_plan):
        detail = checkout._failure_detail(
            PermissionError(errno.EACCES, "Permission denied", str(owned)), "copy", (source,))
        raise checkout.WorkspaceMaterializationError(
            "workspace_materialization_failed", "workspace materialization failed during copy",
            detail)

    original = checkout.materialize
    checkout.materialize = refuse
    try:
        refused = run(argv + ["--label", "refused"])
    finally:
        checkout.materialize = original
    return {"materialization_receipt": succeeded, "materialization_refusal": refused}


def receipt_shapes() -> dict[str, list[str]]:
    """``receipt:<name>`` -> sorted key paths of each sampled runtime receipt."""
    with tempfile.TemporaryDirectory() as temp:
        home = Path(temp)
        jobs = home / "jobs"
        jobs.mkdir()
        samples = {**_job_receipts(jobs), **_materialization_receipts(home)}
    return {f"receipt:{name}": sorted(key_paths(value)) for name, value in samples.items()}
