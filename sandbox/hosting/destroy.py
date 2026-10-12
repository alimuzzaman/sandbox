"""Tear down one hosted target: `sb host destroy`.

A hosted stack is not a Sandbox instance, so `sb instance delete` never sees it.
This removes everything `host apply` created for one remote/project/environment:
the Compose stack with its volumes and built images, the edge route and its
certificates, the remote runtime directory (which holds generated passwords),
the Cloudflare DNS records Sandbox created, the Origin CA certificate, and the
local state entry. It works from recorded state alone, so a target whose
project checkout or manifest is gone can still be removed.

Without ``confirm`` it only reports what it would remove.
"""

from __future__ import annotations

import contextlib
import json
import re
import shlex
import time

import sandbox.core._cloudflare as cloudflare
import sandbox.core._hosting as hosting
import sandbox.core._remote as remote
from sandbox.hosting.state_partition.layout import target_unreadable

_PART_RE = re.compile(r"[a-z0-9][a-z0-9-]*$")
_PROBE_KEYS = ("containers", "volumes", "networks", "images", "runtime_kb", "source_kb", "route", "certs")


def _already_gone(exc: Exception) -> bool:
    text = str(exc)
    return "HTTP 404" in text or "already revoked" in text.lower()


class DestroyError(hosting.HostingError):
    def __init__(self, message: str, code: str = "host_destroy_failed"):
        super().__init__(message)
        self.code = code


def parse_target(value: str) -> tuple[str, str]:
    """``project/environment`` with the same characters apply accepts."""
    parts = (value or "").strip().split("/")
    if len(parts) != 2 or not all(_PART_RE.fullmatch(part) for part in parts):
        raise DestroyError("--target must be <project>/<environment>", "invalid_target")
    return parts[0], parts[1]


def compose_name(project: str, environment: str) -> str:
    return f"sandbox-host-{project}-{environment}"


def _paths(home: str, project: str, environment: str) -> dict:
    return {"runtime": f"{home}/runtime/hosts/{project}/{environment}",
            "project_runtime": f"{home}/runtime/hosts/{project}",
            "source": f"{home}/deploy-src/hosts/{project}"}


def _cert_dirs(name: str, front_door: str) -> list[str]:
    if front_door == "nginx":
        from sandbox.hosting.front_door import nginx as nginx_edge
        return [f"{nginx_edge.CERT_ROOT}/{name.removeprefix('sandbox-')}"]
    return [f"/etc/caddy/certs/{name}"]


def _route_file(name: str, front_door: str) -> str:
    if front_door == "nginx":
        from sandbox.hosting.front_door import nginx as nginx_edge
        return nginx_edge.route_file(name.removeprefix("sandbox-"))
    return f"/etc/caddy/conf.d/{name}.caddy"


def _probe_command(name: str, paths: dict, route: str, certs: list[str]) -> str:
    label = shlex.quote(f"label=com.docker.compose.project={name}")
    cert_test = " -o ".join(f"-d {shlex.quote(path)}" for path in certs)
    # A failed Docker listing must fail the probe, never read as zero.
    return (
        "set -u; if [ \"$(id -u)\" = 0 ]; then SUDO=; else SUDO=sudo; fi; "
        f"c=$(docker ps -aq --filter {label}) || exit 3; "
        f"v=$(docker volume ls -q --filter {label}) || exit 3; "
        f"n=$(docker network ls -q --filter {label}) || exit 3; "
        f"i=$(docker images -q --filter {label}) || exit 3; "
        "echo networks=$(printf '%s' \"$n\" | grep -c .); "
        "echo containers=$(printf '%s' \"$c\" | grep -c .); "
        "echo volumes=$(printf '%s' \"$v\" | grep -c .); "
        "echo images=$(printf '%s' \"$i\" | sort -u | grep -c .); "
        f"echo runtime_kb=$(du -sk {shlex.quote(paths['runtime'])} 2>/dev/null | cut -f1); "
        f"echo source_kb=$(du -sk {shlex.quote(paths['source'])} 2>/dev/null | cut -f1); "
        f"if $SUDO test -f {shlex.quote(route)}; then echo route=1; else echo route=0; fi; "
        f"if $SUDO test {cert_test}; then echo certs=1; else echo certs=0; fi"
    )


def parse_probe(text: str) -> dict:
    found = {}
    for line in (text or "").splitlines():
        key, _, value = line.strip().partition("=")
        if key in _PROBE_KEYS:
            value = value.strip()
            found[key] = int(value) if value.isdigit() else 0
    missing = [key for key in _PROBE_KEYS if key not in found]
    if missing:
        raise DestroyError("remote probe was incomplete: missing " + ", ".join(missing),
                           "remote_probe_failed")
    return found


