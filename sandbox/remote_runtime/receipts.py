"""Receipt samples for the control-protocol shape guard (spec 061 FR-006).

Source fingerprints miss keys that a runtime receipt takes from elsewhere: a
job snapshot's columns come from the registry schema, and a materialization
refusal's ``detail`` from a helper. So the guard also runs the installed
runtime's real producers against a throwaway home and records each receipt's
key paths (``a``, ``a.b``, ``a[].c``). A key renamed anywhere along the way
changes the recorded shape and needs a protocol bump.
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
        snapshot = service.get(accepted["job_id"], reconcile=False)
        page = job_page([snapshot], limit=1, has_more=False)
    finally:
        repository.close()
    return {"job_accepted": accepted, "job_snapshot": snapshot, "job_list_page": page}


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
