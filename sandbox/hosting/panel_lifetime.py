"""Spec 077 lifetimes as the `sb web` panel shows them (spec 076 follow-up, read-only).

Every rule that decides whether a record is valid, or whether cleanup may
remove its target, lives in ``hoststore.py`` (CLAUDE.md gotcha 35). This
module only:

* runs each row through ``hoststore.parse_record`` and ``hoststore.view``,
  whether it came from this server's files (``Store.peek``) or from a
  controller's ``sb host stack list --remote R --json``;
* projects the result onto the panel's allow-list (``LifetimeView``);
* orders the rows so temporary copies near expiry come first;
* caches the controller's per-remote listing off the request path.

Anything that is not a record the store accepts is "unclassified, protected"
(or "unknown" while a source cannot be read) and is never reapable.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterable, Literal, Mapping, TypedDict

from sandbox.hosting.lifetime import hoststore

if TYPE_CHECKING:
    from typing import NotRequired

Lifetime = Literal["temporary", "hosted", "permanent"]
LifetimeState = Literal["active", "reaping", "reaped"]
Classification = Literal["recorded", "unclassified", "unknown"]
UnclassifiedReason = Literal["no_record", "invalid", "unavailable", "loading"]
ReapReason = Literal["expired", "merged"]
Kind = Literal["stack", "target"]

LIFETIMES: frozenset[str] = frozenset({"temporary", "hosted", "permanent"})
STATES: frozenset[str] = frozenset({"active", "reaping", "reaped"})
REAP_REASONS: frozenset[str] = frozenset({"expired", "merged"})

MAX_REMOTES = 16
CACHE_TTL_SECONDS = 60
FETCH_TIMEOUT_SECONDS = 90
MAX_LIST_BYTES = 4 * 1024 * 1024
_LABEL_LIMIT = 200


class LifetimeView(TypedDict):
    classification: Classification
    reason: UnclassifiedReason | None
    lifetime: Lifetime | None
    owner: str | None
    expires_at: int | None
    seconds_left: int | None
    canonical: bool
    reap_eligible: bool
    reap_reason: ReapReason | None
    state: LifetimeState | None
    extended_count: int
    kind: Kind | None
    slug: str | None
    copy_id: str | None
    ref_branch: str | None
    commit: str | None


class LifetimeRow(LifetimeView):
    remote: str
    source: Literal["this-server", "controller"]
    project: str
    environment: str
    compose_project: str
    name: str
    card: bool


class LifetimeListing(TypedDict):
    """One source's lifetimes, keyed by compose project."""
    available: bool
    records: dict[str, LifetimeView]
    partial_reasons: list[str]
    identities: NotRequired[dict[str, tuple[str, str]]]  # compose project -> (project, environment)


# Spec 077's agreed row fields; everything else in a row is ignored, so a
# controller-only key (manifest path, last_apply) never reaches the store parser.
RECORD_FIELDS = (
    "schema_version", "compose_project", "project", "environment", "kind", "lifetime",
    "lifetime_source", "ttl_seconds", "expires_at", "extended_count", "state", "owner",
    "created_at", "updated_at", "classified_by", "classified_at", "slug", "copy_id", "repo",
    "ref", "ref_branch", "commit", "dev_branch", "canonical", "template", "ports", "hostnames",
    "front_door", "dns_mode", "merge", "reap_run", "overrides")


def _empty(classification: Classification, reason: UnclassifiedReason) -> LifetimeView:
    return {"classification": classification, "reason": reason, "lifetime": None, "owner": None,
            "expires_at": None, "seconds_left": None, "canonical": False, "reap_eligible": False,
            "reap_reason": None, "state": None, "extended_count": 0, "kind": None, "slug": None,
            "copy_id": None, "ref_branch": None, "commit": None}


def unclassified(reason: UnclassifiedReason = "no_record") -> LifetimeView:
    """No valid record: protected, never reapable."""
    return _empty("unclassified", reason)


def unknown(reason: UnclassifiedReason = "unavailable") -> LifetimeView:
    """The source could not be read (yet); nothing is claimed either way."""
    return _empty("unknown", reason)


def _text(value: object, limit: int = _LABEL_LIMIT) -> str | None:
    return value if isinstance(value, str) and value and len(value) <= limit else None


