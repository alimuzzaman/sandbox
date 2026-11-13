"""Authoritative lifetime records for hosted targets, kept on the remote (spec 077).

One JSON file per Compose project under ``$SANDBOX_HOME/runtime/target-lifetimes``.
Every write happens under ``.lock`` (flock) as a temp file plus ``os.replace`` and
appends one line to ``audit.jsonl``.

This module is shipped verbatim over SSH and run with the remote's system
``python3 -c``, so it imports only the standard library, uses Python 3.8 syntax,
and reads its one request from stdin. The controller and the host-side reaper
import it, so the claim, validation and eligibility rules exist exactly once.

A missing, unreadable or invalid record never makes a target eligible for reaping.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 1
STORE_RELATIVE = ("runtime", "target-lifetimes")
LIFETIMES = ("temporary", "hosted", "permanent")
SOURCES = ("default", "flag", "classified")
STATES = ("active", "reaping", "reaped")
KINDS = ("stack", "target")
REAP_REASONS = ("expired", "merged")
DNS_MODES = ("wildcard", "exact", "unknown")
DEFAULT_TTL_SECONDS = 14 * 86400
TTL_MIN_SECONDS = 3600
TTL_MAX_SECONDS = 90 * 86400
DEFAULT_POLICY = {"per_owner": 5, "total": 40, "min_free_bytes": 20 * 1024 ** 3}
DOCKER_ROOT = "/var/lib/docker"

# Kept in lockstep with sandbox.hosting.names (parity-tested).
TARGET_PART_RE = re.compile(r"[a-z0-9][a-z0-9-]*\Z")
_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}\Z")
_COPY_RE = re.compile(r"[0-9a-f]{8}\Z")
_CONTROLLER_RE = re.compile(r"[0-9a-f]{32}\Z")
_COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
_TTL_RE = re.compile(r"([1-9][0-9]{0,4})([hdw])\Z")
_TTL_UNITS = {"h": 3600, "d": 86400, "w": 7 * 86400}
_FILE_SUFFIX = ".json"
# Store.peek bounds: a viewer (the `sb web` panel) reads at most this much.
PEEK_MAX_FILES = 500
PEEK_MAX_BYTES = 256 * 1024

# Refusal codes (the controller maps them one to one).
SLUG_TAKEN = "stack_slug_taken"
PROJECT_TAKEN = "stack_project_taken"
HOSTED_TAKEN = "stack_hosted_taken"
LIFETIME_INVALID = "stack_lifetime_invalid"
TTL_INVALID = "stack_ttl_invalid"
EXPIRED = "stack_expired"
REAPING = "stack_reaping"
NOT_OWNER = "stack_not_owner"
LIMIT_REACHED = "stack_limit_reached"
RECORD_INVALID = "stack_record_invalid"
NOT_FOUND = "stack_not_found"
TARGET_NOT_FOUND = "target_not_found"
LIMITS_INVALID = "stack_limits_invalid"
PORT_UNAVAILABLE = "stack_port_unavailable"
HOSTNAME_OWNED = "hostname_owned_by_other_project"
REQUEST_INVALID = "stack_record_unavailable"


class StoreRefusal(Exception):
    def __init__(self, code, message, holder=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.holder = holder


# ------------------------------------------------------------------ time

def to_rfc3339(epoch):
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_rfc3339(value):
    """Epoch seconds for ``YYYY-MM-DDTHH:MM:SSZ``; ``None`` when malformed."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return int(parsed.replace(tzinfo=timezone.utc).timestamp())


def parse_ttl(value):
    """Seconds for ``<n>h|<n>d|<n>w`` within 1 hour to 90 days."""
    match = _TTL_RE.fullmatch(str(value or "").strip().lower())
    if not match:
        raise StoreRefusal(TTL_INVALID, "--ttl must look like 36h, 14d or 2w")
    seconds = int(match.group(1)) * _TTL_UNITS[match.group(2)]
    if not TTL_MIN_SECONDS <= seconds <= TTL_MAX_SECONDS:
        raise StoreRefusal(TTL_INVALID, "--ttl must be between 1h and 90d")
    return seconds


def compose_project_for(project, environment):
    return "sandbox-host-%s-%s" % (project, environment)


# ------------------------------------------------------------------ types

@dataclass(frozen=True)
class Owner:
    label: str
    controller: str

    def same_person(self, other):
        return isinstance(other, Owner) and self.label.lower() == other.label.lower()


@dataclass(frozen=True)
class StackIdentity:
    slug: str
    copy_id: str
    repo: str
    ref: str
    ref_branch: str
    commit: str
    dev_branch: str
    canonical: bool
    template: str
    ports: dict = field(default_factory=dict)


@dataclass(frozen=True)
class MergeEvidence:
    merged: bool
    commit: str
    dev_branch: str
    dev_commit: str
    checked_at: str
    controller: str


@dataclass(frozen=True)
class ReapInfo:
    run_id: str
    started_at: str
    reason: str
    finished_at: str = None
    dns_left: bool = False


@dataclass(frozen=True)
class TargetRecord:
    compose_project: str
    project: str
    environment: str
    kind: str
    lifetime: str
    lifetime_source: str
    ttl_seconds: int
    expires_at: str
    state: str
    owner: Owner
    created_at: str
    updated_at: str
    front_door: str = "nginx"
    dns_mode: str = "unknown"
    hostnames: tuple = ()
    stack: StackIdentity = None      # kind "stack" only
    commit: str = None               # kind "target": known deployed commit, or None
    repo: str = None                 # kind "target": known repo, or None
    extended_count: int = 0
    classified_by: Owner = None
    classified_at: str = None
    merge: MergeEvidence = None
    reap_run: ReapInfo = None
    overrides: tuple = ()
    extra: dict = field(default_factory=dict)

    @property
    def slug(self):
        return self.stack.slug if self.stack else None

    @property
    def record_commit(self):
        return self.stack.commit if self.stack else self.commit

    @property
    def record_repo(self):
        return self.stack.repo if self.stack else self.repo


@dataclass(frozen=True)
class Invalid:
    name: str
    reason: str


@dataclass(frozen=True)
class Policy:
    per_owner: int
    total: int
    min_free_bytes: int


@dataclass(frozen=True)
class Eligibility:
    eligible: bool
    reason: str


_STACK_KEYS = ("slug", "copy_id", "ref", "ref_branch", "dev_branch", "template")
_KNOWN_KEYS = frozenset((
    "schema_version", "compose_project", "project", "environment", "kind", "lifetime",
    "lifetime_source", "ttl_seconds", "expires_at", "extended_count", "state", "owner",
    "created_at", "updated_at", "classified_by", "classified_at", "front_door", "dns_mode",
    "hostnames", "slug", "copy_id", "repo", "ref", "ref_branch", "commit", "dev_branch",
    "canonical", "template", "ports", "merge", "reap", "reap_run", "overrides"))


