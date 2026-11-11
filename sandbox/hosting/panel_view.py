"""One read-only view of stacks and hosted sites for `sb web` (spec 076).

The view is built from two sources with the same shape:

* ``this-server``: the summaries and site links `panel_records` reads on a
  Sandbox remote;
* ``controller``: the controller's stack registry and hosting state, turned
  into the same records by :func:`controller_records`.

Nothing here writes, probes or reads a secret. Health comes in through a
lookup function so the builder stays pure.
"""
from __future__ import annotations

import re
import time
from itertools import islice
from typing import TYPE_CHECKING, Callable, Iterable, Literal, Mapping, TypedDict
from urllib.parse import urlsplit

from sandbox.hosting import panel_lifetime as pl
from sandbox.hosting import panel_records as pr
from sandbox.hosting import stack_registry
from sandbox.hosting.panel_health import NOT_CHECKED, HealthResult

if TYPE_CHECKING:
    from typing import NotRequired

Source = Literal["this-server", "controller"]
Origin = Literal["route", "linked", "matched_by_name"]
RowState = Literal["serving", "redirecting", "applying", "failed", "instance_not_found", "unknown"]

MAX_KNOWN_SITES = 1000
MAX_CONTROLLER_INPUT = 2000

HealthLookup = Callable[[list[tuple[str, str]]], dict[str, HealthResult]]


class Ref(TypedDict):
    branch: str | None
    commit: str | None


class ServerRow(TypedDict):
    hostname: str
    url: str | None
    origin: Origin
    mode: Literal["serve", "redirect"]
    target: str | None
    primary: bool
    state: RowState
    ref: Ref
    instance: str | None
    health: HealthResult


class ApplyView(TypedDict):
    status: str
    started_at: int | None
    finished_at: int | None
    request_id: str | None
    reason: NotRequired[str]


class StackPageAccess(TypedDict):
    uses: list[str]
    inbox: dict[str, str] | None
    inbox_reason: str | None
    revision_status: Literal["verified", "unverified", "not_delivered"]
    sign_in: SignInAvailability


class SignInAvailability(TypedDict):
    available: bool
    code: str
    message: str


class StackView(TypedDict):
    key: str
    remote: str
    source: Source
    slug: str
    project: str
    environment: str
    ref: str
    ref_branch: str
    commit: str
    template: str
    ports: dict[str, int]
    mailpit_port: int | None
    loopback_port: int | None
    created_at: int | None
    ttl_expires_at: int | None
    last_apply: ApplyView
    compose_project: str
    lifetime: pl.LifetimeView
    servers: list[ServerRow]
    page_access: NotRequired[StackPageAccess]


class TargetView(TypedDict):
    key: str
    remote: str
    source: Source
    project: str
    environment: str
    commit: str | None
    loopback_port: int | None
    last_apply: ApplyView | None
    recorded: bool
    compose_project: str
    lifetime: pl.LifetimeView
    stack_slug: str | None  # from the lifetime record when there is no summary
    servers: list[ServerRow]


class HostingView(TypedDict):
    ok: bool
    generated_at: int
    sources: list[Source]
    stacks: list[StackView]
    targets: list[TargetView]
    lifetimes: list[pl.LifetimeRow]
    partial_reasons: list[str]


class KnownSite(TypedDict):
    """A hostname this machine serves for a Sandbox instance (server source only)."""
    hostname: str
    instance: str | None


class SourceRecords(TypedDict):
    source: Source
    targets: list[pr.TargetSummary]
    legacy: list[pr.LegacyTarget]
    links: list[pr.SiteLink]
    partial_reasons: list[str]


# -------------------------------------------------------- controller source

def _controller_apply(record_apply: dict | None, fallback_status: str | None) -> pr.LastApply | None:
    if record_apply:
        status = str(record_apply.get("status") or "")
        mapped = ("succeeded" if status == "succeeded" else
                  "failed" if status in ("failed", "submission_failed", "cancelled", "timed_out",
                                         "interrupted") else
                  "applying")
        started = record_apply.get("submitted_at")
        finished = record_apply.get("finished_at")
        return {"status": mapped,  # type: ignore[typeddict-item]
                "started_at": started if isinstance(started, int) else 0,
                "finished_at": finished if isinstance(finished, int) else None,
                "request_id": record_apply.get("request_id") or None}
    if fallback_status:
        return {"status": fallback_status, "started_at": 0, "finished_at": None,  # type: ignore[typeddict-item]
                "request_id": None}
    return None


