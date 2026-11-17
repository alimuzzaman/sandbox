"""Blue-green host apply by side-loading (docs/research/blue-green-stack-apply.md).

Pure rules only: manifest validation, the shared/colored split, the side
project's Compose model, and the route upstream swap. No remote effects.

The steady state never changes: one base project on loopback port P. During an
apply the new revision's colored services run as the side project on
P + SIDE_OFFSET, the route moves there, the base is recreated, and the route
moves back.
"""
from __future__ import annotations

import hashlib
import re

STRATEGIES = ("auto", "blue-green", "in-place")
SIDE_OFFSET = 1000
SIDE_PREFIX = "sandbox-hostside-"
_SERVICE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")


class Ineligible(ValueError):
    """This apply cannot side-load; `auto` falls back to in-place."""


def validate(deploy: dict, compose: dict) -> dict:
    """Return {"strategy", "shared", "colored"} from deploy.strategy / deploy.blue_green.

    shared: stateful services that stay single. colored: side-loaded services
    allowed to mount data (a named volume or writable bind) beside the running
    revision, e.g. WordPress on its docroot; any other data mount refuses.
    """
    strategy = deploy.get("strategy", "auto")
    if strategy not in STRATEGIES:
        raise ValueError("deploy.strategy must be auto, blue-green or in-place")
    block = deploy.get("blue_green", {})
    if not isinstance(block, dict) or set(block) - {"shared", "colored"}:
        raise ValueError("deploy.blue_green accepts only shared and colored")
    declared = [compose["service"], *compose.get("background_services", []),
                *compose.get("init_services", [])]
    lists = {}
    for key in ("shared", "colored"):
        names = block.get(key, [])
        if (not isinstance(names, list) or len(set(names)) != len(names)
                or any(not isinstance(name, str) or not _SERVICE.fullmatch(name) for name in names)):
            raise ValueError(f"deploy.blue_green.{key} must be a list of service names")
        unknown = [name for name in names if name not in declared]
        if unknown:
            raise ValueError(f"deploy.blue_green.{key} names undeclared services: " + ", ".join(unknown))
        lists[key] = list(names)
    if compose["service"] in lists["shared"]:
        raise ValueError("deploy.blue_green.shared cannot include the primary compose.service")
    if set(lists["shared"]) & set(lists["colored"]):
        raise ValueError("a service cannot be both deploy.blue_green.shared and colored")
    if strategy == "in-place" and (lists["shared"] or lists["colored"]):
        raise ValueError("deploy.blue_green needs deploy.strategy auto or blue-green")
    return {"strategy": strategy, **lists}


def revision_exempt(validated: dict) -> set[str]:
    """Shared services keep their container across revisions, so the
    pushed-revision check does not expect the new revision in them."""
    plan = (validated.get("deploy") or {}).get("blue_green_plan") or {}
    if plan.get("strategy") == "in-place":
        return set()
    return set(plan.get("shared") or ())


def side_project(validated: dict) -> str:
    return f"{SIDE_PREFIX}{validated['project']}-{validated['environment']}"


def side_network(validated: dict) -> str:
    return side_project(validated) + "_net"


def side_port(loopback_port: int) -> int:
    return int(loopback_port) + SIDE_OFFSET


def split(model: dict, validated: dict, *, gate_service: str | None = None) -> dict:
    """{"colored": [...], "shared": [...]} over the services apply starts."""
    compose = validated["compose"]
    services = model.get("services")
    if not isinstance(services, dict):
        raise Ineligible("the resolved Compose model has no services")
    plan = validated["deploy"].get("blue_green_plan") or {}
    started = [compose["service"], *compose.get("background_services", [])]
    shared = set(plan.get("shared") or ()) | set(compose.get("init_services", []))
    if gate_service:
        shared.add(gate_service)
    colored = []
    for name in started:
        if name not in services:
            raise Ineligible(f"service {name} is missing from the resolved model")
        if name in shared:
            continue
        service = services[name]
        if service.get("ports") and name != compose["service"]:
            shared.add(name)  # e.g. Mailpit's own loopback inbox: one container
            continue
        colored.append(name)
    if compose["service"] not in colored:
        raise Ineligible("the primary service must be side-loadable")
    # Two database servers on one data directory corrupt it: a colored service
    # may mount data only when the manifest says it is safe to run twice.
    allowed = set(plan.get("colored") or ())
    stateful = sorted(name for name in colored
                      if _data_mounts(services[name]) and name not in allowed)
    if stateful:
        raise Ineligible(", ".join(stateful) + " mount data; list each in "
                         "deploy.blue_green.shared (stateful) or colored (safe to run twice)")
    known = set(colored) | shared
    for name in colored:
        service = services[name]
        if service.get("container_name"):
            raise Ineligible(f"{name} sets container_name")
        if service.get("network_mode"):
            raise Ineligible(f"{name} sets network_mode")
        networks = service.get("networks") or {}
        if set(networks) - {"default"}:
            raise Ineligible(f"{name} joins a declared network")
        for key in ("links", "external_links", "volumes_from", "secrets", "configs"):
            if service.get(key):
                raise Ineligible(f"{name} uses {key}")
        for key in ("ipc", "pid"):
            if str(service.get(key) or "").startswith(("service:", "container:")):
                raise Ineligible(f"{name} shares another service's {key} namespace")
        outside = sorted(set(service.get("depends_on") or {}) - known)
        if outside:
            # In place would start them as dependencies; the side never does.
            raise Ineligible(f"{name} depends on services apply does not start: " + ", ".join(outside))
    return {"colored": colored, "shared": sorted(shared & set(services))}