# ------------------------------------------------------------------ parse

def _owner(value, name="owner"):
    if not isinstance(value, dict):
        raise ValueError("%s must be an object" % name)
    label, controller = value.get("label"), value.get("controller")
    if not isinstance(label, str) or not label.strip() or len(label) > 200:
        raise ValueError("%s.label must be a non-empty string" % name)
    if "@" in label:
        # Labels are shown to everyone on the panel; an email is personal data.
        raise ValueError("%s.label must be a name, not an email address" % name)
    if not isinstance(controller, str) or not _CONTROLLER_RE.fullmatch(controller):
        raise ValueError("%s.controller must be 32 lowercase hex characters" % name)
    return Owner(label.strip(), controller)


def _ports(value):
    ports = value if value is not None else {}
    if not isinstance(ports, dict) or not all(
            isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool)
            for k, v in ports.items()):
        raise ValueError("ports must map names to integers")
    return dict(ports)


def _stack(data):
    text = {}
    for key in _STACK_KEYS + ("repo", "commit"):
        item = data.get(key)
        if not isinstance(item, str):
            raise ValueError("a stack record needs a string %s" % key)
        text[key] = item
    if not _SLUG_RE.fullmatch(text["slug"]):
        raise ValueError("slug is not a safe slug")
    if not _COPY_RE.fullmatch(text["copy_id"]):
        raise ValueError("copy_id must be 8 lowercase hex characters")
    if not _COMMIT_RE.fullmatch(text["commit"]):
        raise ValueError("commit must be a full commit")
    if not text["dev_branch"] or not text["repo"]:
        raise ValueError("a stack record needs repo and dev_branch")
    canonical = data.get("canonical")
    if not isinstance(canonical, bool):
        raise ValueError("canonical must be a boolean")
    return StackIdentity(canonical=canonical, ports=_ports(data.get("ports")), **text)


def _target_identity(data):
    """A classified target: stack identity is null; commit and repo when known."""
    for key in _STACK_KEYS:
        if data.get(key) is not None:
            raise ValueError("a target record has no %s" % key)
    if data.get("canonical") not in (None, False):
        raise ValueError("only a stack is canonical")
    if _ports(data.get("ports")):
        raise ValueError("a target record has no stack ports")
    commit, repo = data.get("commit"), data.get("repo")
    if commit is not None and not (isinstance(commit, str) and _COMMIT_RE.fullmatch(commit)):
        raise ValueError("commit must be a full commit or null")
    if repo is not None and (not isinstance(repo, str) or not repo or len(repo) > 300):
        raise ValueError("repo must be a string or null")
    return commit, repo


def _merge(value):
    if value is None:
        return None
    if not isinstance(value, dict) or not isinstance(value.get("merged"), bool):
        raise ValueError("merge must be an object with a boolean merged")
    for key in ("commit", "dev_commit"):
        if not _COMMIT_RE.fullmatch(str(value.get(key) or "")):
            raise ValueError("merge.%s must be a full commit" % key)
    if parse_rfc3339(value.get("checked_at")) is None:
        raise ValueError("merge.checked_at must be RFC3339")
    return MergeEvidence(value["merged"], value["commit"], str(value.get("dev_branch") or ""),
                         value["dev_commit"], value["checked_at"], str(value.get("controller") or ""))


def _reap_run(value):
    if value is None:
        return None
    if not isinstance(value, dict) or not isinstance(value.get("run_id"), str) or not value["run_id"]:
        raise ValueError("reap_run.run_id must be a string")
    if value.get("reason") not in REAP_REASONS:
        raise ValueError("reap_run.reason is unknown")
    if parse_rfc3339(value.get("started_at")) is None:
        raise ValueError("reap_run.started_at must be RFC3339")
    finished = value.get("finished_at")
    if finished is not None and parse_rfc3339(finished) is None:
        raise ValueError("reap_run.finished_at must be RFC3339")
    return ReapInfo(value["run_id"], value["started_at"], value["reason"], finished,
                    bool(value.get("dns_left")))


def parse_record(data, name=None):
    """Return a ``TargetRecord``, or ``Invalid`` naming the first rule it breaks."""
    label = name or (data.get("compose_project") if isinstance(data, dict) else None) or "?"
    try:
        return _parse(data, name)
    except (ValueError, TypeError, KeyError) as exc:
        return Invalid(str(label), str(exc)[:200])


