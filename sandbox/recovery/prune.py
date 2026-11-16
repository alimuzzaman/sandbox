"""Plan-bound pruning of verified recovery sets beyond a keep count.

A prune is two steps. ``plan`` verifies every set of one profile in the
destination (manifest, ciphertext digest, current passphrase), keeps the newest
``keep_count`` verified sets, and stores the remaining verified sets as
candidates under a content-derived plan id. ``apply`` needs that id and
``--confirm``: it re-verifies the destination, refuses unless the fresh
inventory still yields the same candidates and keeps every planned protected
set, writes a deletion intent for each set to an owner-only journal, and only
then deletes that set's listed objects.

Only complete, verified, current-passphrase sets are ever candidates. Sets that
are incomplete, unverifiable, of another profile, or outside ``sets/`` are never
touched. The newest verified set is never a candidate because ``keep_count`` is
at least one, and apply re-checks that against the fresh inventory.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
from datetime import datetime, timezone
from pathlib import Path

from .errors import RecoveryError

PLAN_SECONDS = 24 * 3600
_PLAN_ID_RE = re.compile(r"rp_[0-9a-f]{32}")
_PROFILE_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


def valid_plan_id(value: object) -> bool:
    return isinstance(value, str) and bool(_PLAN_ID_RE.fullmatch(value))


def valid_profile(value: object) -> bool:
    return isinstance(value, str) and bool(_PROFILE_RE.fullmatch(value))


def plan_id_for(body: dict) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "rp_" + hashlib.sha256(canonical).hexdigest()[:32]


def _private_dir(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise RecoveryError("recovery retention state directory is unsafe", "retention_state_unsafe")
    return path


def _write_private(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


class PruneStore:
    """Owner-only plans and append-only deletion journals."""

    def __init__(self, root: Path, *, clock=None) -> None:
        self.root = Path(root)
        self.clock = clock or time.time

    def _plans(self) -> Path:
        return _private_dir(self.root / "plans")

    def _journals(self) -> Path:
        return _private_dir(self.root / "deletions")

    def save(self, body: dict) -> dict:
        plan_id = plan_id_for(body)
        path = self._plans() / f"{plan_id}.json"
        record = {**body, "plan_id": plan_id,
                  "expires_at": int(self.clock()) + PLAN_SECONDS}
        if path.exists():
            existing = self.load(plan_id, allow_expired=True)
            if existing.get("expires_at", 0) > self.clock():
                return existing
        _write_private(path, json.dumps(record, sort_keys=True).encode("utf-8"))
        return record

    def load(self, plan_id: str, *, allow_expired: bool = False) -> dict:
        if not valid_plan_id(plan_id):
            raise RecoveryError("retention plan id is invalid", "invalid_retention_plan")
        path = self._plans() / f"{plan_id}.json"
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise RecoveryError("retention plan file is unsafe", "retention_state_unsafe")
            record = json.loads(path.read_bytes())
        except FileNotFoundError:
            raise RecoveryError("retention plan is unknown", "retention_plan_unknown") from None
        except ValueError:
            raise RecoveryError("retention plan is invalid", "invalid_retention_plan") from None
        body = {key: value for key, value in record.items() if key not in {"plan_id", "expires_at"}}
        if record.get("plan_id") != plan_id or plan_id_for(body) != plan_id:
            raise RecoveryError("retention plan does not match its id", "invalid_retention_plan")
        if not allow_expired and record.get("expires_at", 0) <= self.clock():
            raise RecoveryError("retention plan expired; plan again", "retention_plan_expired")
        return record

    def journaled(self, plan_id: str) -> set[str]:
        path = self._journals() / f"{plan_id}.jsonl"
        if not path.exists():
            return set()
        started = set()
        for line in path.read_text("utf-8").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("phase") == "intent":
                started.add(event.get("set_id"))
        return started

    def append(self, plan_id: str, event: dict) -> None:
        path = self._journals() / f"{plan_id}.jsonl"
        line = json.dumps({**event, "plan_id": plan_id,
                           "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")},
                          sort_keys=True) + "\n"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(descriptor, line.encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def plan_body(destination: str, profile: str, keep_count: int, inventory: dict) -> dict:
    """The exact, content-bound facts a confirmed prune must still find."""
    return {
        "schema_version": 1, "destination": destination, "profile": profile,
        "keep_count": keep_count,
        "protected_sets": list(inventory["protected_sets"]),
        "candidates": [{"id": item["id"], "ciphertext_sha256": item["ciphertext_sha256"]}
                       for item in inventory["candidates"]],
    }


def apply_prune(store: PruneStore, plan: dict, fresh: dict, drive) -> dict:
    """Delete the planned candidates after the fresh inventory confirms them."""
    plan_id = plan["plan_id"]
    planned = {item["id"]: item["ciphertext_sha256"] for item in plan["candidates"]}
    started = store.journaled(plan_id)
    fresh_candidates = {item["id"]: item["ciphertext_sha256"] for item in fresh["candidates"]}
    fresh_protected = set(fresh["protected_sets"])
    newest = fresh.get("newest_verified")
    if not set(plan["protected_sets"]) <= fresh_protected:
        raise RecoveryError("a planned protected set is no longer verified", "stale_retention_plan")
    if newest is None or newest in planned:
        raise RecoveryError("the newest verified set would be deleted", "stale_retention_plan")
    for set_id, digest in planned.items():
        if set_id in fresh_candidates:
            if fresh_candidates[set_id] != digest:
                raise RecoveryError("a candidate set changed since planning", "stale_retention_plan")
        elif set_id not in started or set_id in fresh_protected:
            raise RecoveryError("retention candidates are stale", "stale_retention_plan")
    if set(fresh_candidates) - set(planned):
        raise RecoveryError("retention candidates changed since planning", "stale_retention_plan")
    deleted = []
    for set_id in sorted(planned):
        store.append(plan_id, {"phase": "intent", "set_id": set_id,
                               "ciphertext_sha256": planned[set_id]})
        keys = sorted((key for key in fresh["objects"].get(set_id, ())
                       if key.startswith(f"sets/{set_id}/")),
                      key=lambda key: (not key.endswith("/manifest.json"), key))
        # The manifest goes first: an interrupted prune leaves an incomplete
        # set that no restore or later plan treats as verified.
        for key in keys:
            drive.delete(key)
        drive.remove_empty_dir(f"sets/{set_id}")
        store.append(plan_id, {"phase": "outcome", "set_id": set_id, "objects": len(keys)})
        deleted.append(set_id)
    return {"plan_id": plan_id, "deleted": deleted, "kept": sorted(fresh_protected)}
