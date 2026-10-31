"""``sb host heal`` (spec 066): restart hosted services that gave up.

A hosted container whose ``on-failure`` restart policy ran out of retries
(typically after the disk filled) stays exited until someone starts it. Heal
lists exactly those containers and, only with ``--confirm`` and only while free
space is strictly above the remote's storage floor, starts them again. It never
recreates, pulls, rebuilds or deletes anything.
"""
from __future__ import annotations

import json
import re
import shlex

from sandbox.core._remote import ssh_run
from sandbox.resources.storage_floor import GB, floor_bytes, remote_free_space

HOSTED_PREFIX = "sandbox-host-"
_ID = re.compile(r"^[0-9a-f]{12,64}$")
_INVENTORY = ("ids=$(docker ps -aq --filter status=exited --filter "
              "label=com.docker.compose.project) || exit $?; "
              "if [ -z \"$ids\" ]; then echo '[]'; else docker inspect $ids; fi")


def select_candidates(inventory, *, project: str | None = None,
                      environment: str | None = None) -> list[dict]:
    wanted = None
    if project and environment:
        wanted = f"{HOSTED_PREFIX}{project}-{environment}"
    picked = []
    for item in inventory or ():
        if not isinstance(item, dict):
            continue
        labels = (item.get("Config") or {}).get("Labels") or {}
        compose = labels.get("com.docker.compose.project") if isinstance(labels, dict) else None
        if not isinstance(compose, str) or not compose.startswith(HOSTED_PREFIX):
            continue
        if wanted is not None and compose != wanted:
            continue
        if project and wanted is None and not compose.startswith(f"{HOSTED_PREFIX}{project}-"):
            continue
        state = item.get("State") or {}
        policy = (item.get("HostConfig") or {}).get("RestartPolicy") or {}
        maximum = policy.get("MaximumRetryCount")
        count = item.get("RestartCount")
        if (state.get("Status") != "exited" or policy.get("Name") != "on-failure"
                or type(state.get("ExitCode")) is not int or state["ExitCode"] <= 0):
            continue
        if type(maximum) is not int or maximum <= 0 or type(count) is not int \
                or count < maximum:
            continue
        cid = str(item.get("Id") or "")
        picked.append({"id": cid, "container": str(item.get("Name") or "").lstrip("/") or cid[:12],
                       "project": compose, "restart_count": count, "max_retries": maximum,
                       "exit_code": state.get("ExitCode")})
    return picked


def _inventory(remote: dict):
    try:
        result = ssh_run(remote, _INVENTORY, timeout=60)
    except Exception:
        return None
    if getattr(result, "returncode", 1) != 0:
        return None
    try:
        data = json.loads(getattr(result, "stdout", "") or "")
    except ValueError:
        return None
    return data if isinstance(data, list) else None


def _storage(remote: dict) -> dict:
    configured = remote.get("storage_floor_gb")
    floor = floor_bytes(remote) or 0
    valid = configured is None or type(configured) is int and configured >= 0
    space = remote_free_space(remote)
    free = space.get("free_bytes")
    return {"free_bytes": free, "floor_bytes": floor,
            "above_floor": valid and space.get("status") == "complete" and type(free) is int and free > floor}


def run(remote: dict, *, project: str | None = None, environment: str | None = None,
        confirm: bool = False) -> dict:
    inventory = _inventory(remote)
    if inventory is None:
        return {"ok": False, "action": "heal", "code": "inventory_unavailable",
                "error": "could not list hosted containers on the remote"}
    candidates = select_candidates(inventory, project=project, environment=environment)
    storage = _storage(remote)
    result = {"ok": True, "action": "heal", "dry_run": not confirm,
              "storage": storage, "candidates": candidates, "restarted": []}
    if not confirm or not candidates:
        return result
    if not storage["above_floor"]:
        return {**result, "ok": False, "code": "storage_floor_not_clear",
                "error": "free space is not above the storage floor; reclaim space first"}
    # Re-select immediately before acting: only what is still exhausted and
    # was in the preview gets started.
    again = _inventory(remote)
    if again is None:
        return {**result, "ok": False, "code": "inventory_unavailable",
                "error": "could not re-list hosted containers on the remote"}
    still = {c["id"] for c in select_candidates(again, project=project, environment=environment)}
    targets = [c for c in candidates if c["id"] in still and _ID.fullmatch(c["id"])]
    storage = result["storage"] = _storage(remote)
    if not storage["above_floor"]:
        return {**result, "ok": False, "code": "storage_floor_not_clear",
                "error": "free space is not above the storage floor; reclaim space first"}
    if targets:
        started = ssh_run(remote, "docker start " + " ".join(shlex.quote(c["id"]) for c in targets),
                timeout=120)
    for target in targets:
        probe = ssh_run(remote, "docker inspect --format '{{.State.Status}}' "
                        + shlex.quote(target["id"]), timeout=30)
        state = (getattr(probe, "stdout", "") or "").strip() or "unknown"
        result["restarted"].append({"container": target["container"], "state": state})
    result["ok"] = (not targets or getattr(started, "returncode", 1) == 0) and all(
        item["state"] == "running" for item in result["restarted"])
    return result


def render(result: dict) -> str:
    storage = result.get("storage") or {}
    free = storage.get("free_bytes")
    lines = [f"free {'unknown' if free is None else f'{free / GB:.1f} GB'}, "
             f"floor {(storage.get('floor_bytes') or 0) / GB:.1f} GB"]
    candidates = result.get("candidates") or []
    if not candidates:
        lines.append("no hosted service is stopped after exhausting its restarts")
    for item in candidates:
        lines.append(f"  {item['project']}  {item['container']}  exit {item['exit_code']}  "
                     f"restarts {item['restart_count']}/{item['max_retries']}")
    if result.get("dry_run") and candidates:
        lines.append("dry run: nothing restarted; re-run with --confirm to start these")
    for item in result.get("restarted") or []:
        lines.append(f"  started {item['container']}: {item['state']}")
    if result.get("error"):
        lines.append(f"error: {result['error']} ({result.get('code')})")
    return "\n".join(lines)