def _parse(data, name):
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA:
        raise ValueError("unknown record schema_version")
    project, environment = data.get("project"), data.get("environment")
    if not isinstance(project, str) or not TARGET_PART_RE.fullmatch(project):
        raise ValueError("project is not a valid target part")
    if not isinstance(environment, str) or not TARGET_PART_RE.fullmatch(environment):
        raise ValueError("environment is not a valid target part")
    compose_project = data.get("compose_project")
    if compose_project != compose_project_for(project, environment):
        raise ValueError("compose_project does not match project and environment")
    if name is not None and name != compose_project + _FILE_SUFFIX:
        raise ValueError("file name does not match compose_project")
    kind, lifetime, source, state = (data.get(key) for key in ("kind", "lifetime", "lifetime_source", "state"))
    if kind not in KINDS:
        raise ValueError("kind is unknown")
    if lifetime not in LIFETIMES:
        raise ValueError("lifetime is unknown")
    if source not in SOURCES:
        raise ValueError("lifetime_source is unknown")
    if state not in STATES:
        raise ValueError("state is unknown")
    ttl, expires = data.get("ttl_seconds"), data.get("expires_at")
    if lifetime == "temporary":
        if not isinstance(ttl, int) or isinstance(ttl, bool) or not TTL_MIN_SECONDS <= ttl <= TTL_MAX_SECONDS:
            raise ValueError("a temporary record needs ttl_seconds between 1h and 90d")
        if parse_rfc3339(expires) is None:
            raise ValueError("a temporary record needs an RFC3339 expires_at")
    elif ttl is not None or expires is not None:
        raise ValueError("only a temporary record has ttl_seconds and expires_at")
    for key in ("created_at", "updated_at"):
        if parse_rfc3339(data.get(key)) is None:
            raise ValueError("%s must be RFC3339" % key)
    extended = data.get("extended_count", 0)
    if not isinstance(extended, int) or isinstance(extended, bool) or extended < 0:
        raise ValueError("extended_count must be a non-negative integer")
    classified_by = data.get("classified_by")
    classified_at = data.get("classified_at")
    if source == "classified":
        classified_by = _owner(classified_by, "classified_by")
        if parse_rfc3339(classified_at) is None:
            raise ValueError("a classified record needs an RFC3339 classified_at")
    elif classified_by is not None or classified_at is not None:
        raise ValueError("only a classified record has classified_by and classified_at")
    stack, commit, repo = None, None, None
    if kind == "stack":
        stack = _stack(data)
        if source == "classified":
            raise ValueError("a stack's lifetime comes from stack up or stack ttl, not classify")
    else:
        commit, repo = _target_identity(data)
    if lifetime == "hosted" and stack is not None and not (
            stack.canonical and stack.ref_branch == stack.dev_branch):
        raise ValueError("a hosted stack must be the canonical stack of its dev branch")
    if stack is not None and stack.canonical and lifetime != "hosted":
        raise ValueError("only a hosted stack is canonical")
    reap_run = _reap_run(data.get("reap_run"))
    if state in ("reaping", "reaped") and reap_run is None:
        raise ValueError("a reaping or reaped record needs reap_run")
    hostnames = data.get("hostnames") or []
    if not isinstance(hostnames, list) or not all(isinstance(item, str) for item in hostnames):
        raise ValueError("hostnames must be a list of strings")
    front_door = data.get("front_door") or "nginx"
    if front_door not in ("nginx", "caddy"):
        raise ValueError("front_door is unknown")
    dns_mode = data.get("dns_mode") or "unknown"
    if dns_mode not in DNS_MODES:
        raise ValueError("dns_mode is unknown")
    overrides = data.get("overrides") or []
    if not isinstance(overrides, list) or not all(isinstance(item, dict) for item in overrides):
        raise ValueError("overrides must be a list of objects")
    extra = {key: value for key, value in data.items() if key not in _KNOWN_KEYS}
    return TargetRecord(
        compose_project=compose_project, project=project, environment=environment, kind=kind,
        lifetime=lifetime, lifetime_source=source, ttl_seconds=ttl, expires_at=expires,
        state=state, owner=_owner(data.get("owner")), created_at=data["created_at"],
        updated_at=data["updated_at"], front_door=front_door, dns_mode=dns_mode,
        hostnames=tuple(sorted(set(item.strip().rstrip(".").lower() for item in hostnames if item.strip()))),
        stack=stack, commit=commit, repo=repo, extended_count=extended,
        classified_by=classified_by, classified_at=classified_at,
        merge=_merge(data.get("merge")), reap_run=reap_run, overrides=tuple(overrides), extra=extra)


def _owner_dict(owner):
    return None if owner is None else {"label": owner.label, "controller": owner.controller}


def reap_view(record, now):
    """The panel's ``reap`` block: eligibility at ``now`` plus merge evidence."""
    verdict = eligibility(record, now)
    merge = record.merge
    merged = merge is not None and merge.merged and merge.commit == record.record_commit
    return {"eligible": verdict.eligible,
            "reason": verdict.reason if verdict.reason in REAP_REASONS and verdict.eligible else None,
            "merged_into": merge.dev_branch if merged else None,
            "merged_at": merge.checked_at if merged else None}


def record_to_dict(record, now=None):
    """The flat record shape agreed with the panel (``schema_version`` 1).

    ``reap`` is computed at ``now`` (default: the record's ``updated_at``); readers
    that need the current answer recompute it, as ``view`` does.
    """
    stack = record.stack
    data = dict(record.extra)
    data.update({
        "schema_version": SCHEMA, "compose_project": record.compose_project,
        "project": record.project, "environment": record.environment, "kind": record.kind,
        "lifetime": record.lifetime, "lifetime_source": record.lifetime_source,
        "ttl_seconds": record.ttl_seconds, "expires_at": record.expires_at,
        "extended_count": record.extended_count, "state": record.state,
        "owner": _owner_dict(record.owner), "created_at": record.created_at,
        "updated_at": record.updated_at, "classified_by": _owner_dict(record.classified_by),
        "classified_at": record.classified_at, "front_door": record.front_door,
        "dns_mode": record.dns_mode, "hostnames": list(record.hostnames),
        "slug": stack.slug if stack else None, "copy_id": stack.copy_id if stack else None,
        "repo": record.record_repo, "ref": stack.ref if stack else None,
        "ref_branch": stack.ref_branch if stack else None, "commit": record.record_commit,
        "dev_branch": stack.dev_branch if stack else None,
        "canonical": bool(stack.canonical) if stack else False,
        "template": stack.template if stack else None,
        "ports": dict(stack.ports) if stack else {},
        "merge": None if record.merge is None else {
            "merged": record.merge.merged, "commit": record.merge.commit,
            "dev_branch": record.merge.dev_branch, "dev_commit": record.merge.dev_commit,
            "checked_at": record.merge.checked_at, "controller": record.merge.controller},
        "reap_run": None if record.reap_run is None else {
            "run_id": record.reap_run.run_id, "started_at": record.reap_run.started_at,
            "reason": record.reap_run.reason, "finished_at": record.reap_run.finished_at,
            "dns_left": record.reap_run.dns_left},
        "overrides": [dict(item) for item in record.overrides],
    })
    data["reap"] = reap_view(record, parse_rfc3339(record.updated_at) if now is None else now)
    return data


def parse_policy(data):
    data = data if isinstance(data, dict) else {}
    values = {}
    for key, default in DEFAULT_POLICY.items():
        value = data.get(key, default)
        if not isinstance(value, int) or isinstance(value, bool) or value < (0 if key == "min_free_bytes" else 1):
            raise StoreRefusal(LIMITS_INVALID, "%s must be a positive integer" % key)
        values[key] = value
    return Policy(**values)


# --------------------------------------------------------------- decisions

def eligibility(record, now):
    """Whether server-side cleanup may tear the target down at epoch ``now``."""
    if not isinstance(record, TargetRecord):
        return Eligibility(False, "invalid")
    if record.state == "reaped":
        return Eligibility(False, "reaped")
    if record.lifetime != "temporary":
        return Eligibility(False, record.lifetime)
    if record.state == "reaping" and record.reap_run is not None:
        return Eligibility(True, record.reap_run.reason)
    merge, stack = record.merge, record.stack
    if merge is not None and merge.merged and stack is not None and merge.commit == stack.commit:
        return Eligibility(True, "merged")
    expires = parse_rfc3339(record.expires_at)
    if expires is not None and now >= expires:
        return Eligibility(True, "expired")
    return Eligibility(False, "not_expired")


def _holder(record):
    stack = record.stack
    return {"compose_project": record.compose_project, "owner": record.owner.label,
            "slug": stack.slug if stack else None, "copy_id": stack.copy_id if stack else None,
            "kind": record.kind, "state": record.state}


