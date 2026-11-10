"""Secret-free records the `sb web` panel reads to show stacks and hosted sites (spec 073).

`sb host apply` runs on a controller and writes to the remote over its existing
connection, so a panel running on the remote cannot see the controller's stack
registry or hosting state. Two small records give it what it needs:

* ``runtime/hosts/<project>/<environment>/summary.json``: one hosted target,
  written by apply when it starts, succeeds and fails;
* ``runtime/stack-sites/<slug>/<hostname>.json``: one WordPress site that
  ``sb deploy --stack`` made for a stack.

Both hold an explicit allow-list of fields. No secret value or secret key name
is ever copied in, and the target's ``environment.env`` is never opened.
"""
from __future__ import annotations

import base64
import json
import os
import posixpath
import re
import shlex
import time
from pathlib import Path, PurePosixPath
from typing import Callable, Literal, TypedDict

from sandbox.hosting.names import TARGET_PART_RE

SCHEMA = 1
SUMMARY_NAME = "summary.json"
SITES_DIR = "stack-sites"
MAX_RECORD_BYTES = 64 * 1024
MAX_TARGETS = 200
MAX_SITE_LINKS = 500
MAX_ROUTES = 64

ApplyStatus = Literal["applying", "succeeded", "failed"]
APPLY_STATUSES: frozenset[str] = frozenset({"applying", "succeeded", "failed"})

SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
_ENV_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z")
_HOST_RE = re.compile(r"(?:\*\.)?[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\Z")
_PORT_NAME_RE = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")
_TEXT_LIMIT = 200


class Route(TypedDict, total=False):
    hostname: str
    mode: Literal["serve", "redirect"]
    primary: bool
    target: str


class LastApply(TypedDict):
    status: ApplyStatus
    started_at: int
    finished_at: int | None
    request_id: str | None


class StackFacts(TypedDict, total=False):
    slug: str
    ref: str
    ref_branch: str
    commit: str
    template: str
    ports: dict[str, int]
    created_at: int | None
    ttl_expires_at: int


class TargetSummary(TypedDict):
    schema: int
    remote: str
    project: str
    environment: str
    compose_project: str
    loopback_port: int | None
    healthcheck_path: str
    routes: list[Route]
    commit: str | None
    last_apply: LastApply
    stack: StackFacts | None


class SiteLink(TypedDict):
    schema: int
    remote: str
    slug: str
    hostname: str
    instance: str
    label: str
    commit: str | None
    recorded_at: int


class LegacyTarget(TypedDict):
    project: str
    environment: str


class ServerRecords(TypedDict):
    targets: list[TargetSummary]
    legacy: list[LegacyTarget]
    links: list[SiteLink]
    partial_reasons: list[str]


class RecordError(ValueError):
    """A record failed validation at the read or build boundary."""


# ------------------------------------------------------------------ paths

def summary_path(home: str, project: str, environment: str) -> str:
    return posixpath.join(home, "runtime", "hosts", project, environment, SUMMARY_NAME)


def site_link_path(home: str, slug: str, hostname: str) -> str:
    return posixpath.join(home, "runtime", SITES_DIR, slug, f"{hostname}.json")


# --------------------------------------------------------- field parsers

def _text(value: object, field: str, *, required: bool = True, limit: int = _TEXT_LIMIT) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value) or len(value) > limit:
        raise RecordError(f"{field} must be a string of at most {limit} characters")
    return value