def _named_volumes(service: dict) -> list[str]:
    return [item.get("source") for item in service.get("volumes") or ()
            if isinstance(item, dict) and item.get("type") == "volume" and item.get("source")]


def _data_mounts(service: dict) -> list[str]:
    """Named volumes and writable bind mounts: anything two copies could both write."""
    found = []
    for item in service.get("volumes") or ():
        if not isinstance(item, dict):
            continue
        if item.get("type") == "volume" and item.get("source"):
            found.append(item["source"])
        elif item.get("type") == "bind" and not item.get("read_only"):
            found.append(str(item.get("source")))
    return found


def render_side(model: dict, validated: dict, *, base_project: str, colored: list[str],
                shared: list[str], port: int) -> dict:
    """The side project's whole Compose model (written as JSON, which Compose reads)."""
    compose = validated["compose"]
    services = {}
    for name in colored:
        service = {key: value for key, value in model["services"][name].items()
                   if key not in ("ports", "build", "depends_on", "networks")}
        if not service.get("image"):
            if "build" not in model["services"][name]:
                raise Ineligible(f"{name} has neither image nor build")
            # Compose's own tag for a built service without an image name.
            service["image"] = f"{base_project}-{name}"
        depends = model["services"][name].get("depends_on") or {}
        kept = {dep: spec for dep, spec in depends.items() if dep in colored}
        if kept:
            service["depends_on"] = kept
        service["pull_policy"] = "never"
        if name == compose["service"]:
            service["ports"] = [{"mode": "ingress", "host_ip": "127.0.0.1",
                                 "target": int(compose["container_port"]),
                                 "published": str(int(port)), "protocol": "tcp"}]
        services[name] = service
    volumes = {}
    for name in colored:
        for source in _named_volumes(model["services"][name]):
            declared = (model.get("volumes") or {}).get(source) or {}
            if declared.get("external"):
                volumes[source] = {"name": declared.get("name") or source, "external": True}
            else:
                volumes[source] = {"name": declared.get("name") or f"{base_project}_{source}",
                                   "external": True}
    side = {"name": side_project(validated), "services": services,
            "networks": {"default": {"name": side_network(validated), "external": True}}}
    if volumes:
        side["volumes"] = volumes
    del shared  # the shared containers join the side network; they are not in it
    return side


_UPSTREAM = r"(?<![0-9.])127\.0\.0\.1:{port}(?![0-9])"
# nginx.render_host_conf: location = /.well-known/sandbox-edge-route { ... return 200 "<24 hex>\n"; }
_MARKER = re.compile(r'(?<=return 200 ")([0-9a-f]{24})(?=\\n";)')


def swap_upstream(text: str, old: int, new: int) -> str:
    """Point every loopback upstream at old to new; nothing else changes."""
    if not isinstance(text, str) or not text:
        raise Ineligible("no installed route to switch")
    pattern = re.compile(_UPSTREAM.format(port=int(old)))
    swapped, count = pattern.subn(f"127.0.0.1:{int(new)}", text)
    if count == 0:
        raise Ineligible(f"the installed route has no 127.0.0.1:{int(old)} upstream")
    # The nginx reload probe proves a route loaded by its marker. A marker
    # rendered for P would also answer from the P route, so the side route
    # carries its own, derived from the old one and the new port.
    for marker in set(_MARKER.findall(swapped)):
        fresh = hashlib.sha256(f"{marker}:{int(new)}".encode()).hexdigest()[:24]
        swapped = swapped.replace(marker, fresh)
    return swapped


def serves_port(text: str | None, port: int) -> bool:
    return bool(text) and re.search(_UPSTREAM.format(port=int(port)), text) is not None