def project(row: object, now: int) -> LifetimeView:
    """Validate ``row`` with the store's own rules and project it for the panel.

    ``row`` is a record as stored, a ``hoststore.view`` dict or a
    ``stack list --json`` row. Eligibility is recomputed at ``now``.
    """
    if not isinstance(row, Mapping):
        return unclassified("invalid")
    data = {key: row[key] for key in RECORD_FIELDS if key in row}
    record = hoststore.parse_record(data, None)
    if not isinstance(record, hoststore.TargetRecord):
        return unclassified("invalid")
    shown = hoststore.view(record, now)
    reap = shown.get("reap") or {}
    expires = hoststore.parse_rfc3339(shown.get("expires_at")) if shown.get("expires_at") else None
    owner = shown.get("owner") or {}
    eligible = reap.get("eligible") is True
    reason = reap.get("reason") if eligible and reap.get("reason") in REAP_REASONS else None
    kind = shown.get("kind") if shown.get("kind") in ("stack", "target") else None
    return {
        "classification": "recorded", "reason": None,
        "lifetime": shown["lifetime"] if shown.get("lifetime") in LIFETIMES else None,
        "owner": _text(owner.get("label")) if isinstance(owner, Mapping) else None,
        "expires_at": expires, "seconds_left": (expires - int(now)) if expires is not None else None,
        "canonical": shown.get("canonical") is True, "reap_eligible": eligible, "reap_reason": reason,
        "state": shown["state"] if shown.get("state") in STATES else None,
        "extended_count": shown.get("extended_count") if isinstance(shown.get("extended_count"), int) else 0,
        "kind": kind, "slug": _text(shown.get("slug"), 64), "copy_id": _text(shown.get("copy_id"), 16),
        "ref_branch": _text(shown.get("ref_branch")), "commit": _text(shown.get("commit"), 40),
    }


def listing(records: Mapping[str, LifetimeView], partial_reasons: Iterable[str] = (), *,
            identities: Mapping[str, tuple[str, str]] | None = None) -> LifetimeListing:
    out: LifetimeListing = {"available": True, "records": dict(records),
                            "partial_reasons": list(partial_reasons)}
    if identities:
        out["identities"] = dict(identities)
    return out


def unavailable(reason: str) -> LifetimeListing:
    return {"available": False, "records": {}, "partial_reasons": [reason]}


def lookup(source: LifetimeListing | None, compose_project: str) -> LifetimeView:
    """The lifetime of one target: never reapable unless its record is valid."""
    if source is None:
        return unknown("loading")
    if not source.get("available"):
        return unknown("unavailable")
    found = source["records"].get(compose_project)
    return dict(found) if found else unclassified("no_record")  # type: ignore[return-value]


def identity(source: LifetimeListing | None, compose_project: str) -> tuple[str, str] | None:
    names = (source or {}).get("identities") or {}
    pair = names.get(compose_project)
    return (pair[0], pair[1]) if pair else None


_RANK = {"temporary": 1, "hosted": 2, "permanent": 3}


def sort_key(row: Mapping) -> tuple:
    """Temporary copies first, reapable ones before the rest, soonest expiry first."""
    if row.get("classification") == "recorded":
        rank = 0 if row.get("lifetime") == "temporary" and row.get("reap_eligible") else _RANK.get(
            str(row.get("lifetime")), 4)
    else:
        rank = 4 if row.get("classification") == "unknown" else 5
    expires = row.get("expires_at")
    return (rank, expires if isinstance(expires, int) else float("inf"),
            str(row.get("owner") or ""), str(row.get("name") or ""), str(row.get("remote") or ""))


def order(rows: Iterable[LifetimeRow]) -> list[LifetimeRow]:
    return sorted(rows, key=sort_key)


# ------------------------------------------------------------------ sources

def _from_store_result(result: Mapping, now: int) -> tuple[dict[str, LifetimeView], dict, list[str]]:
    records: dict[str, LifetimeView] = {}
    names: dict[str, tuple[str, str]] = {}
    reasons: list[str] = []
    for row in result.get("records") or []:
        if not isinstance(row, Mapping) or not isinstance(row.get("compose_project"), str):
            continue
        records[row["compose_project"]] = project(row, now)
        if records[row["compose_project"]]["classification"] == "recorded":
            names[row["compose_project"]] = (str(row.get("project")), str(row.get("environment")))
    for item in result.get("invalid") or []:
        key = item.get("compose_project") if isinstance(item, Mapping) else None
        if isinstance(key, str) and key.startswith("sandbox-host-") and len(key) <= 200:
            records[key] = unclassified("invalid")
            reasons.append(f"lifetime_record_invalid:{key}")
    return records, names, reasons


def read_server(runtime_dir: Path, now: int, *, max_files: int = hoststore.PEEK_MAX_FILES) -> LifetimeListing:
    """This server's lifetime records, read only through ``Store.peek`` (no lock, no write)."""
    try:
        result = hoststore.Store(Path(runtime_dir).parent).peek(now, max_files=max_files)
    except Exception as exc:  # the panel shows the rest; every target reads "unknown"
        return unavailable(f"lifetime_records_unreadable:{type(exc).__name__}")
    records, names, reasons = _from_store_result(result, now)
    if result.get("truncated"):
        reasons.append("lifetime_records_truncated")
    return listing(records, reasons, identities=names)


