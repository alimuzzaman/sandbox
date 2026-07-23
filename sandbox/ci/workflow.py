"""Side-effect-free remote CI workflow loading, graphing, and preflight."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .compatibility import CATALOG_VERSION, detect


class WorkflowError(ValueError):
    pass


def load_workflow(project_root: str | Path, workflow_path: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    path = (root / workflow_path).resolve() if not Path(workflow_path).is_absolute() else Path(workflow_path).resolve()
    if root not in path.parents:
        raise WorkflowError("workflow path must remain under the project")
    try:
        import yaml
        value = yaml.safe_load(path.read_text()) or {}
    except OSError as exc:
        raise WorkflowError(f"could not read workflow: {exc}") from exc
    except Exception as exc:
        raise WorkflowError(f"workflow YAML is invalid: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("jobs"), dict):
        raise WorkflowError("workflow must contain a jobs mapping")
    return value


def matrix_cells(job: dict) -> list[dict]:
    matrix = (job.get("strategy") or {}).get("matrix") or {}
    if not isinstance(matrix, dict): return [{}]
    include = matrix.get("include")
    if include and not [key for key in matrix if key not in {"include", "exclude"}]:
        return [dict(value) for value in include]
    axes = {key: values for key, values in matrix.items() if key not in {"include", "exclude"}}
    cells = [{}]
    for key, values in axes.items():
        if not isinstance(values, list): raise WorkflowError(f"matrix axis {key!r} must be a list")
        cells = [{**cell, key: value} for cell in cells for value in values]
    return cells


def preflight(project_root: str | Path, workflow_path: str | Path, *, selected_jobs: list[str] | None = None,
              accepted_differences: list[str] | None = None, safe_mode: bool = True) -> dict:
    workflow = load_workflow(project_root, workflow_path)
    jobs = workflow["jobs"]
    selected = selected_jobs or list(jobs)
    unknown = [name for name in selected if name not in jobs]
    if unknown: raise WorkflowError(f"unknown workflow jobs: {', '.join(unknown)}")
    active_jobs = list(dict.fromkeys(selected))
    active_job_set = set(active_jobs)
    pending = list(selected)
    while pending:
        job_id = pending.pop()
        needs = jobs[job_id].get("needs", [])
        needs = needs if isinstance(needs, list) else [needs]
        for dependency in needs:
            if dependency not in jobs:
                raise WorkflowError(f"job {job_id!r} needs unknown job {dependency!r}")
            if dependency not in active_job_set:
                active_job_set.add(dependency)
                active_jobs.append(dependency)
                pending.append(dependency)

    differences = [item for item in detect(workflow) if not item["location"].startswith("jobs.") or
                   any(item["location"].startswith(f"jobs.{job_id}.") for job_id in active_job_set)]
    accepted = set(accepted_differences or ())
    for item in differences: item["accepted"] = item["id"] in accepted
    blocking = [item["id"] for item in differences if item["severity"] == "block" and not item["accepted"]]
    safe_actions = []
    for job_id in active_jobs:
        for index, step in enumerate(jobs[job_id].get("steps") or []):
            text = str(step.get("uses") or step.get("run") or "").lower()
            if any(word in text for word in ("deploy", "release", "publish", "git push", "svn commit")):
                location = f"jobs.{job_id}.steps[{index}]"
                difference_id = f"safe-mode:{job_id}:{index}"
                safe_actions.append({"id": difference_id, "location": location,
                                     "action": "neutralized" if safe_mode else "allowed"})
                if safe_mode:
                    differences.append({"id": difference_id, "workflow": str(workflow_path),
                        "location": location, "severity": "notice", "accepted": True,
                        "detail": "deployment/release/publish step is neutralized in safe mode",
                        "catalog_version": CATALOG_VERSION})
    return {"ok": not blocking, "compatible": not blocking, "catalog_version": CATALOG_VERSION,
            "engine": {"name": "act", "version": "observed-at-execution"},
            "runner": {"platform": "linux", "accepted": True},
            "graph": {"jobs": list(jobs), "selected_jobs": selected,
                      "dependencies": {name: jobs[name].get("needs", []) if isinstance(jobs[name].get("needs", []), list) else [jobs[name].get("needs")] for name in selected},
                      "matrix_cells": sum(len(matrix_cells(jobs[name])) for name in active_jobs)},
            "differences": differences, "safe_mode_actions": safe_actions, "blocking": sorted(set(blocking))}