def _epoch(value: object, field: str, *, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RecordError(f"{field} must be a non-negative integer")
    return value


def _port(value: object, field: str, *, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise RecordError(f"{field} must be a TCP port")
    return value


def _commit(value: object, field: str) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not COMMIT_RE.fullmatch(value):
        raise RecordError(f"{field} must be a full commit")
    return value


def _hostname(value: object, field: str, *, wildcard: bool = True) -> str:
    host = value.strip().rstrip(".").lower() if isinstance(value, str) else ""
    if (not _HOST_RE.fullmatch(host) or len(host) > 253 or ".." in host
            or (host.startswith("*.") and not wildcard)):
        raise RecordError(f"{field} must be a hostname")
    return host


def _part(value: object, field: str) -> str:
    if not isinstance(value, str) or not TARGET_PART_RE.fullmatch(value):
        raise RecordError(f"{field} must use lowercase letters, numbers and hyphens")
    return value


def _environment(value: object) -> str:
    if not isinstance(value, str) or not _ENV_NAME_RE.fullmatch(value):
        raise RecordError("environment is invalid")
    return value


def _slug(value: object) -> str:
    if not isinstance(value, str) or not SLUG_RE.fullmatch(value):
        raise RecordError("slug is invalid")
    return value


def _ports(value: object) -> dict[str, int]:
    if not isinstance(value, dict) or len(value) > 32:
        raise RecordError("ports must map names to ports")
    ports: dict[str, int] = {}
    for name, port in value.items():
        if not isinstance(name, str) or not _PORT_NAME_RE.fullmatch(name):
            raise RecordError("port names must be short lowercase identifiers")
        ports[name] = _port(port, f"ports.{name}")  # type: ignore[assignment]
    return dict(sorted(ports.items()))


def _route(value: object) -> Route:
    if not isinstance(value, dict):
        raise RecordError("each route must be a mapping")
    mode = value.get("mode")
    if mode not in ("serve", "redirect"):
        raise RecordError("route mode must be serve or redirect")
    route: Route = {"hostname": _hostname(value.get("hostname"), "route hostname"),
                    "mode": mode, "primary": value.get("primary") is True}
    if mode == "redirect":
        route["target"] = _text(value.get("target"), "redirect target", limit=500)
    return route


def _last_apply(value: object) -> LastApply:
    if not isinstance(value, dict) or value.get("status") not in APPLY_STATUSES:
        raise RecordError("last_apply.status is invalid")
    request_id = value.get("request_id")
    return {"status": value["status"],
            "started_at": _epoch(value.get("started_at"), "last_apply.started_at"),  # type: ignore[typeddict-item]
            "finished_at": _epoch(value.get("finished_at"), "last_apply.finished_at", optional=True),
            "request_id": _text(request_id, "last_apply.request_id") if request_id is not None else None}


def _stack_facts(value: object) -> StackFacts:
    if not isinstance(value, dict):
        raise RecordError("stack must be a mapping")
    facts: StackFacts = {
        "slug": _slug(value.get("slug")),
        "ref": _text(value.get("ref"), "stack.ref", required=False),
        "ref_branch": _text(value.get("ref_branch"), "stack.ref_branch", required=False),
        "commit": _commit(value.get("commit"), "stack.commit") or "",
        "template": _text(value.get("template"), "stack.template", required=False, limit=500),
        "ports": _ports(value.get("ports") or {}),
        "created_at": _epoch(value.get("created_at"), "stack.created_at", optional=True),
    }
    if value.get("ttl_expires_at") is not None:
        facts["ttl_expires_at"] = _epoch(value["ttl_expires_at"], "stack.ttl_expires_at")  # type: ignore[typeddict-item]
    return facts


def parse_target_summary(data: object) -> TargetSummary:
    """Validate one summary and return only its allow-listed fields."""
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise RecordError("unknown summary schema")
    routes = data.get("routes")
    if not isinstance(routes, list) or not routes or len(routes) > MAX_ROUTES:
        raise RecordError("routes must list 1 to 64 routes")
    healthcheck = _text(data.get("healthcheck_path") or "/", "healthcheck_path", limit=500)
    if not healthcheck.startswith("/"):
        raise RecordError("healthcheck_path must start with /")
    return {
        "schema": SCHEMA,
        "remote": _text(data.get("remote"), "remote", limit=64),
        "project": _part(data.get("project"), "project"),
        "environment": _environment(data.get("environment")),
        "compose_project": _text(data.get("compose_project"), "compose_project"),
        "loopback_port": _port(data.get("loopback_port"), "loopback_port", optional=True),
        "healthcheck_path": healthcheck,
        "routes": [_route(route) for route in routes],
        "commit": _commit(data.get("commit"), "commit"),
        "last_apply": _last_apply(data.get("last_apply")),
        "stack": _stack_facts(data["stack"]) if data.get("stack") is not None else None,
    }


def parse_site_link(data: object) -> SiteLink:
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise RecordError("unknown site link schema")
    return {
        "schema": SCHEMA,
        "remote": _text(data.get("remote"), "remote", limit=64),
        "slug": _slug(data.get("slug")),
        "hostname": _hostname(data.get("hostname"), "hostname", wildcard=False),
        "instance": _text(data.get("instance"), "instance", limit=128),
        "label": _text(data.get("label") or "default", "label", limit=128),
        "commit": _commit(data.get("commit"), "commit"),
        "recorded_at": _epoch(data.get("recorded_at"), "recorded_at"),  # type: ignore[typeddict-item]
    }


# --------------------------------------------------------------- builders

def _template_path(template: str, checkout: str | None) -> str:
    """The template path relative to its checkout, never a controller-local absolute path."""
    if not template:
        return ""
    path = Path(template)
    if checkout:
        try:
            return path.resolve().relative_to(Path(checkout).resolve()).as_posix()
        except (OSError, ValueError):
            pass
    return path.name if path.is_absolute() else PurePosixPath(template).as_posix()


def build_stack_facts(stack: dict, stack_record: dict | None = None) -> StackFacts:
    record = stack_record or {}
    facts: dict = {
        "slug": stack.get("slug") or record.get("slug"),
        "ref": stack.get("ref") or record.get("ref") or "",
        "ref_branch": stack.get("ref_branch") or record.get("ref_branch") or "",
        "commit": stack.get("commit") or record.get("commit") or "",
        "template": _template_path(str(stack.get("template") or record.get("template") or ""),
                                   record.get("checkout")),
        "ports": stack.get("ports") or record.get("ports") or {},
        "created_at": record.get("created_at"),
    }
    if record.get("ttl_expires_at") is not None:
        facts["ttl_expires_at"] = record["ttl_expires_at"]
    return _stack_facts(facts)


def build_target_summary(validated: dict, remote_name: str, runtime: dict, *,
                         commit: str | None, last_apply: LastApply,
                         stack_record: dict | None = None) -> TargetSummary:
    """Pick the panel's fields out of a validated manifest and desired runtime.

    Only named fields are read. ``validated["secrets"]``, the derived
    environment and the rendered override or Caddyfile are never touched.
    """
    routes: list[dict] = []
    for route in validated.get("routes") or []:
        item = {"hostname": route.get("hostname"), "mode": route.get("mode"),
                "primary": route.get("primary") is True}
        if route.get("mode") == "redirect":
            item["target"] = route.get("target")
        routes.append(item)
    stack = validated.get("stack")
    raw = {
        "schema": SCHEMA, "remote": remote_name,
        "project": validated.get("project"), "environment": validated.get("environment"),
        "compose_project": runtime.get("compose_project"),
        "loopback_port": runtime.get("loopback_port"),
        "healthcheck_path": (validated.get("healthcheck") or {}).get("path") or "/",
        "routes": routes, "commit": commit, "last_apply": dict(last_apply),
        "stack": build_stack_facts(stack, stack_record) if isinstance(stack, dict) else None,
    }
    return parse_target_summary(raw)


def build_site_link(*, remote: str, slug: str, hostname: str, instance: str, label: str,
                    commit: str | None, recorded_at: int) -> SiteLink:
    return parse_site_link({"schema": SCHEMA, "remote": remote, "slug": slug,
                            "hostname": hostname, "instance": instance, "label": label,
                            "commit": commit, "recorded_at": recorded_at})


def encode_record(record: TargetSummary | SiteLink) -> str:
    return json.dumps(record, indent=2, sort_keys=True) + "\n"


def remote_write_command(path: str, text: str) -> str:
    """A shell command that atomically writes ``text`` to ``path`` with mode 0600."""
    payload = base64.b64encode(text.encode()).decode()
    parent = posixpath.dirname(path)
    template = posixpath.join(parent, ".panel-record.XXXXXX")
    return (f"umask 077; mkdir -p {shlex.quote(parent)} && "
            f"t=$(mktemp {shlex.quote(template)}) && "
            f"printf %s {shlex.quote(payload)} | base64 -d > \"$t\" && "
            f"chmod 0600 \"$t\" && mv -f \"$t\" {shlex.quote(path)}")


def stack_record_for(validated: dict, remote_name: str) -> dict | None:
    """The controller's stack record for a rendered stack target, or None. Never raises."""
    stack = validated.get("stack")
    if not isinstance(stack, dict) or not stack.get("slug"):
        return None
    try:
        from sandbox.hosting import stack_registry
        record = stack_registry.read()["stacks"].get(stack_registry.record_key(remote_name, stack["slug"]))
    except Exception:
        return None
    return record if isinstance(record, dict) else None


class SummaryRecorder:
    """Best-effort writer of one target's summary across an apply.

    ``run_remote`` runs one shell command on the remote. Any failure to write
    is kept in ``errors`` and never reaches the apply.
    """

    def __init__(self, validated: dict, remote_name: str, runtime: dict, *,
                 run_remote: Callable[[str], object], stack_record: dict | None = None,
                 clock: Callable[[], float] = time.time):
        self._validated = validated
        self._remote_name = remote_name
        self._runtime = runtime
        self._run_remote = run_remote
        self._stack_record = stack_record
        self._clock = clock
        self._path: str | None = None
        self._commit: str | None = None
        self._last: LastApply | None = None
        self.errors: list[str] = []

    @property
    def started_apply(self) -> bool:
        return self._last is not None

    def _write(self) -> None:
        try:
            summary = build_target_summary(
                self._validated, self._remote_name, self._runtime, commit=self._commit,
                last_apply=self._last, stack_record=self._stack_record)  # type: ignore[arg-type]
            self._run_remote(remote_write_command(self._path, encode_record(summary)))  # type: ignore[arg-type]
        except Exception as exc:  # the apply must never fail on a panel record
            self.errors.append(type(exc).__name__)

    def started(self, home: str, commit: str | None, request_id: str | None) -> None:
        self._path = summary_path(home, self._validated["project"], self._validated["environment"])
        self._commit = commit
        self._last = {"status": "applying", "started_at": int(self._clock()),
                      "finished_at": None,
                      "request_id": request_id if isinstance(request_id, str) and request_id else None}
        self._write()

    def finished(self, ok: bool) -> None:
        if self._last is None or self._last["status"] != "applying":
            return
        self._last = {**self._last, "status": "succeeded" if ok else "failed",
                      "finished_at": int(self._clock())}
        self._write()

    def run(self, apply: Callable[[], object]):
        try:
            return apply()
        except Exception:
            self.finished(False)
            raise


# ----------------------------------------------------------------- readers

def _read_json(path: Path) -> object:
    """Read a small JSON file without following a symlink at the final component."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            data = handle.read(MAX_RECORD_BYTES + 1)
    finally:
        if fd >= 0:
            os.close(fd)
    if len(data) > MAX_RECORD_BYTES:
        raise RecordError("record is too large")
    return json.loads(data)


def _dirs(path: Path) -> list[Path]:
    try:
        return sorted(child for child in path.iterdir()
                      if child.is_dir() and not child.is_symlink())
    except OSError:
        return []


def read_server_records(runtime_dir: Path) -> ServerRecords:
    """Read every target summary and site link under this host's runtime directory.

    Only files named ``summary.json`` and ``<hostname>.json`` are opened. A
    target directory without a summary is listed as legacy when it holds the
    Compose override an apply writes; nothing else in it is read.
    """
    records: ServerRecords = {"targets": [], "legacy": [], "links": [], "partial_reasons": []}
    for project_dir in _dirs(runtime_dir / "hosts"):
        if not TARGET_PART_RE.fullmatch(project_dir.name):
            continue
        for env_dir in _dirs(project_dir):
            if not _ENV_NAME_RE.fullmatch(env_dir.name):
                continue
            if len(records["targets"]) + len(records["legacy"]) >= MAX_TARGETS:
                records["partial_reasons"].append("hosted_targets_truncated")
                break
            summary = env_dir / SUMMARY_NAME
            label = f"{project_dir.name}/{env_dir.name}"
            if summary.is_file() or summary.is_symlink():
                try:
                    target = parse_target_summary(_read_json(summary))
                    if (target["project"], target["environment"]) != (project_dir.name, env_dir.name):
                        raise RecordError("summary names another target")
                    records["targets"].append(target)
                except (OSError, ValueError) as exc:
                    records["partial_reasons"].append(
                        f"target_summary_unreadable:{label}:{type(exc).__name__}")
            elif (env_dir / "compose.override.yml").exists():
                records["legacy"].append({"project": project_dir.name, "environment": env_dir.name})
    for slug_dir in _dirs(runtime_dir / SITES_DIR):
        if not SLUG_RE.fullmatch(slug_dir.name):
            continue
        try:
            files = sorted(slug_dir.glob("*.json"))
        except OSError:
            files = []
        for path in files:
            if len(records["links"]) >= MAX_SITE_LINKS:
                records["partial_reasons"].append("site_links_truncated")
                return records
            try:
                link = parse_site_link(_read_json(path))
                if link["slug"] != slug_dir.name or f"{link['hostname']}.json" != path.name:
                    raise RecordError("site link names another site")
                records["links"].append(link)
            except (OSError, ValueError) as exc:
                records["partial_reasons"].append(
                    f"site_link_unreadable:{slug_dir.name}/{path.name}:{type(exc).__name__}")
    return records