def _other_environments(state: dict, remote_name: str, project: str, environment: str) -> list[str]:
    prefix = f"{remote_name}/{project}/"
    return sorted(key for key in state.get("hosts", {})
                  if key.startswith(prefix) and key != f"{prefix}{environment}")


def _fence_only(record) -> bool:
    """A placeholder an interrupted destroy left on a colliding name."""
    return isinstance(record, dict) and set(record) <= {"compose_project", "active_operation"} \
        and (record.get("active_operation") or {}).get("action") == "destroy"


def _collisions(state: dict, remote_name: str, key: str, name: str) -> list[str]:
    """Other recorded targets on this remote whose Compose project is the same name.

    ``site-blue/sandbox`` and ``site/blue-sandbox`` share one name; destroying one
    must never remove the other's stack under the wrong lock and fences.
    """
    prefix = f"{remote_name}/"
    found = []
    for other, record in state.get("hosts", {}).items():
        if other == key or not other.startswith(prefix) or _fence_only(record):
            continue
        parts = other.split("/")
        recorded = record.get("compose_project") if isinstance(record, dict) else None
        if recorded == name or (len(parts) == 3 and compose_name(parts[1], parts[2]) == name):
            found.append(other)
    return sorted(found)


def namesakes(remote_name: str, project: str, environment: str) -> list[str]:
    """Every target key whose Compose project name equals this target's.

    Destroy holds all of them, so a first apply of a colliding target cannot
    start while its stack would be removed.
    """
    joined = f"{project}-{environment}"
    keys = set()
    for index, char in enumerate(joined):
        if char == "-":
            left, right = joined[:index], joined[index + 1:]
            if _PART_RE.fullmatch(left) and _PART_RE.fullmatch(right):
                keys.add(f"{remote_name}/{left}/{right}")
    keys.add(f"{remote_name}/{project}/{environment}")
    return sorted(keys)


def plan(state: dict, entry: dict, remote_name: str, project: str, environment: str, *,
         probe=None) -> dict:
    """What destroy would remove. Reads only; ``probe`` is injectable for tests."""
    key = f"{remote_name}/{project}/{environment}"
    record = state.get("hosts", {}).get(key)
    record = record if isinstance(record, dict) else None
    name = (record or {}).get("compose_project") or compose_name(project, environment)
    if name != compose_name(project, environment):
        raise DestroyError(f"recorded compose project {name!r} does not match the target",
                           "state_mismatch")
    collisions = _collisions(state, remote_name, key, name)
    if collisions:
        raise DestroyError(f"{name} also belongs to " + ", ".join(collisions) +
                           "; destroy that target instead", "target_ambiguous")
    unreadable = [owned for owned in namesakes(remote_name, project, environment)
                  if target_unreadable(state, owned)]
    if unreadable:
        # A corrupt record may own this stack or hold its fences and DNS ids.
        raise DestroyError("state for " + ", ".join(unreadable) +
                           " cannot be read; repair it before destroying", "state_unreadable")
    from sandbox.hosting.front_door import front_door_mode
    front_door = (record or {}).get("front_door") or front_door_mode(entry)
    if front_door != "nginx":
        # Caddy route removal cannot prove its reload took effect on a retry.
        raise DestroyError(f"host destroy supports the nginx front door; this target uses {front_door}",
                           "front_door_capability_unavailable")
    home = remote.resolve_sandbox_home(entry)
    paths = _paths(home, project, environment)
    route = _route_file(name, front_door)
    certs = _cert_dirs(name, front_door)
    if probe is None:
        result = remote.ssh_run(entry, _probe_command(name, paths, route, certs), timeout=120)
        if result.returncode != 0:
            raise DestroyError("remote probe failed: " + (result.stderr or "")[-400:],
                               "remote_probe_failed")
        probe = parse_probe(result.stdout)
    others = _other_environments(state, remote_name, project, environment)
    dns = []
    for item in (record or {}).get("records") or []:
        if isinstance(item, dict) and item.get("zone_id") and (
                item.get("created_id") or item.get("previous")):
            dns.append({"zone_id": item["zone_id"],
                        "action": "restore_previous" if item.get("previous") else "delete",
                        "record_id": (item.get("previous") or {}).get("id") or item.get("created_id")})
    certificate = (record or {}).get("certificate")
    origin_cert = certificate.get("id") if isinstance(certificate, dict) else None
    fences = [field for field in ("active_operation", "recovery_uncertainty")
              if record is not None and record.get(field) is not None]
    activation = (record or {}).get("image_activation")
    if isinstance(activation, dict) and activation.get("active") is not None:
        fences.append("image_activation")
    remote_present = any(probe[k] for k in ("containers", "volumes", "networks", "images", "route", "certs")) \
        or bool(probe["runtime_kb"])
    return {
        "target": key, "compose_project": name, "front_door": front_door,
        "state_entry": record is not None, "fences": fences,
        "remote": {**probe, "route_file": route, "cert_dirs": certs,
                   "runtime_dir": paths["runtime"]},
        "other_environments": others,
        "dns_records": dns,
        "origin_certificate": origin_cert,
        "found": record is not None or remote_present,
    }


