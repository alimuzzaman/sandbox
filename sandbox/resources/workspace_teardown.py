"""Workspace job evidence and stack teardown for remote reclaim (spec 066).

Both functions are injected verbatim into the remote resource program (see
``remote._program``), so they import only the standard library, inside the
function body, and take every side effect as an argument.
"""
from __future__ import annotations

DEFAULT_FINISHED_JOB_IDLE_SECONDS = 6 * 3600
# Spec 080: a workspace with more bound strays than this is left for review.
MAX_STRAYS_PER_CHECKOUT = 10


def owns_compose_project(workspace, project):
    """Whether compose ``project`` belongs to workspace checkout ``workspace``.

    ``sandbox-<workspace>``, a ``sandbox-<workspace>-<label>`` instance of it,
    or a truncation of that name that still carries the full ``-workspace-``
    marker plus 8 hash characters; never a hosted ``sandbox-host-*`` project.
    """
    marker = "-workspace-"
    if (not isinstance(workspace, str) or marker not in workspace
            or not isinstance(project, str) or not project.startswith("sandbox-")
            or project.startswith("sandbox-host-")):
        return False
    segment = project[len("sandbox-"):]
    if segment == workspace or segment.startswith(workspace + "-"):
        return True
    minimum = workspace.index(marker) + len(marker) + 8
    return len(segment) >= minimum and workspace.startswith(segment)


def assess_stray_container(container, checkout):
    """Decide whether one unlabeled container is a stray owned by ``checkout``.

    ``container`` is one ``docker inspect`` object (or the same keys from a
    format template). Returns ``{"owned", "reason", "evidence", "fingerprint"}``:
    ``owned`` is true only when the container has no Compose, stack, swarm or
    Sandbox label and no ``sandbox-`` name, every mount is a bind whose source
    (as written and resolved) is ``checkout`` or below it, it uses only the
    default bridge network, and its restart policy is none. ``reason`` names
    the first rule that protects it. ``fingerprint`` hashes the evidence the
    cleanup compares before acting (spec 080 research R4); it leaves out the
    running state so a stop that a crash interrupted still matches.
    """
    import hashlib
    import json
    import os

    def verdict(reason, evidence=None, fingerprint=None):
        return {"owned": reason is None, "reason": reason,
                "evidence": evidence or {}, "fingerprint": fingerprint}

    def section(name):
        value = container.get(name)
        return value if isinstance(value, dict) else None

    def inside(path, root):
        return path == root or path.startswith(root.rstrip("/") + "/")

    if not isinstance(container, dict) or not isinstance(checkout, str):
        return verdict("invalid_record")
    identity = container.get("Id")
    name = container.get("Name")
    config, host, state = section("Config"), section("HostConfig"), section("State")
    settings = section("NetworkSettings")
    mounts = container.get("Mounts")
    if (not isinstance(identity, str) or not identity or not isinstance(name, str)
            or config is None or host is None or state is None or settings is None
            or not isinstance(mounts, list) or not os.path.isabs(checkout)
            or os.path.normpath(checkout) != checkout.rstrip("/")):
        return verdict("invalid_record")
    labels = config.get("Labels") or {}
    networks = settings.get("Networks") or {}
    restart = host.get("RestartPolicy") or {}
    if (not isinstance(labels, dict) or not isinstance(networks, dict)
            or not isinstance(restart, dict)):
        return verdict("invalid_record")
    display = name.lstrip("/")
    mount_rows = []
    for mount in mounts:
        if not isinstance(mount, dict):
            return verdict("invalid_record")
        mount_rows.append({
            "type": mount.get("Type"), "source": mount.get("Source"),
            "destination": mount.get("Destination"), "name": mount.get("Name"),
            "rw": mount.get("RW"),
        })
    evidence = {
        "id": identity, "name": display, "image": config.get("Image"),
        "created": container.get("Created"), "started_at": state.get("StartedAt"),
        "restart_count": container.get("RestartCount"),
        "running": state.get("Running") is True,
        "network_mode": host.get("NetworkMode"), "networks": sorted(networks),
        "mounts": [{"type": row["type"], "source": row["source"],
                    "destination": row["destination"]} for row in mount_rows],
    }
    fingerprint = hashlib.sha256(json.dumps({
        "id": identity, "name": name, "created": container.get("Created"),
        "config_image": config.get("Image"), "image": container.get("Image"),
        "labels": labels,
        "mounts": sorted(mount_rows, key=lambda row: json.dumps(row, sort_keys=True)),
        "network_mode": host.get("NetworkMode"), "networks": sorted(networks),
        "restart_policy": restart.get("Name"), "started_at": state.get("StartedAt"),
        "restart_count": container.get("RestartCount"),
    }, sort_keys=True, default=str).encode()).hexdigest()
    for key in labels:
        lowered = str(key).lower()
        if lowered.startswith(("com.docker.compose.", "com.docker.stack.",
                               "com.docker.swarm.")):
            return verdict("compose_project", evidence, fingerprint)
    if (any("sandbox" in str(key).lower() for key in labels)
            or display.startswith("sandbox-")):
        return verdict("sandbox_identity", evidence, fingerprint)
    if not mount_rows:
        return verdict("no_mounts", evidence, fingerprint)
    root = checkout.rstrip("/")
    resolved_root = os.path.realpath(root)
    for row in mount_rows:
        if row["type"] == "volume":
            return verdict("named_volume", evidence, fingerprint)
        if row["type"] != "bind":
            return verdict("unsupported_mount", evidence, fingerprint)
        source = row["source"]
        if (not isinstance(source, str) or not os.path.isabs(source)
                or os.path.normpath(source) != source.rstrip("/")
                or not inside(source.rstrip("/"), root)
                or not inside(os.path.realpath(source), resolved_root)):
            return verdict("bind_outside_checkout", evidence, fingerprint)
    if (host.get("NetworkMode") not in ("default", "bridge")
            or any(net != "bridge" for net in networks)):
        return verdict("non_default_network", evidence, fingerprint)
    if restart.get("Name") not in (None, "", "no"):
        return verdict("restart_policy", evidence, fingerprint)
    return verdict(None, evidence, fingerprint)


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


