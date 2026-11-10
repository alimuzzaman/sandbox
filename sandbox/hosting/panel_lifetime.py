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
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Iterable, Literal, Mapping, TypedDict

from sandbox.hosting.lifetime import hoststore

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