def _active(record):
    return isinstance(record, TargetRecord) and record.state != "reaped"


@dataclass(frozen=True)
class ClaimDecision:
    stored: TargetRecord
    is_new: bool


def decide_claim(records, invalid, deployed, draft, *, caller, explicit_copy=False,
                 legacy=False, lifetime_explicit=False, new_ttl=False, now, policy,
                 free_bytes):
    """Decide one ``stack up`` claim; raise ``StoreRefusal`` or return what to store.

    ``records`` maps compose project to valid records, ``invalid`` lists the
    compose projects whose files are invalid, ``deployed`` the compose projects with
    a runtime directory on the host.
    """
    if not isinstance(draft, TargetRecord) or draft.kind != "stack" or draft.stack is None:
        raise StoreRefusal(REQUEST_INVALID, "the claim does not describe a stack")
    if draft.compose_project in invalid:
        raise StoreRefusal(RECORD_INVALID, "the record for %s is invalid; repair or release it first"
                           % draft.compose_project)
    slug, copy_id = draft.stack.slug, draft.stack.copy_id
    holders = {}
    same = records.get(draft.compose_project)
    if same is not None:
        holders[same.compose_project] = same
    for record in records.values():
        if record.stack is not None and record.stack.slug == slug:
            holders[record.compose_project] = record
    if len(holders) > 1:
        names = ", ".join(sorted(holders))
        raise StoreRefusal(SLUG_TAKEN, "stack %s and its project are held by different records (%s)"
                           % (slug, names), [_holder(item) for item in holders.values()])
    existing = next(iter(holders.values()), None)
    if existing is not None:
        if existing.kind != "stack":
            raise StoreRefusal(PROJECT_TAKEN, "project %s is a classified hosted target, not a stack"
                               % existing.compose_project, _holder(existing))
        if existing.stack.copy_id != copy_id:
            code = SLUG_TAKEN if existing.stack.slug == slug else PROJECT_TAKEN
            raise StoreRefusal(code, "stack %s is held on this remote by %s (copy %s); pick another "
                               "--name, or the owner names the copy with --copy"
                               % (existing.stack.slug, existing.owner.label, existing.stack.copy_id),
                               _holder(existing))
        if explicit_copy and not existing.owner.same_person(caller):
            raise StoreRefusal(NOT_OWNER, "copy %s of stack %s belongs to %s"
                               % (copy_id, existing.stack.slug, existing.owner.label), _holder(existing))
        if existing.owner.controller != caller.controller and not explicit_copy:
            # Same copy id from another controller without naming it: a copied registry.
            raise StoreRefusal(SLUG_TAKEN, "stack %s copy %s was created on another controller; "
                               "pass --copy %s to target it" % (slug, copy_id, copy_id), _holder(existing))
        if existing.state == "reaping":
            raise StoreRefusal(REAPING, "stack %s is being torn down by server cleanup" % slug,
                               _holder(existing))
        if (existing.state == "active" and existing.lifetime == "temporary" and not new_ttl
                and eligibility(existing, now).reason == "expired"):
            raise StoreRefusal(EXPIRED, "stack %s expired at %s; give --ttl to extend it before "
                               "re-running" % (slug, existing.expires_at), _holder(existing))
    elif draft.compose_project in deployed and not legacy:
        raise StoreRefusal(PROJECT_TAKEN, "project %s is deployed on this remote with no lifetime "
                           "record; it is not this controller's stack" % draft.compose_project)
    fresh = existing is None or existing.state == "reaped"
    lifetime, source, ttl, expires = draft.lifetime, draft.lifetime_source, draft.ttl_seconds, draft.expires_at
    if not fresh:
        if not lifetime_explicit:
            # A re-run keeps what the copy already has.
            lifetime, source = existing.lifetime, existing.lifetime_source
        if lifetime == "temporary":
            if new_ttl or existing.lifetime != "temporary":
                ttl, expires = draft.ttl_seconds or DEFAULT_TTL_SECONDS, None
            else:
                ttl, expires = existing.ttl_seconds, existing.expires_at
        else:
            ttl, expires = None, None
    if lifetime == "temporary" and expires is None:
        ttl = ttl or DEFAULT_TTL_SECONDS
        expires = to_rfc3339(now + ttl)
    canonical = lifetime == "hosted"
    if canonical and draft.stack.ref_branch != draft.stack.dev_branch:
        raise StoreRefusal(LIFETIME_INVALID, "stack %s is the hosted stack of %s; it can only run "
                           "the dev branch %s (give --lifetime temporary to change that)"
                           % (slug, draft.stack.repo, draft.stack.dev_branch))
    if canonical:
        for record in records.values():
            if (record.compose_project != draft.compose_project and _active(record)
                    and record.stack is not None and record.stack.canonical
                    and record.stack.repo == draft.stack.repo
                    and record.stack.dev_branch == draft.stack.dev_branch):
                raise StoreRefusal(HOSTED_TAKEN, "the canonical hosted stack of %s %s on this remote is %s"
                                   % (draft.stack.repo, draft.stack.dev_branch, record.stack.slug),
                                   _holder(record))
    others = [record for record in records.values()
              if record.compose_project != draft.compose_project and _active(record)]
    taken_ports = {}
    for record in others:
        for port in (record.stack.ports.values() if record.stack else ()):
            taken_ports[port] = record
    for name, port in sorted(draft.stack.ports.items()):
        if port in taken_ports:
            holder = taken_ports[port]
            raise StoreRefusal(PORT_UNAVAILABLE, "port %s=%d is held by %s on this remote"
                               % (name, port, holder.stack.slug if holder.stack else holder.compose_project),
                               _holder(holder))
    for record in others:
        shared = sorted(set(draft.hostnames) & set(record.hostnames))
        if shared:
            raise StoreRefusal(HOSTNAME_OWNED, "hostname %s is held by %s on this remote"
                               % (shared[0], record.compose_project), _holder(record))
    if fresh:
        check_limits(list(records.values()), policy, free_bytes, caller, temporary=lifetime == "temporary")
    stamp = to_rfc3339(now)
    stack = replace(draft.stack, canonical=canonical)
    # Every claim starts a delivery, so earlier merge evidence never carries over:
    # a re-run must not be reaped while its apply is still running.
    merge = None
    base = existing if existing is not None and not fresh else None
    owner = draft.owner if base is None else Owner(base.owner.label, caller.controller)
    stored = replace(
        draft, lifetime=lifetime, lifetime_source=source, ttl_seconds=ttl if lifetime == "temporary" else None,
        expires_at=expires if lifetime == "temporary" else None, state="active", owner=owner,
        created_at=base.created_at if base else stamp, updated_at=stamp, stack=stack, merge=merge,
        reap_run=None, overrides=base.overrides if base else (),
        extended_count=base.extended_count if base else 0,
        extra=dict(base.extra) if base else dict(draft.extra))
    if isinstance(parse_record(record_to_dict(stored)), Invalid):
        raise StoreRefusal(LIFETIME_INVALID, "the stack's lifetime is not valid: "
                           + parse_record(record_to_dict(stored)).reason)
    return ClaimDecision(stored, fresh)