def teardown_workspace_stack(run, workspace, container_ids, *, record=None,
                             strays=None, checkout=None):
    """Remove the compose project(s) exclusively owned by one workspace.

    Spec 080: ``strays`` (a list, possibly empty, of the plan's
    ``remove_containers``) and ``checkout`` (the planned path) switch on the
    reviewed-plan mode. Every container bound to the checkout is listed fresh;
    an unplanned or changed unlabeled one, or a foreign project, refuses the
    whole workspace before anything is touched. Each planned stray is then
    recorded, stopped and removed, and only then is the project torn down.
    With ``strays`` None (an older plan) any unlabeled bound container still
    refuses, as before.

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

    record = record or (lambda kind, locator, **extra: None)
    if not isinstance(workspace, str) or "-workspace-" not in workspace:
        return result

    def owned(project):
        return owns_compose_project(workspace, project)

    def lines(code, out):
        return [line.strip() for line in str(out or "").splitlines() if line.strip()] \
            if code == 0 else None

    ids = [item for item in container_ids or () if isinstance(item, str) and item]
    projects = []
    if strays is not None:
        # Spec 080: judge every container bound to the checkout now, before
        # any mutation, against the reviewed plan.
        def refuse(reason):
            result["ok"] = False
            result["failures"].append(reason)
            return result

        result["strays_removed"] = []
        planned = {}
        # 10 is MAX_STRAYS_PER_CHECKOUT; this body is shipped without globals.
        if (not isinstance(strays, list) or len(strays) > 10
                or not isinstance(checkout, str) or not checkout.startswith("/")):
            return refuse("invalid_planned_strays")
        for item in strays:
            if (not isinstance(item, dict) or not isinstance(item.get("id"), str)
                    or not item["id"] or not isinstance(item.get("fingerprint"), str)
                    or not item["fingerprint"] or item["id"] in planned):
                return refuse("invalid_planned_strays")
            planned[item["id"]] = item["fingerprint"]
        code, out, _err = run(["docker", "ps", "-aq", "--no-trunc"], 30)
        present = lines(code, out)
        if present is None:
            return refuse("bound_list_failed")

        def missing(err):
            # IDs a "No such object" error names; None for any other error.
            # A container removed between two engine calls is not evidence
            # of anything, but only once that exact ID is reported gone.
            named = set()
            for line in str(err or "").splitlines():
                line = line.strip()
                if not line:
                    continue
                lowered = line.lower()
                if "no such object" not in lowered and "no such container" not in lowered:
                    return None
                named.add(line.rsplit(" ", 1)[-1].strip())
            return named

        def reconciled(code, err, requested, seen):
            gone = missing(err) if code != 0 else set()
            if gone is None or (code != 0 and not gone):
                return None
            if any(item not in seen and item not in gone for item in requested):
                return None
            return gone

        bound = []
        alive = set()
        root = checkout.rstrip("/")
        for offset in range(0, len(present), 100):
            batch = present[offset:offset + 100]
            code, out, err = run(["docker", "inspect", "--format",
                                  "{{json .Id}} {{json .Mounts}}"] + batch, 60)
            seen = set()
            for line in str(out or "").splitlines():
                if not line.strip():
                    continue
                text = line.strip()
                try:
                    identity, end = json.JSONDecoder().raw_decode(text)
                    mounts = json.loads(text[end:])
                except (ValueError, TypeError):
                    return refuse("bound_list_failed")
                if not isinstance(identity, str) or not isinstance(mounts, list):
                    return refuse("bound_list_failed")
                seen.add(identity)
                for mount in mounts:
                    source = str((mount or {}).get("Source") or "") if isinstance(mount, dict) else ""
                    if source and (source.rstrip("/") == root or source.startswith(root + "/")):
                        bound.append(identity)
                        break
            if reconciled(code, err, batch, seen) is None:
                return refuse("bound_list_failed")
            alive.update(seen)
        inspected = []
        if bound:
            code, out, err = run(["docker", "inspect"] + bound, 60)
            try:
                inspected = json.loads(out or "[]")
            except ValueError:
                return refuse("bound_inspect_failed")
            if not isinstance(inspected, list) or not all(isinstance(r, dict) for r in inspected):
                return refuse("bound_inspect_failed")
            gone = reconciled(code, err, bound, {r.get("Id") for r in inspected})
            if gone is None:
                return refuse("bound_inspect_failed")
            alive.difference_update(gone)
        matched = []
        for found in inspected:
            identity = found.get("Id")
            labels = (found.get("Config") or {}).get("Labels") or {}
            project = labels.get("com.docker.compose.project") if isinstance(labels, dict) else None
            if project:
                if not owned(project):
                    return refuse("bound_container_not_exclusive")
                if project not in projects:
                    projects.append(project)
                continue
            if identity not in planned:
                return refuse("unplanned_bound_container:" + str(identity))
            verdict = assess_stray_container(found, checkout)
            if not verdict["owned"] or verdict["fingerprint"] != planned[identity]:
                return refuse("container_evidence_changed:" + identity)
            matched.append(identity)
        for identity in planned:
            if identity in alive and identity not in matched:
                # Still on the host but no longer bound (or no longer judged
                # as the same container): not what the operator reviewed.
                return refuse("container_evidence_changed:" + identity)

        def current(identity):
            # Docker has no compare-and-remove, so each mutation is preceded
            # by a fresh inspect of that one container; what is left is the
            # gap between this inspect and the engine call that follows.
            code, out, err = run(["docker", "inspect", identity], 30)
            if code != 0:
                gone = missing(err)
                return "absent" if gone is not None and identity in gone else None
            try:
                rows = json.loads(out or "[]")
            except ValueError:
                return None
            if (not isinstance(rows, list) or len(rows) != 1
                    or not isinstance(rows[0], dict) or rows[0].get("Id") != identity):
                return None
            verdict = assess_stray_container(rows[0], checkout)
            if verdict["owned"] and verdict["fingerprint"] == planned[identity]:
                return "match"
            return "changed"

        for identity in planned:
            if identity not in matched:
                result["strays_removed"].append(identity)
                continue
            for step in ("stop", "rm"):
                state = current(identity)
                if state == "absent":
                    break
                if state is None:
                    return refuse("stray_inspect_failed:" + identity)
                if state != "match":
                    return refuse("container_evidence_changed:" + identity)
                if step == "stop":
                    record("stray_container", identity, fingerprint=planned[identity])
                    argv = ["docker", "stop", "-t", "5", identity]
                else:
                    argv = ["docker", "rm", identity]
                code, _out, err = run(argv, 30)
                if code != 0 and "no such container" not in str(err).lower():
                    return refuse("stray_" + step + "_failed:" + identity)
            result["strays_removed"].append(identity)
        ids = []
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