def _remote_teardown_command(name: str, paths: dict) -> str:
    """Remove the stack and directories; exit non-zero unless all of it is gone.

    Images are removed by this project's own tags (``<project>-<service>``) and
    never forced: another tag keeps a shared image, and an image a remaining
    container still uses makes the command fail with state kept. Everything is
    selected by the project label, never through ``docker compose down``, which
    would read whatever Compose file sits in the login directory and could
    remove an explicitly named volume belonging to something else.
    """
    label = shlex.quote(f"label=com.docker.compose.project={name}")
    own = shlex.quote(f"^{re.escape(name)}-[^:]*:")
    removals = [paths["runtime"]]
    quoted = " ".join(shlex.quote(path) for path in removals)
    tags = f"docker images --format '{{{{.Repository}}}}:{{{{.Tag}}}}' --filter {label}"
    return (
        "set -u; docker info >/dev/null 2>&1 || exit 3; "
        f"docker ps -aq --filter {label} | xargs -r docker rm -f >/dev/null || exit 4; "
        f"docker volume ls -q --filter {label} | xargs -r docker volume rm >/dev/null || exit 4; "
        f"docker network ls -q --filter {label} | xargs -r docker network rm >/dev/null || exit 4; "
        f"t=$({tags}) || exit 3; "
        f"printf '%s\\n' \"$t\" | grep -E {own} | xargs -r docker rmi >/dev/null || exit 5; "
        f"rm -rf {quoted} || exit 6; "
        f"rmdir {shlex.quote(paths['project_runtime'])} 2>/dev/null || true; "
        f"for path in {quoted}; do [ ! -e \"$path\" ] || exit 6; done; "
        f"c=$(docker ps -aq --filter {label}) || exit 3; "
        f"v=$(docker volume ls -q --filter {label}) || exit 3; "
        f"n=$(docker network ls -q --filter {label}) || exit 3; "
        f"t=$({tags}) || exit 3; "
        f"i=$(printf '%s\\n' \"$t\" | grep -E {own} || true); "
        "[ -z \"$c$v$n$i\" ] || exit 7; echo removed"
    )


def _remove_edge(entry: dict, name: str, front_door: str, route_present: bool) -> None:
    """Remove the route if present, then always the certificates (retries included)."""
    if route_present:
        from sandbox.hosting.front_door import nginx as nginx_edge
        nginx_edge.remove_route(entry, nginx_edge_name(name))
    certs = " ".join(shlex.quote(path) for path in _cert_dirs(name, front_door))
    command = "set -u; if [ \"$(id -u)\" = 0 ]; then SUDO=; else SUDO=sudo; fi; " \
              f"$SUDO rm -rf {certs} || exit 1; "
    if front_door == "nginx":
        lineage = shlex.quote("sandbox-" + nginx_edge_name(name))
        command += (f"if $SUDO test -e /etc/letsencrypt/renewal/{lineage}.conf -o "
                    f"-d /etc/letsencrypt/live/{lineage}; then "
                    f"$SUDO certbot delete --non-interactive --cert-name {lineage} >/dev/null || exit 1; fi; ")
    command += f"for path in {certs}; do ! $SUDO test -e \"$path\" || exit 1; done"
    if front_door == "nginx":
        # A retry after an interrupted route removal finds the file gone while
        # nginx may still serve it; reload so the loaded config matches disk.
        command += "; $SUDO nginx -t >/dev/null 2>&1 && $SUDO systemctl reload nginx || exit 1"
    result = remote.ssh_run(entry, command, timeout=120)
    if result.returncode != 0:
        raise DestroyError("certificate removal failed; state kept", "edge_cleanup_failed")


def nginx_edge_name(name: str) -> str:
    return name.removeprefix("sandbox-")


def _remove_cloudflare(found: dict, client) -> list[dict]:
    results = []
    for item in found.get("_records") or []:
        zone_id = item["zone_id"]
        try:
            client.restore_record(zone_id, item.get("previous"), item.get("created_id"))
            results.append({"zone_id": zone_id, "state": "removed"})
        except cloudflare.CloudflareError as exc:
            if not _already_gone(exc):
                raise
            results.append({"zone_id": zone_id, "state": "already_absent"})
    certificate = found.get("origin_certificate")
    if certificate:
        try:
            client.revoke_origin_certificate(certificate)
            results.append({"origin_certificate": "revoked"})
        except cloudflare.CloudflareError as exc:
            if not _already_gone(exc):
                raise
            results.append({"origin_certificate": "already_absent"})
    return results


