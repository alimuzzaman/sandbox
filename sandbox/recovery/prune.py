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
from contextlib import contextmanager
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

    def journaled(self, plan_id: str) -> dict[str, dict]:
        """Started sets of a plan: the exact objects each intent approved."""
        path = self._journals() / f"{plan_id}.jsonl"
        if not path.exists():
            return {}
        started: dict[str, dict] = {}
        for line in path.read_text("utf-8").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if (isinstance(event, dict) and event.get("phase") == "intent"
                    and isinstance(event.get("set_id"), str)
                    and isinstance(event.get("objects"), list)):
                started.setdefault(event["set_id"], event)
        return started

    @contextmanager
    def locked(self):
        """One prune apply at a time on this controller."""
        import fcntl
        descriptor = os.open(self._journals() / ".lock", os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise RecoveryError("another retention prune is running", "retention_locked") from None
            yield
        finally:
            os.close(descriptor)

    def append(self, plan_id: str, event: dict) -> None:
        journals = self._journals()
        path = journals / f"{plan_id}.jsonl"
        created = not path.exists()
        line = json.dumps({**event, "plan_id": plan_id,
                           "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")},
                          sort_keys=True) + "\n"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(descriptor, line.encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if created:
            # Make the new journal's directory entries durable before any delete.
            for directory in (journals, journals.parent, journals.parent.parent):
                _fsync_dir(directory)


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def plan_body(destination: str, profile: str, keep_count: int, inventory: dict) -> dict:
    """The exact, content-bound facts a confirmed prune must still find."""
    return {
        "schema_version": 1, "destination": destination, "profile": profile,
        "keep_count": keep_count,
        "protected_sets": list(inventory["protected_sets"]),
        "candidates": [{"id": item["id"], "ciphertext_sha256": item["ciphertext_sha256"],
                        "ciphertext_object": item["ciphertext_object"]}
                       for item in inventory["candidates"]],
    }


def _ordered(keys) -> list[str]:
    # The manifest goes first: an interrupted prune leaves an incomplete set
    # that no restore or later plan treats as verified.
    return sorted(keys, key=lambda key: (not key.endswith("/manifest.json"), key))


def apply_prune(store: PruneStore, plan: dict, fresh: dict, drive, *, recheck,
                protected_present, remnant_matches) -> dict:
    """Delete the planned candidates after the fresh inventory confirms them.

    ``recheck(set_id)`` re-verifies one candidate immediately before its
    deletes and returns its current object keys. ``protected_present()``
    re-lists the destination and confirms every protected manifest is still
    there. ``remnant_matches(key, digest)`` checks a surviving ciphertext on
    resume. Anything unexpected refuses before the next delete.
    """
    plan_id = plan["plan_id"]
    planned = {item["id"]: item for item in plan["candidates"]}
    fresh_candidates = {item["id"]: item["ciphertext_sha256"] for item in fresh["candidates"]}
    fresh_protected = set(fresh["protected_sets"])
    newest = fresh.get("newest_verified")
    with store.locked():
        started = store.journaled(plan_id)
        if not set(plan["protected_sets"]) <= fresh_protected:
            raise RecoveryError("a planned protected set is no longer verified", "stale_retention_plan")
        if newest is None or newest in planned:
            raise RecoveryError("the newest verified set would be deleted", "stale_retention_plan")
        for set_id, item in planned.items():
            if set_id in fresh_protected:
                raise RecoveryError("a candidate set is now protected", "stale_retention_plan")
            if set_id in fresh_candidates:
                if fresh_candidates[set_id] != item["ciphertext_sha256"]:
                    raise RecoveryError("a candidate set changed since planning", "stale_retention_plan")
            elif set_id not in started:
                raise RecoveryError("retention candidates are stale", "stale_retention_plan")
        if set(fresh_candidates) - set(planned):
            raise RecoveryError("retention candidates changed since planning", "stale_retention_plan")
        deleted = []
        for set_id in sorted(planned):
            item = planned[set_id]
            if set_id in started:
                approved = set(started[set_id]["objects"])
                remaining = set(fresh["objects"].get(set_id, ()))
                manifest = f"sets/{set_id}/manifest.json"
                if set_id not in fresh_candidates:
                    # Resume: only remnants of the approved set may remain.
                    if manifest in remaining or not remaining <= approved:
                        raise RecoveryError("a started set changed since its intent", "stale_retention_plan")
                    for key in remaining:
                        if key == item["ciphertext_object"] and not remnant_matches(
                                key, item["ciphertext_sha256"]):
                            raise RecoveryError("a started set changed since its intent",
                                                "stale_retention_plan")
                    keys = _ordered(remaining)
                else:
                    keys = _ordered(recheck(set_id))
                    if not set(keys) <= approved:
                        raise RecoveryError("a started set changed since its intent", "stale_retention_plan")
            else:
                keys = _ordered(recheck(set_id))
                store.append(plan_id, {"phase": "intent", "set_id": set_id, "objects": keys,
                                       "ciphertext_sha256": item["ciphertext_sha256"]})
            if not protected_present():
                raise RecoveryError("a protected set disappeared during the prune", "stale_retention_plan")
            for key in keys:
                drive.delete(key)
            drive.remove_empty_dir(f"sets/{set_id}")
            store.append(plan_id, {"phase": "outcome", "set_id": set_id, "objects": len(keys)})
            deleted.append(set_id)
    return {"plan_id": plan_id, "deleted": deleted, "kept": sorted(fresh_protected)}