def check_limits(records, policy, free_bytes, caller, *, temporary):
    live = [record for record in records if _active(record)]
    if len(live) >= policy.total:
        raise StoreRefusal(LIMIT_REACHED, "this remote holds %d targets, its limit is %d; tear a stack "
                           "down first (sb host stack list --remote ...)" % (len(live), policy.total))
    if temporary:
        mine = [record for record in live if record.kind == "stack" and record.lifetime == "temporary"
                and record.owner.same_person(caller)]
        if len(mine) >= policy.per_owner:
            names = ", ".join(sorted(record.stack.slug for record in mine))
            raise StoreRefusal(LIMIT_REACHED, "%s already runs %d temporary stacks here (%s), the limit "
                               "is %d" % (caller.label, len(mine), names, policy.per_owner))
    if free_bytes is not None and free_bytes < policy.min_free_bytes:
        raise StoreRefusal(LIMIT_REACHED, "this remote has %.1f GiB free, the minimum for a new stack "
                           "is %.1f GiB" % (free_bytes / 1024 ** 3, policy.min_free_bytes / 1024 ** 3))


def _owned_or_forced(record, caller, force_owner, action, now):
    if record.owner.same_person(caller):
        return record
    if not force_owner:
        raise StoreRefusal(NOT_OWNER, "%s belongs to %s; pass --force-owner to %s it anyway"
                           % (record.stack.slug if record.stack else record.compose_project,
                              record.owner.label, action), _holder(record))
    entry = {"action": action, "by": {"label": caller.label, "controller": caller.controller},
             "at": to_rfc3339(now), "owner": record.owner.label}
    return replace(record, overrides=tuple(record.overrides) + (entry,))


# ------------------------------------------------------------------- store