def execute(entry: dict, remote_name: str, project: str, environment: str, *,
            clear_fenced: bool = False, client_factory=None,
            lock_wait: float = 600) -> dict:
    """Remove the target under its mutation transaction; state goes last.

    The shared state lock is held only to read and to write state, never across
    an SSH call, so a slow remote does not stall other targets.
    """
    from sandbox.hosting.recovery.repository import RecoveryRepository
    key = f"{remote_name}/{project}/{environment}"
    repository = RecoveryRepository()
    with contextlib.ExitStack() as owned:
        for owned_key in namesakes(remote_name, project, environment):
            owned.enter_context(repository.target_mutation_port(
                "destroy", timeout_seconds=lock_wait).target_mutation_transaction(owned_key))
        with repository.state_lock(timeout_seconds=lock_wait):
            state = hosting.load_host_state()
        found = plan(state, entry, remote_name, project, environment)
        record = state.get("hosts", {}).get(key)
        found["_records"] = [item for item in (record or {}).get("records") or []
                             if isinstance(item, dict) and item.get("zone_id")]
        if not found["found"]:
            return {"state": "absent", "target": key}
        if found["fences"] and not clear_fenced:
            raise DestroyError(
                "target is fenced by " + ", ".join(found["fences"]) +
                "; check no apply or recovery is still running, then pass --clear-fenced",
                "target_fenced")
        name = found["compose_project"]
        with repository.state_lock(timeout_seconds=lock_wait):
            # A durable fence: if this run is interrupted while the remote shell
            # keeps going, apply, recovery and a second destroy still refuse.
            # Every key that maps to this Compose name is fenced, so a first apply
            # of a colliding name cannot start under a still-running teardown.
            state = hosting.load_host_state()
            fence = {"action": "destroy", "started_at": int(time.time())}
            for owned_key in namesakes(remote_name, project, environment):
                fenced = state.setdefault("hosts", {}).setdefault(owned_key, {"compose_project": name})
                fenced["active_operation"] = dict(fence)
            repository._write(state)
        paths = _paths(remote.resolve_sandbox_home(entry), project, environment)
        _remove_edge(entry, name, found["front_door"], bool(found["remote"]["route"]))
        result = remote.ssh_run(entry, _remote_teardown_command(name, paths), timeout=600)
        if result.returncode != 0 or "removed" not in result.stdout.split():
            raise DestroyError(f"remote teardown incomplete (exit {result.returncode}); state kept",
                               "remote_teardown_incomplete")
        cloud = []
        if found["_records"] or found["origin_certificate"]:
            client = (client_factory or cloudflare.Client)()
            cloud = _remove_cloudflare(found, client)
        with repository.state_lock(timeout_seconds=lock_wait):
            state = hosting.load_host_state()
            for owned_key in namesakes(remote_name, project, environment):
                state.get("hosts", {}).pop(owned_key, None)
            repository._write(state)
    found.pop("_records", None)
    return {"state": "destroyed", **found, "cloudflare": cloud}


def render(result: dict, *, confirmed: bool) -> str:
    if result.get("state") == "absent" or not result.get("found", True):
        return f"{result['target']}: nothing to remove"
    r = result["remote"]
    lines = [f"{result['target']} ({result['compose_project']}, {result['front_door']} front door)"]
    lines.append(f"  containers {r['containers']}, volumes {r['volumes']}, images {r['images']}")
    lines.append(f"  edge route {'present' if r['route'] else 'absent'}, "
                 f"certificates {'present' if r['certs'] else 'absent'}")
    lines.append(f"  runtime dir {r['runtime_kb']} KB (generated secrets live here)")
    if r.get("source_kb"):
        lines.append(f"  deploy source {r['source_kb']} KB kept (shared by the project's "
                     "environments; remove it by hand once none is left)")
    lines.append(f"  DNS records {len(result['dns_records'])}, origin certificate "
                 f"{'yes' if result['origin_certificate'] else 'none recorded'}")
    lines.append(f"  local state entry {'yes' if result['state_entry'] else 'no'}")
    if result["other_environments"]:
        lines.append("  keeps deploy source for " + ", ".join(result["other_environments"]))
    if result["fences"]:
        lines.append("  fenced by " + ", ".join(result["fences"]) + " (needs --clear-fenced)")
    if not confirmed:
        lines.append("dry run; re-run with --confirm to remove")
    else:
        lines.append("removed")
    return "\n".join(lines)


def dumps(result: dict) -> str:
    return json.dumps(result, sort_keys=True)