def _host_entry_status(entry: dict) -> str | None:
    runtime = (entry.get("runtime") or {}).get("state")
    edge = (entry.get("edge") or {}).get("state")
    if runtime == "ready" and edge == "ready":
        return "succeeded"
    if runtime == "pending" or edge == "pending":
        return "applying"
    if runtime or edge:
        return "failed"
    return None


def controller_records(stack_data: dict, host_state: dict,
                       last_apply_view: Callable[[dict], dict]) -> SourceRecords:
    """Turn the controller's stack registry and hosting state into panel records."""
    out: SourceRecords = {"source": "controller", "targets": [], "legacy": [], "links": [],
                          "partial_reasons": []}
    hosts = host_state.get("hosts") if isinstance(host_state, dict) else None
    hosts = hosts if isinstance(hosts, dict) else {}
    stacks = stack_data.get("stacks") if isinstance(stack_data, dict) else None
    stacks = stacks if isinstance(stacks, dict) else {}
    def key_of(record: dict) -> str:
        return f"{record.get('remote')}/{record.get('project')}/{record.get('environment')}"

    def full() -> bool:
        if len(out["targets"]) < pr.MAX_TARGETS:
            return False
        if "controller_records_truncated" not in out["partial_reasons"]:
            note("controller_records_truncated")
        return True

    def note(reason: str) -> None:
        pr.add_reason(out["partial_reasons"], reason)

    def bounded_hosts(values: object, key: str) -> list[str]:
        if not isinstance(values, list):
            return []
        hostnames = sorted({str(value) for value in islice(values, MAX_CONTROLLER_INPUT)})
        if len(values) > MAX_CONTROLLER_INPUT or len(hostnames) > pr.MAX_ROUTES:
            note(f"controller_hostnames_truncated:{key}")
        return hostnames[:pr.MAX_ROUTES]

    # The controller's files are already parsed, but walk at most a fixed
    # number of entries from each so oversized state stays cheap to show.
    stack_records = list(islice(stacks.values(), MAX_CONTROLLER_INPUT))
    host_entries = list(islice(hosts.items(), MAX_CONTROLLER_INPUT))
    if len(stacks) > MAX_CONTROLLER_INPUT or len(hosts) > MAX_CONTROLLER_INPUT:
        note("controller_records_truncated")
    claimed = {key_of(record) for record in stack_records if isinstance(record, dict)}
    for record in stack_records:
        if not isinstance(record, dict):
            continue
        if full():
            break
        key = key_of(record)
        entry = hosts.get(key) if isinstance(hosts.get(key), dict) else {}
        hostnames = bounded_hosts(record.get("hostnames") or entry.get("hostnames") or [], key)
        try:
            refined = last_apply_view(record)
            delivered = stack_registry.delivered_commit(record, refined.get("status"))
            last = _controller_apply(refined, None) or {
                "status": "applying", "started_at": 0, "finished_at": None, "request_id": None}
            stack = {"slug": record.get("slug"), "ref": record.get("ref"),
                     "ref_branch": record.get("ref_branch"), "commit": delivered,
                     "template": record.get("template"), "ports": record.get("ports") or {}}
            out["targets"].append(pr.parse_target_summary({
                "schema": pr.SCHEMA, "remote": record.get("remote"),
                "project": record.get("project"), "environment": record.get("environment"),
                "compose_project": entry.get("compose_project")
                or f"sandbox-host-{record.get('project')}-{record.get('environment')}",
                "loopback_port": entry.get("loopback_port"), "healthcheck_path": "/",
                "routes": [{"hostname": host, "mode": "serve", "primary": False} for host in hostnames],
                "commit": delivered, "last_apply": last,
                "stack": pr.build_stack_facts(stack, record)}))
        except (ValueError, TypeError, AttributeError) as exc:
            note(f"stack_record_unreadable:{record.get('slug')}:{type(exc).__name__}")
            continue
        sites = record.get("sites") if isinstance(record.get("sites"), list) else []
        if len(sites) > MAX_CONTROLLER_INPUT:
            note(f"site_links_truncated:{record.get('slug')}")
        for site in islice(sites, MAX_CONTROLLER_INPUT):
            if len(out["links"]) >= pr.MAX_SITE_LINKS:
                note("site_links_truncated")
                break
            try:
                out["links"].append(pr.build_site_link(
                    remote=str(record.get("remote")), slug=str(record.get("slug")),
                    hostname=site.get("hostname"), instance=site.get("instance"),
                    label=site.get("label") or "default", commit=site.get("commit"),
                    recorded_at=site.get("recorded_at") or 0))
            except (ValueError, TypeError, AttributeError) as exc:
                note(f"site_link_unreadable:{record.get('slug')}:{type(exc).__name__}")
    for key, entry in sorted(host_entries, key=lambda item: str(item[0])):
        if key in claimed or not isinstance(entry, dict):
            continue
        if full():
            break
        remote, _, rest = str(key).partition("/")
        project, _, environment = rest.partition("/")
        hostnames = bounded_hosts(entry.get("hostnames") or [], str(key))
        if not hostnames:
            continue
        try:
            out["targets"].append(pr.parse_target_summary({
                "schema": pr.SCHEMA, "remote": remote, "project": project, "environment": environment,
                "compose_project": entry.get("compose_project") or f"sandbox-host-{project}-{environment}",
                "loopback_port": entry.get("loopback_port"), "healthcheck_path": "/",
                "routes": [{"hostname": host, "mode": "serve", "primary": False} for host in hostnames],
                "commit": entry.get("commit"),
                "last_apply": _controller_apply(None, _host_entry_status(entry)) or {
                    "status": "applying", "started_at": 0, "finished_at": None, "request_id": None},
                "stack": None}))
        except (ValueError, TypeError) as exc:
            note(f"host_entry_unreadable:{key}:{type(exc).__name__}")
    return out


