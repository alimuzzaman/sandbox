"""Workspace job evidence and stack teardown for remote reclaim (spec 066).

Both functions are injected verbatim into the remote resource program (see
``remote._program``), so they import only the standard library, inside the
function body, and take every side effect as an argument.
"""
from __future__ import annotations

DEFAULT_FINISHED_JOB_IDLE_SECONDS = 6 * 3600


def job_workspace_references(jobs, *, now, idle_seconds):
    """Map each job's canonical project root to its reclaim references.

    ``retained_job`` protects the workspace: any non-terminal job (queued,
    running, cancelling, unknown), and any retained terminal job that finished
    within ``idle_seconds`` or whose finish time is unknown. A retained terminal
    job that finished longer ago adds ``finished_job_idle`` instead. Any
    ``retained_job`` on a path wins over ``finished_job_idle``.
    """
    from datetime import datetime
    from pathlib import Path

    terminal = {"succeeded", "failed", "timed_out", "cancelled", "interrupted"}

    def finished_epoch(value):
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str) and value:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
            except ValueError:
                return None
        return None

    references = {}
    for job in jobs or ():
        if not isinstance(job, dict):
            continue
        root = job.get("project_root")
        if not isinstance(root, str) or not root:
            continue
        lifecycle = job.get("lifecycle")
        retained = (
            job.get("cleanup_state") == "retained"
            or (job.get("cleanup_policy") == "retain"
                and job.get("cleanup_state") != "completed")
        )
        if lifecycle in terminal and not retained:
            continue
        reference = "retained_job"
        if lifecycle in terminal:
            finished = finished_epoch(job.get("finished_at"))
            if finished is not None and now - finished >= idle_seconds:
                reference = "finished_job_idle"
        canonical = str(Path(root).resolve(strict=False))
        references.setdefault(canonical, set()).add(reference)
    return {
        path: (("retained_job",) if "retained_job" in refs else ("finished_job_idle",))
        for path, refs in references.items()
    }


def teardown_workspace_stack(run, workspace, container_ids, *, record=None):
    """Remove the compose project(s) exclusively owned by one workspace.

    ``run(argv, timeout) -> (code, stdout, stderr)``. Only a workspace
    checkout (``<repo>-workspace-<hash>``) owns a stack. Its projects are every
    container or volume project named ``sandbox-<workspace>``, a
    ``sandbox-<workspace>-<label>`` instance of it, or a truncation of that
    name that still carries the full ``-workspace-`` marker plus 8 hash
    characters; never a hosted ``sandbox-host-*`` project. Projects are found
    from the engine, not only from containers bound to the checkout, because a
    job database never binds it. Volumes still used by a container outside the
    project, and networks with outside endpoints, are kept and reported.
    """
    import json

    result = {"ok": True, "projects": [], "containers_removed": 0,
              "volumes_removed": [], "volumes_kept_shared": [],
              "networks_removed": [], "networks_kept": [], "failures": []}

    record = record or (lambda kind, locator: None)
    marker = "-workspace-"
    if not isinstance(workspace, str) or marker not in workspace:
        return result
    minimum = workspace.index(marker) + len(marker) + 8

    def owned(project):
        if not isinstance(project, str) or not project.startswith("sandbox-"):
            return False
        if project.startswith("sandbox-host-"):
            return False
        segment = project[len("sandbox-"):]
        if segment == workspace or segment.startswith(workspace + "-"):
            return True
        return len(segment) >= minimum and workspace.startswith(segment)

    def lines(code, out):
        return [line.strip() for line in str(out or "").splitlines() if line.strip()] \
            if code == 0 else None

    ids = [item for item in container_ids or () if isinstance(item, str) and item]
    projects = []
    if ids:
        code, out, _err = run(["docker", "inspect", "--format",
                               '{{json .Config.Labels}}'] + ids, 60)
        if code != 0:
            result["ok"] = False
            result["failures"].append("inspect_failed")
            return result
        for line in str(out or "").splitlines():
            try:
                labels = json.loads(line) or {}
            except ValueError:
                result["ok"] = False
                result["failures"].append("inspect_invalid")
                return result
            project = labels.get("com.docker.compose.project") if isinstance(labels, dict) else None
            if not owned(project):
                result["ok"] = False
                result["failures"].append("bound_container_not_exclusive")
                return result
            if project not in projects:
                projects.append(project)
    label_format = '{{.Label "com.docker.compose.project"}}'
    for argv in (["docker", "ps", "-a", "--format", label_format],
                 ["docker", "volume", "ls", "--format", label_format]):
        names = lines(*run(argv, 60)[:2])
        if names is None:
            result["ok"] = False
            result["failures"].append("list_projects_failed")
            return result
        for project in names:
            if owned(project) and project not in projects:
                projects.append(project)
    result["projects"] = projects
    for project in projects:
        label = "label=com.docker.compose.project=" + project
        members = lines(*run(["docker", "ps", "-aq", "--filter", label], 30)[:2])
        if members is None:
            result["ok"] = False
            result["failures"].append("list_containers_failed:" + project)
            continue
        for container in members:
            record("container", container)
            run(["docker", "stop", "-t", "5", container], 30)
            code, _out, err = run(["docker", "rm", "-f", container], 30)
            if code == 0 or "no such container" in str(err).lower():
                result["containers_removed"] += 1
            else:
                result["ok"] = False
                result["failures"].append("container_rm_failed")
        volumes = lines(*run(["docker", "volume", "ls", "-q", "--filter", label], 30)[:2])
        if volumes is None:
            result["ok"] = False
            result["failures"].append("list_volumes_failed:" + project)
            volumes = []
        for volume in volumes:
            users = lines(*run(["docker", "ps", "-aq", "--filter", "volume=" + volume], 30)[:2])
            if users is None:
                result["ok"] = False
                result["failures"].append("volume_users_unknown")
                continue
            if users:
                result["volumes_kept_shared"].append(volume)
                continue
            record("volume", volume)
            code, _out, err = run(["docker", "volume", "rm", volume], 60)
            if code == 0 or "no such volume" in str(err).lower():
                result["volumes_removed"].append(volume)
            else:
                result["ok"] = False
                result["failures"].append("volume_rm_failed")
        networks = lines(*run(["docker", "network", "ls", "-q", "--filter", label], 30)[:2])
        if networks is None:
            result["ok"] = False
            result["failures"].append("list_networks_failed:" + project)
            networks = []
        for network in networks:
            record("network", network)
            code, _out, err = run(["docker", "network", "rm", network], 30)
            if code == 0 or "no such network" in str(err).lower():
                result["networks_removed"].append(network)
            elif "active endpoints" in str(err).lower():
                result["networks_kept"].append(network)
            else:
                result["ok"] = False
                result["failures"].append("network_rm_failed")
    return result