def listing_from_stack_list(payload: object, now: int) -> LifetimeListing:
    """Parse ``sb host stack list --remote R --json`` output into a listing."""
    if (not isinstance(payload, Mapping) or payload.get("ok") is not True or payload.get("remote_error")
            or not isinstance(payload.get("stacks"), list) or not isinstance(payload.get("targets", []), list)):
        remote = payload.get("remote") if isinstance(payload, Mapping) else None
        return unavailable(f"lifetime_unavailable:{remote}" if isinstance(remote, str) else "lifetime_unavailable")
    present = [row for row in payload["stacks"]
               if isinstance(row, Mapping) and row.get("remote_record") == "present"]
    records, names, reasons = _from_store_result(
        {"records": present + list(payload.get("targets") or []), "invalid": payload.get("invalid")}, now)
    for row in payload["stacks"]:
        key = row.get("compose_project") if isinstance(row, Mapping) else None
        if isinstance(key, str) and key not in records and row.get("remote_record") in ("missing", "invalid"):
            records[key] = unclassified("invalid" if row.get("remote_record") == "invalid" else "no_record")
    for key in payload.get("unclassified") or []:
        if isinstance(key, str) and key.startswith("sandbox-host-") and key not in records:
            records[key] = unclassified("no_record")
    return listing(records, reasons, identities=names)


_REMOTE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")


def stack_list_fetcher(entry: Path, *, run: Callable = subprocess.run,
                       timeout: float = FETCH_TIMEOUT_SECONDS) -> Callable[[str], object]:
    """A fetch function that runs this checkout's ``sb host stack list --remote R --json``."""

    def fetch(remote: str) -> object:
        env = {**os.environ, "SANDBOX_AUTO_UPDATE": "0"}
        result = run([str(entry), "host", "stack", "list", "--remote", remote, "--json"],
                     capture_output=True, text=True, timeout=timeout, env=env, check=False,
                     stdin=subprocess.DEVNULL)
        text = (result.stdout or "")[-MAX_LIST_BYTES:]
        lines = [line for line in text.splitlines() if line.strip().startswith("{")]
        return json.loads(lines[-1]) if lines else None
    return fetch


class LifetimeCache:
    """Per-remote ``stack list`` results, refreshed in the background.

    ``snapshot`` never waits: it returns what it has (``None`` until the first
    fetch lands) and schedules a refresh for anything older than ``ttl``, at
    most one per remote in flight.
    """

    def __init__(self, fetch: Callable[[str], object], *, ttl: float = CACHE_TTL_SECONDS,
                 max_remotes: int = MAX_REMOTES, workers: int = 2,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time,
                 submit: Callable[[Callable[[], None]], object] | None = None):
        self._fetch = fetch
        self._ttl = ttl
        self._max = max_remotes
        self._workers = workers
        self._clock = clock
        self._wall = wall
        self._lock = threading.Lock()
        self._results: dict[str, tuple[float, object, bool]] = {}
        self._in_flight: set[str] = set()
        self._executor: ThreadPoolExecutor | None = None
        self._submit = submit or self._pool_submit

    def _pool_submit(self, job: Callable[[], None]) -> object:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=self._workers,
                                                thread_name_prefix="sandbox-panel-lifetime")
        return self._executor.submit(job)

    def _run(self, remote: str) -> None:
        try:
            payload, ok = self._fetch(remote), True
        except Exception:
            payload, ok = None, False
        with self._lock:
            self._results[remote] = (self._clock(), payload, ok)
            self._in_flight.discard(remote)

    def snapshot(self, remotes: Iterable[str], now: int) -> dict[str, LifetimeListing | None]:
        wanted: list[str] = []
        for remote in remotes:
            if isinstance(remote, str) and _REMOTE_RE.fullmatch(remote) and remote not in wanted:
                wanted.append(remote)
            if len(wanted) >= self._max:
                break
        out: dict[str, LifetimeListing | None] = {}
        due: list[str] = []
        clock = self._clock()
        with self._lock:
            # Keep only the remotes asked for now (and any still in flight), so a
            # long-lived panel never holds more than about 2 x max_remotes entries.
            if len(self._results) > self._max:
                keep = set(wanted) | self._in_flight
                self._results = {name: value for name, value in self._results.items() if name in keep}
            for remote in wanted:
                cached = self._results.get(remote)
                if cached is None:
                    out[remote] = None
                elif not cached[2]:
                    out[remote] = unavailable(f"lifetime_unavailable:{remote}")
                else:
                    parsed = listing_from_stack_list(cached[1], now)
                    if not parsed["available"]:
                        parsed = unavailable(f"lifetime_unavailable:{remote}")
                    out[remote] = parsed
                if (cached is None or clock - cached[0] >= self._ttl) and remote not in self._in_flight:
                    self._in_flight.add(remote)
                    due.append(remote)
        for remote in due:
            try:
                self._submit(lambda remote=remote: self._run(remote))
            except RuntimeError:
                with self._lock:
                    self._in_flight.discard(remote)
        return out