def server_source(records: pr.ServerRecords) -> SourceRecords:
    return {"source": "this-server", **records}  # type: ignore[typeddict-item]


def known_sites(registry_rows: Iterable[dict], route_file_names: Iterable[str]) -> list[KnownSite]:
    """Hostnames this machine serves for instances: registry URLs and Caddy instance route file names."""
    sites: dict[str, KnownSite] = {}
    for index, row in enumerate(registry_rows):
        if index >= MAX_KNOWN_SITES:
            break
        if not isinstance(row, dict):
            continue
        try:
            host = (urlsplit(str(row.get("url") or "")).hostname or "").lower()
        except ValueError:
            continue
        if "." in host and not host.endswith((".tst", ".test.local")) and host != "localhost":
            sites[host] = {"hostname": host, "instance": str(row.get("instance") or "") or None}
    for index, name in enumerate(route_file_names):
        if index >= MAX_KNOWN_SITES:
            break
        match = re.fullmatch(r"sandbox-instance-(.+)\.caddy", name)
        if match and match.group(1) not in sites:
            sites[match.group(1)] = {"hostname": match.group(1), "instance": None}
    return [site for _, site in sorted(sites.items())]


# ------------------------------------------------------------------ builder

def _apply_view(last: pr.LastApply | None) -> ApplyView | None:
    if last is None:
        return None
    view: ApplyView = {"status": last["status"], "started_at": last["started_at"] or None,
                       "finished_at": last["finished_at"], "request_id": last["request_id"]}
    if last.get("reason"):
        view["reason"] = last["reason"]  # type: ignore[typeddict-item]
    return view


def _route_state(last: pr.LastApply | None, mode: str) -> RowState:
    if last is None:
        return "unknown"
    if last["status"] == "applying":
        return "applying"
    if last["status"] == "failed":
        return "failed"
    # "succeeded" and "unverified" both mean the apply ran: the route is in place.
    return "redirecting" if mode == "redirect" else "serving"


def _row(hostname: str, *, origin: Origin, mode: str = "serve", target: str | None = None,
         primary: bool = False, state: RowState, ref: Ref, instance: str | None = None,
         probe_path: str = "/") -> tuple[ServerRow, tuple[str, str] | None]:
    wildcard = hostname.startswith("*.")
    row: ServerRow = {"hostname": hostname, "url": None if wildcard else f"https://{hostname}/",
                      "origin": origin, "mode": mode,  # type: ignore[typeddict-item]
                      "target": target, "primary": primary, "state": state, "ref": ref,
                      "instance": instance, "health": dict(NOT_CHECKED)}  # type: ignore[typeddict-item]
    return row, (None if wildcard else (hostname, probe_path if mode == "serve" else "/"))


def _zones(slug: str, hostnames: Iterable[str]) -> set[str]:
    marker = f"-{slug}."
    return {host.split(marker, 1)[1] for host in hostnames if marker in host and not host.startswith("*.")}


def _compose_project(project: str, environment: str) -> str:
    return f"sandbox-host-{project}-{environment}"


LifetimeSources = Mapping[str, "pl.LifetimeListing | None"]