class Store:
    """The record directory on one host; every mutation holds ``.lock``."""

    def __init__(self, home, *, free_paths=None):
        self.home = Path(home)
        self.root = self.home.joinpath(*STORE_RELATIVE)
        self.free_paths = tuple(free_paths) if free_paths is not None else (str(self.home), DOCKER_ROOT)

    @contextmanager
    def lock(self, blocking=True):
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(str(self.root), 0o700)
        handle = open(str(self.root / ".lock"), "a+")
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def path(self, compose_project):
        if not compose_project.startswith("sandbox-host-") or "/" in compose_project:
            raise StoreRefusal(REQUEST_INVALID, "not a hosted compose project: %r" % compose_project)
        return self.root / (compose_project + _FILE_SUFFIX)

    def load(self):
        """``(records by compose project, invalid compose projects -> reason)``."""
        records, invalid = {}, {}
        try:
            children = sorted(self.root.iterdir())
        except OSError:
            return records, invalid
        for child in children:
            if not child.name.endswith(_FILE_SUFFIX) or not child.name.startswith("sandbox-host-"):
                continue
            key = child.name[:-len(_FILE_SUFFIX)]
            try:
                data = json.loads(child.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError) as exc:
                invalid[key] = "unreadable: %s" % type(exc).__name__
                continue
            parsed = parse_record(data, child.name)
            if isinstance(parsed, Invalid):
                invalid[key] = parsed.reason
            else:
                records[key] = parsed
        return records, invalid

    def peek(self, now, max_files=PEEK_MAX_FILES, max_bytes=PEEK_MAX_BYTES):
        """Read-only listing for viewers such as the `sb web` panel.

        Takes no lock and writes nothing (no ``.lock``, no chmod, no mkdir).
        Skips nothing silently: a symlink, a non-regular file, an oversized,
        partly written or invalid record is listed under ``invalid``, so a
        viewer shows it as protected. A file replaced while it is read may
        show as invalid for one call; it is never reported as reapable.
        Returns ``{"records": [view...], "invalid": [{compose_project, reason}],
        "truncated": bool}``.
        """
        records, invalid, truncated = [], [], False
        try:
            with os.scandir(str(self.root)) as scan:
                entries = sorted(scan, key=lambda entry: entry.name)
        except OSError:
            return {"records": records, "invalid": invalid, "truncated": truncated}
        seen = 0
        for entry in entries:
            name = entry.name
            if not name.startswith("sandbox-host-") or not name.endswith(_FILE_SUFFIX):
                continue
            if seen >= max_files:
                truncated = True
                break
            seen += 1
            key = name[:-len(_FILE_SUFFIX)]
            try:
                if not entry.is_file(follow_symlinks=False):
                    raise ValueError("not a regular file")
                fd = os.open(entry.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
                try:
                    data = b""
                    while len(data) <= max_bytes:
                        chunk = os.read(fd, max_bytes + 1 - len(data))
                        if not chunk:
                            break
                        data += chunk
                finally:
                    os.close(fd)
                if len(data) > max_bytes:
                    raise ValueError("record is too large")
                parsed = parse_record(json.loads(data.decode("utf-8")), name)
            except (OSError, UnicodeError, ValueError) as exc:
                invalid.append({"compose_project": key, "reason": ("unreadable: %s" % exc)[:200]})
                continue
            if isinstance(parsed, Invalid):
                invalid.append({"compose_project": key, "reason": parsed.reason})
            else:
                records.append(view(parsed, now))
        return {"records": records, "invalid": invalid, "truncated": truncated}

    def write(self, record):
        target = self.path(record.compose_project)
        fd, temporary = tempfile.mkstemp(prefix=".record-", suffix=".json", dir=str(self.root))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(record_to_dict(record), handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, str(target))
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        self._sync_dir()

    def _sync_dir(self):
        """Make the rename durable; readers never see a partly written record."""
        try:
            descriptor = os.open(str(self.root), os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)

    def delete(self, compose_project):
        try:
            self.path(compose_project).unlink()
            return True
        except FileNotFoundError:
            return False

    def set_aside(self, compose_project, now):
        """Keep an invalid record's bytes under a name ``load`` ignores; return that name."""
        source = self.path(compose_project)
        base = "%s.invalid-%d" % (source.name, int(now))
        for attempt in range(1000):
            kept = source.with_name(base if attempt == 0 else "%s-%d" % (base, attempt))
            try:
                os.link(str(source), str(kept))  # never replaces an earlier kept file
            except FileExistsError:
                continue
            except FileNotFoundError:
                return None
            os.unlink(str(source))
            self._sync_dir()
            return kept.name
        raise StoreRefusal(REQUEST_INVALID, "too many kept copies of %s" % source.name)

    def audit(self, op, compose_project, caller, now, **detail):
        line = {"op": op, "compose_project": compose_project, "at": to_rfc3339(now),
                "by": None if caller is None else {"label": caller.label, "controller": caller.controller}}
        line.update(detail)
        descriptor = os.open(str(self.root / "audit.jsonl"), os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        try:
            os.write(descriptor, (json.dumps(line, sort_keys=True) + "\n").encode("utf-8"))
        finally:
            os.close(descriptor)

    def policy(self):
        try:
            data = json.loads((self.root / "policy.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {}
        except (OSError, UnicodeError, ValueError):
            raise StoreRefusal(LIMITS_INVALID, "policy.json on this remote is unreadable")
        return parse_policy(data)

    def set_policy(self, policy):
        body = {"schema_version": SCHEMA, "per_owner": policy.per_owner, "total": policy.total,
                "min_free_bytes": policy.min_free_bytes}
        fd, temporary = tempfile.mkstemp(prefix=".policy-", dir=str(self.root))
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(body, handle, sort_keys=True)
            handle.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, str(self.root / "policy.json"))

    def deployed(self):
        """Compose projects with a runtime directory under ``runtime/hosts/<p>/<e>``."""
        found = set()
        hosts = self.home / "runtime" / "hosts"
        try:
            projects = sorted(hosts.iterdir())
        except OSError:
            return found
        for project in projects:
            if not project.is_dir() or not TARGET_PART_RE.fullmatch(project.name):
                continue
            try:
                environments = sorted(project.iterdir())
            except OSError:
                continue
            for environment in environments:
                if environment.is_dir() and TARGET_PART_RE.fullmatch(environment.name):
                    found.add(compose_project_for(project.name, environment.name))
        return found

    def free_bytes(self):
        values = []
        for path in self.free_paths:
            try:
                stats = os.statvfs(path)
            except OSError:
                continue
            values.append(stats.f_bavail * stats.f_frsize)
        return min(values) if values else None


# --------------------------------------------------------------------- ops

def _caller(request):
    try:
        return _owner(request.get("caller"))
    except ValueError as exc:
        raise StoreRefusal(REQUEST_INVALID, "caller: %s" % exc)


def _now(request):
    now = parse_rfc3339(request.get("now"))
    if now is None:
        raise StoreRefusal(REQUEST_INVALID, "now must be RFC3339")
    return now


def view(record, now):
    """The record as the controller and the panel see it, ``reap`` as of ``now``."""
    return record_to_dict(record, now)


def _list(store, request, now):
    records, invalid = store.load()
    deployed = store.deployed()
    return {"records": [view(records[key], now) for key in sorted(records)],
            "invalid": [{"compose_project": key, "reason": invalid[key]} for key in sorted(invalid)],
            "unclassified": sorted(deployed - set(records) - set(invalid)),
            "policy": record_to_policy(store.policy()), "free_bytes": store.free_bytes()}


def record_to_policy(policy):
    return {"per_owner": policy.per_owner, "total": policy.total, "min_free_bytes": policy.min_free_bytes}


def _claim(store, request, now, *, write):
    caller = _caller(request)
    draft = parse_record(request.get("record"))
    if isinstance(draft, Invalid):
        raise StoreRefusal(LIFETIME_INVALID, "the stack's record is not valid: " + draft.reason)
    records, invalid = store.load()
    decision = decide_claim(
        records, set(invalid), store.deployed(), draft, caller=caller,
        explicit_copy=bool(request.get("explicit_copy")), legacy=bool(request.get("legacy")),
        lifetime_explicit=bool(request.get("lifetime_explicit")), new_ttl=bool(request.get("new_ttl")),
        now=now, policy=store.policy(), free_bytes=store.free_bytes())
    if write:
        store.write(decision.stored)
        store.audit("claim", decision.stored.compose_project, caller, now,
                    slug=decision.stored.stack.slug, copy_id=decision.stored.stack.copy_id,
                    commit=decision.stored.stack.commit, new=decision.is_new)
    return {"record": view(decision.stored, now), "new": decision.is_new}


def _existing(store, compose_project):
    records, invalid = store.load()
    if compose_project in invalid:
        raise StoreRefusal(RECORD_INVALID, "the record for %s is invalid: %s"
                           % (compose_project, invalid[compose_project]))
    record = records.get(compose_project)
    if record is None:
        raise StoreRefusal(NOT_FOUND, "no lifetime record for %s on this remote" % compose_project)
    return record


def _update(store, request, now):
    caller = _caller(request)
    record = _existing(store, str(request.get("compose_project") or ""))
    if record.state != "active":
        raise StoreRefusal(REAPING, "%s is %s; it cannot be changed" % (record.compose_project, record.state))
    if record.kind != "stack":
        raise StoreRefusal(LIFETIME_INVALID, "%s is a classified target; use sb host classify"
                           % record.compose_project, _holder(record))
    record = _owned_or_forced(record, caller, bool(request.get("force_owner")), "ttl", now)
    lifetime = request.get("lifetime") or record.lifetime
    if lifetime not in LIFETIMES:
        raise StoreRefusal(LIFETIME_INVALID, "unknown lifetime %r" % lifetime)
    ttl = request.get("ttl_seconds")
    stack = record.stack
    if lifetime == "hosted" and record.kind == "stack":
        if stack.ref_branch != stack.dev_branch:
            raise StoreRefusal(LIFETIME_INVALID, "hosted is only for the stack of the dev branch %s"
                               % stack.dev_branch)
        records, _invalid = store.load()
        for other in records.values():
            if (other.compose_project != record.compose_project and _active(other) and other.stack
                    and other.stack.canonical and other.stack.repo == stack.repo
                    and other.stack.dev_branch == stack.dev_branch):
                raise StoreRefusal(HOSTED_TAKEN, "the canonical hosted stack of %s %s here is %s"
                                   % (stack.repo, stack.dev_branch, other.stack.slug), _holder(other))
    if stack is not None:
        stack = replace(stack, canonical=lifetime == "hosted")
    if lifetime == "temporary":
        if ttl is None:
            ttl = record.ttl_seconds or DEFAULT_TTL_SECONDS
        if not isinstance(ttl, int) or not TTL_MIN_SECONDS <= ttl <= TTL_MAX_SECONDS:
            raise StoreRefusal(TTL_INVALID, "ttl_seconds must be between 1h and 90d")
        expires = to_rfc3339(now + ttl)
    elif ttl is not None:
        raise StoreRefusal(TTL_INVALID, "only a temporary stack has a TTL")
    else:
        expires = None
    source = "flag" if request.get("lifetime") else record.lifetime_source
    extended = record.extended_count + (1 if request.get("ttl_seconds") is not None else 0)
    updated = replace(record, lifetime=lifetime, lifetime_source=source,
                      ttl_seconds=ttl if lifetime == "temporary" else None, expires_at=expires,
                      stack=stack, updated_at=to_rfc3339(now), extended_count=extended)
    checked = parse_record(record_to_dict(updated))
    if isinstance(checked, Invalid):
        raise StoreRefusal(LIFETIME_INVALID, checked.reason)
    store.write(updated)
    store.audit("update", record.compose_project, caller, now, lifetime=lifetime, ttl_seconds=ttl)
    return {"record": view(updated, now)}


def _release(store, request, now):
    caller = _caller(request)
    compose_project = str(request.get("compose_project") or "")
    records, invalid = store.load()
    record = records.get(compose_project)
    if record is None:
        if compose_project in invalid and not request.get("force_owner"):
            raise StoreRefusal(RECORD_INVALID, "the record for %s is invalid; pass --force-owner to "
                               "release it" % compose_project)
        if compose_project in invalid:
            kept = store.set_aside(compose_project, now)
            if kept:
                store.audit("release", compose_project, caller, now, invalid=True, kept=kept)
            return {"released": bool(kept), "kept": kept}
        return {"released": store.delete(compose_project)}
    copy_id = request.get("copy_id")
    if (record.stack is not None and copy_id and record.stack.copy_id == copy_id
            and record.owner.same_person(caller)):
        pass
    else:
        record = _owned_or_forced(record, caller, bool(request.get("force_owner")), "down", now)
    store.delete(compose_project)
    store.audit("release", compose_project, caller, now,
                overrides=[dict(item) for item in record.overrides][-1:] if request.get("force_owner") else [])
    return {"released": True}


def _record_merge(store, request, now):
    caller = _caller(request)
    record = _existing(store, str(request.get("compose_project") or ""))
    commit = str(request.get("commit") or "")
    if record.stack is None or record.stack.commit != commit or record.state != "active":
        return {"recorded": False, "reason": "record changed since the check"}
    evidence = dict(request.get("evidence") or {})
    evidence.update({"commit": commit, "controller": caller.controller, "checked_at": to_rfc3339(now)})
    try:
        merge = _merge(evidence)
    except ValueError as exc:
        raise StoreRefusal(REQUEST_INVALID, "merge evidence: %s" % exc)
    store.write(replace(record, merge=merge, updated_at=to_rfc3339(now)))
    store.audit("merge", record.compose_project, caller, now, merged=merge.merged,
                dev_commit=merge.dev_commit)
    return {"recorded": True, "merged": merge.merged}


SUMMARY_MAX_BYTES = 64 * 1024


def _server_summary(home, project, environment):
    """This target's summary of its last succeeded apply, as that apply wrote it on
    this host (``runtime/hosts/<project>/<environment>/summary.json``).

    ``None`` when the summary is missing, unreadable, for another target or not a
    succeeded apply. The exec selector checks the same fields.
    """
    path = Path(home).joinpath("runtime", "hosts", project, environment, "summary.json")
    try:
        # Non-blocking and regular-file only: this runs under the store lock, so a
        # FIFO or device planted at the path must refuse instead of blocking.
        descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                             | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return None
            data = handle.read(SUMMARY_MAX_BYTES + 1)
        summary = json.loads(data.decode("utf-8")) if len(data) <= SUMMARY_MAX_BYTES else None
    except (OSError, UnicodeError, ValueError):
        return None
    if not isinstance(summary, dict):
        return None
    last = summary.get("last_apply")
    if ((summary.get("project"), summary.get("environment")) != (project, environment)
            or summary.get("compose_project") != compose_project_for(project, environment)
            or not isinstance(last, dict) or last.get("status") != "succeeded"):
        return None
    return summary


def _summary_commit(summary):
    commit = summary.get("commit") if summary else None
    return commit if isinstance(commit, str) and _COMMIT_RE.fullmatch(commit) else None


def _summary_hostnames(summary):
    """The route hostnames of a succeeded apply's summary, or ``None`` if it lists none."""
    routes = summary.get("routes") if summary else None
    if not isinstance(routes, list) or not routes:
        return None
    names = [route.get("hostname") if isinstance(route, dict) else None for route in routes]
    if not all(isinstance(name, str) and name.strip() for name in names):
        return None
    return sorted(names)


def _server_summary_commit(home, project, environment):
    """The commit of this target's last succeeded apply on this host, or ``None``."""
    return _summary_commit(_server_summary(home, project, environment))


def _record_apply(store, request, now):
    """After a succeeded apply, move an existing classified target's commit to it.

    Only an existing active ``kind: "target"`` record changes, and only its commit
    and ``updated_at``. A missing record stays missing (``sb host classify`` creates
    it) and lifetime, TTL and owner never change. The commit must be the one the
    server summary records for a succeeded apply.
    """
    project, environment = str(request.get("project") or ""), str(request.get("environment") or "")
    if not TARGET_PART_RE.fullmatch(project) or not TARGET_PART_RE.fullmatch(environment):
        raise StoreRefusal(REQUEST_INVALID, "--target must be PROJECT/ENVIRONMENT")
    commit = request.get("commit")
    if not isinstance(commit, str) or not _COMMIT_RE.fullmatch(commit):
        raise StoreRefusal(REQUEST_INVALID, "commit must be a full commit")
    compose_project = compose_project_for(project, environment)
    records, invalid = store.load()
    record = records.get(compose_project)
    if record is None or compose_project in invalid:
        return {"recorded": False, "reason": "no lifetime record"}
    if record.kind != "target" or record.state != "active" or record.reap_run is not None:
        return {"recorded": False, "reason": "not an active classified target"}
    if _server_summary_commit(store.home, project, environment) != commit:
        return {"recorded": False, "reason": "the server summary does not record this succeeded apply"}
    if record.commit == commit:
        return {"recorded": False, "reason": "already current"}
    updated = replace(record, commit=commit, updated_at=to_rfc3339(now))
    checked = parse_record(record_to_dict(updated))
    if isinstance(checked, Invalid):
        raise StoreRefusal(RECORD_INVALID, checked.reason)
    store.write(updated)
    store.audit("record_apply", compose_project, None, now, commit=commit, previous=record.commit)
    return {"recorded": True, "commit": commit}


def _classify(store, request, now):
    caller = _caller(request)
    project, environment = str(request.get("project") or ""), str(request.get("environment") or "")
    if not TARGET_PART_RE.fullmatch(project) or not TARGET_PART_RE.fullmatch(environment):
        raise StoreRefusal(REQUEST_INVALID, "--target must be PROJECT/ENVIRONMENT")
    compose_project = compose_project_for(project, environment)
    records, invalid = store.load()
    record = records.get(compose_project)
    force = bool(request.get("force_owner"))
    if record is not None and record.kind == "stack":
        raise StoreRefusal(LIFETIME_INVALID, "%s is a stack; use sb host stack ttl" % compose_project,
                           _holder(record))
    if record is not None:
        record = _owned_or_forced(record, caller, force, "classify", now)
    if compose_project in invalid and not force:
        raise StoreRefusal(RECORD_INVALID, "the record for %s is invalid; pass --force-owner to "
                           "replace it" % compose_project)
    if request.get("clear"):
        kept = _set_aside_invalid(store, compose_project, invalid, caller, now)
        removed = bool(kept) or store.delete(compose_project)
        store.audit("unclassify", compose_project, caller, now, removed=removed)
        return {"record": None, "removed": removed, "kept": kept}
    if record is None and compose_project not in store.deployed():
        raise StoreRefusal(TARGET_NOT_FOUND, "%s/%s has no runtime directory on this remote"
                           % (project, environment))
    lifetime = request.get("lifetime")
    if lifetime not in LIFETIMES:
        raise StoreRefusal(LIFETIME_INVALID, "--lifetime must be temporary, hosted or permanent")
    ttl = request.get("ttl_seconds")
    if lifetime == "temporary":
        ttl = ttl or DEFAULT_TTL_SECONDS
        if not isinstance(ttl, int) or not TTL_MIN_SECONDS <= ttl <= TTL_MAX_SECONDS:
            raise StoreRefusal(TTL_INVALID, "ttl_seconds must be between 1h and 90d")
    elif ttl is not None:
        raise StoreRefusal(TTL_INVALID, "only a temporary target has a TTL")
    stamp = to_rfc3339(now)
    # The server's own succeeded apply beats a controller's possibly older hosts.json.
    summary = _server_summary(store.home, project, environment)
    known = {"commit": _summary_commit(summary) or request.get("commit"),
             "repo": request.get("repo")}
    try:
        commit, repo = _target_identity(known)
    except ValueError as exc:
        raise StoreRefusal(REQUEST_INVALID, str(exc))
    hostnames = _summary_hostnames(summary)
    if hostnames is None:
        hostnames = request.get("hostnames")
    if hostnames is None:
        hostnames = list(record.hostnames) if record else []
    if not isinstance(hostnames, list) or not all(isinstance(item, str) for item in hostnames):
        raise StoreRefusal(REQUEST_INVALID, "hostnames must be a list of strings")
    stored = TargetRecord(
        compose_project=compose_project, project=project, environment=environment, kind="target",
        lifetime=lifetime, lifetime_source="classified",
        ttl_seconds=ttl if lifetime == "temporary" else None,
        expires_at=to_rfc3339(now + ttl) if lifetime == "temporary" else None, state="active",
        owner=record.owner if record else caller, created_at=record.created_at if record else stamp,
        updated_at=stamp, front_door=str(request.get("front_door") or (record.front_door if record else "nginx")),
        dns_mode=record.dns_mode if record else "unknown",
        hostnames=tuple(sorted(set(item.strip().rstrip(".").lower() for item in hostnames if item.strip()))),
        commit=commit or (record.commit if record else None), repo=repo or (record.repo if record else None),
        extended_count=record.extended_count if record else 0, classified_by=caller, classified_at=stamp,
        overrides=record.overrides if record else (), extra=dict(record.extra) if record else {})
    checked = parse_record(record_to_dict(stored))
    if isinstance(checked, Invalid):
        raise StoreRefusal(LIFETIME_INVALID, checked.reason)
    kept = _set_aside_invalid(store, compose_project, invalid, caller, now)
    store.write(stored)
    store.audit("classify", compose_project, caller, now, lifetime=lifetime, ttl_seconds=ttl)
    return {"record": view(stored, now), "kept": kept}


def _set_aside_invalid(store, compose_project, invalid, caller, now):
    """A forced repair keeps the invalid bytes for inspection instead of overwriting them."""
    if compose_project not in invalid:
        return None
    kept = store.set_aside(compose_project, now)
    if kept:
        store.audit("set_aside", compose_project, caller, now, kept=kept)
    return kept


def _policy_op(store, request, now):
    wanted = request.get("set")
    if wanted is None:
        return {"policy": record_to_policy(store.policy())}
    caller = _caller(request)
    current = record_to_policy(store.policy())
    current.update({key: value for key, value in dict(wanted).items() if key in DEFAULT_POLICY})
    policy = parse_policy(current)
    store.set_policy(policy)
    store.audit("policy", "-", caller, now, policy=record_to_policy(policy))
    return {"policy": record_to_policy(policy)}


_OPS = {"list": False, "check": False, "claim": True, "update": True, "release": True,
        "record_merge": True, "classify": True, "record_apply": True, "policy": True}


def handle(request, home, *, free_paths=None):
    """Run one request against the store at ``home``; always returns an envelope."""
    try:
        if not isinstance(request, dict) or request.get("op") not in _OPS:
            raise StoreRefusal(REQUEST_INVALID, "unknown store operation")
        op = request["op"]
        now = _now(request)
        store = Store(home, free_paths=free_paths)
        with store.lock():
            if op == "list":
                result = _list(store, request, now)
            elif op in ("check", "claim"):
                result = _claim(store, request, now, write=op == "claim")
            elif op == "update":
                result = _update(store, request, now)
            elif op == "release":
                result = _release(store, request, now)
            elif op == "record_merge":
                result = _record_merge(store, request, now)
            elif op == "classify":
                result = _classify(store, request, now)
            elif op == "record_apply":
                result = _record_apply(store, request, now)
            else:
                result = _policy_op(store, request, now)
        return {"ok": True, "result": result}
    except StoreRefusal as exc:
        payload = {"ok": False, "code": exc.code, "error": exc.message}
        if exc.holder is not None:
            payload["holder"] = exc.holder
        return payload
    except OSError as exc:
        return {"ok": False, "code": REQUEST_INVALID, "error": "store I/O failed: %s" % type(exc).__name__}


def default_home():
    return os.environ.get("SANDBOX_HOME") or os.path.join(os.path.expanduser("~"), "sandbox")


def main():
    try:
        request = json.loads(sys.stdin.read() or "null")
    except ValueError:
        request = None
    sys.stdout.write(json.dumps(handle(request, default_home()), sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