def build_view(sources: list[SourceRecords], *, known: list[KnownSite] | None = None,
               health: HealthLookup | None = None,
               lifetimes: LifetimeSources | None = None,
               clock: Callable[[], float] = time.time) -> HostingView:
    """``lifetimes`` maps ``"this-server"`` (this server's records) and each
    controller remote name to its spec 077 listing; ``None`` means not loaded."""
    known = known or []
    lifetimes = lifetimes or {}
    partial: list[str] = []
    chosen: dict[tuple[str, str, str], tuple[Source, pr.TargetSummary]] = {}
    legacy: dict[tuple[str, str], pr.LegacyTarget] = {}
    links: list[tuple[Source, pr.SiteLink]] = []
    for source in sources:
        for reason in source["partial_reasons"]:
            pr.add_reason(partial, reason)
        for target in source["targets"]:
            key = (target["remote"], target["project"], target["environment"])
            if key not in chosen or (source["source"] == "this-server" and chosen[key][0] != "this-server"):
                chosen[key] = (source["source"], target)
        for item in source["legacy"]:
            legacy[(item["project"], item["environment"])] = item
        links.extend((source["source"], link) for link in source["links"])

    for listing in lifetimes.values():
        for reason in (listing or {}).get("partial_reasons") or []:
            pr.add_reason(partial, reason)

    def lifetime_source(source: Source, remote: str) -> pl.LifetimeListing | None:
        return lifetimes.get("this-server" if source == "this-server" else remote)

    lifetime_rows: list[pl.LifetimeRow] = []
    carded: set[tuple[str, str]] = set()

    def lifetime_for(source: Source, remote: str, project: str, environment: str, name: str,
                     compose_project: str) -> pl.LifetimeView:
        view = pl.lookup(lifetime_source(source, remote), compose_project)
        key = ("this-server" if source == "this-server" else remote, compose_project)
        if key not in carded:
            carded.add(key)
            lifetime_rows.append({**view, "remote": remote, "source": source, "project": project,  # type: ignore[typeddict-item]
                                  "environment": environment, "compose_project": compose_project,
                                  "name": name, "card": True})
        return view

    known_instances = {site["instance"] for site in known if site["instance"]}
    route_hosts = {route["hostname"] for _, target in chosen.values() for route in target["routes"]}
    stacks: list[StackView] = []
    targets: list[TargetView] = []
    probes: list[tuple[str, str]] = []
    rows_by_host: dict[str, list[ServerRow]] = {}

    def add(row: ServerRow, probe: tuple[str, str] | None, bucket: list[ServerRow]) -> None:
        bucket.append(row)
        rows_by_host.setdefault(row["hostname"], []).append(row)
        if probe:
            probes.append(probe)

    def route_rows(target: pr.TargetSummary, ref: Ref, bucket: list[ServerRow]) -> None:
        ordered = sorted(target["routes"], key=lambda route: (not route.get("primary"), route["hostname"]))
        for route in ordered:
            row, probe = _row(route["hostname"], origin="route", mode=route["mode"],
                              target=route.get("target"), primary=bool(route.get("primary")),
                              state=_route_state(target["last_apply"], route["mode"]), ref=ref,
                              probe_path=target["healthcheck_path"])
            add(row, probe, bucket)

    # Longest slug wins a name match, so `wp-demo-2.<zone>` belongs to `demo-2`, not `2`.
    stack_targets = sorted(((source, target) for source, target in chosen.values() if target["stack"]),
                           key=lambda item: (item[1]["remote"], item[1]["stack"]["slug"]))
    linked_hosts = {link["hostname"] for _, link in links}
    matches: dict[tuple[str, str], list[KnownSite]] = {}
    for site in known:
        host = site["hostname"]
        if host in route_hosts or host in linked_hosts:
            continue
        best: tuple[int, tuple[str, str]] | None = None
        for source, target in stack_targets:
            if source != "this-server":
                continue
            slug = target["stack"]["slug"]
            for zone in _zones(slug, (route["hostname"] for route in target["routes"])):
                if re.fullmatch(rf"[a-z0-9]+(?:-[a-z0-9]+)*-{re.escape(slug)}\.{re.escape(zone)}", host):
                    if best is None or len(slug) > best[0]:
                        best = (len(slug), (target["remote"], slug))
        if best:
            matches.setdefault(best[1], []).append(site)

    for source, target in stack_targets:
        stack = target["stack"]
        ref: Ref = {"branch": stack.get("ref_branch") or None, "commit": stack.get("commit") or target["commit"]}
        servers: list[ServerRow] = []
        route_rows(target, ref, servers)
        stack_links = sorted((link for link_source, link in links
                              if link["slug"] == stack["slug"]
                              and (link["remote"] == target["remote"] or link_source == "this-server")
                              and link_source == source),
                             key=lambda link: link["hostname"])
        for link in stack_links:
            if source == "this-server":
                state: RowState = "serving" if link["instance"] in known_instances else "instance_not_found"
            else:
                state = "unknown"
            row, probe = _row(link["hostname"], origin="linked", state=state,
                              ref={"branch": None, "commit": link["commit"]}, instance=link["instance"])
            add(row, probe, servers)
        for site in sorted(matches.get((target["remote"], stack["slug"]), []), key=lambda s: s["hostname"]):
            row, probe = _row(site["hostname"], origin="matched_by_name", state="serving",
                              ref={"branch": None, "commit": None}, instance=site["instance"])
            add(row, probe, servers)
        ports = dict(stack.get("ports") or {})
        compose_project = target["compose_project"]
        stacks.append({
            "key": f"{target['remote']}/{stack['slug']}", "remote": target["remote"], "source": source,
            "slug": stack["slug"], "project": target["project"], "environment": target["environment"],
            "ref": stack.get("ref") or "", "ref_branch": stack.get("ref_branch") or "",
            "commit": stack.get("commit") or (target["commit"] or ""),
            "template": stack.get("template") or "", "ports": ports,
            "mailpit_port": ports.get("mailpit"), "loopback_port": target["loopback_port"],
            "created_at": stack.get("created_at"), "ttl_expires_at": stack.get("ttl_expires_at"),
            "last_apply": _apply_view(target["last_apply"]),  # type: ignore[typeddict-item]
            "compose_project": compose_project,
            "lifetime": lifetime_for(source, target["remote"], target["project"], target["environment"],
                                     stack["slug"], compose_project),
            "servers": servers,
        })

    for source, target in sorted(chosen.values(), key=lambda item: (item[1]["remote"], item[1]["project"],
                                                                      item[1]["environment"])):
        if target["stack"]:
            continue
        servers = []
        route_rows(target, {"branch": None, "commit": target["commit"]}, servers)
        name = f"{target['project']} · {target['environment']}"
        lifetime = lifetime_for(source, target["remote"], target["project"], target["environment"],
                                name, target["compose_project"])
        targets.append({
            "key": f"{target['remote']}/{target['project']}/{target['environment']}",
            "remote": target["remote"], "source": source, "project": target["project"],
            "environment": target["environment"], "commit": target["commit"],
            "loopback_port": target["loopback_port"], "last_apply": _apply_view(target["last_apply"]),
            "recorded": True, "compose_project": target["compose_project"], "lifetime": lifetime,
            "stack_slug": lifetime["slug"] if lifetime["kind"] == "stack" else None,
            "servers": servers,
        })
    recorded_pairs = {(target["project"], target["environment"])
                      for source, target in chosen.values() if source == "this-server"}
    for (project, environment), item in sorted(legacy.items()):
        if (project, environment) in recorded_pairs:
            continue
        compose_project = _compose_project(project, environment)
        lifetime = pl.lookup(lifetimes.get("this-server"), compose_project)
        stack_slug = lifetime["slug"] if lifetime["kind"] == "stack" else None
        lifetime = lifetime_for("this-server", "this-server", project, environment,
                                stack_slug or f"{project} · {environment}", compose_project)
        targets.append({"key": f"this-server/{project}/{environment}", "remote": "this-server",
                        "source": "this-server", "project": project, "environment": environment,
                        "commit": lifetime["commit"], "loopback_port": None, "last_apply": None,
                        "recorded": False, "compose_project": compose_project, "lifetime": lifetime,
                        "stack_slug": stack_slug, "servers": []})

    # Lifetime records with no card of their own (a copy whose summary is gone,
    # or a target only the record knows) are still listed, never hidden.
    for key, listing in sorted(lifetimes.items()):
        if not listing or not listing.get("available"):
            continue
        source: Source = "this-server" if key == "this-server" else "controller"
        for compose_project, view in sorted(listing["records"].items()):
            if (key, compose_project) in carded:
                continue
            carded.add((key, compose_project))
            pair = pl.identity(listing, compose_project) or ("", "")
            name = view["slug"] or (f"{pair[0]} · {pair[1]}" if pair[0] else compose_project)
            lifetime_rows.append({**view, "remote": key, "source": source, "project": pair[0],  # type: ignore[typeddict-item]
                                  "environment": pair[1], "compose_project": compose_project,
                                  "name": name, "card": False})

    if health is not None and probes:
        results = health(probes)
        for hostname, rows in rows_by_host.items():
            if hostname in results:
                for row in rows:
                    row["health"] = dict(results[hostname])  # type: ignore[typeddict-item]
    present = sorted({source["source"] for source in sources})
    return {"ok": True, "generated_at": int(clock()), "sources": present,  # type: ignore[typeddict-item]
            "stacks": stacks, "targets": targets, "lifetimes": pl.order(lifetime_rows),
            "partial_reasons": partial}
