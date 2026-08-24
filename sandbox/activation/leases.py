"""Cross-process activity leases that prevent unsafe idle suspension."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import time
import uuid


_SAFE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def _root() -> Path:
    from sandbox.core._paths import RUNTIME_DIR
    return RUNTIME_DIR / "activation" / "leases"


def _instance_dir(instance: str) -> Path:
    if not isinstance(instance, str) or not _SAFE.fullmatch(instance):
        raise ValueError("activity lease instance is invalid")
    return _root() / instance


@contextmanager
def instance_activity(instance: str, kind: str, *, ttl_seconds: int):
    """Publish one bounded, owner-only lease for a synchronous operation."""
    if (not isinstance(kind, str) or not _SAFE.fullmatch(kind) or
            isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or
            not 1 <= ttl_seconds <= 604800):
        raise ValueError("activity lease is invalid")
    directory = _instance_dir(instance)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / f"{uuid.uuid4().hex}.json"
    payload = {"schema_version": 1, "kind": kind, "pid": os.getpid(),
               "expires_at": time.time() + ttl_seconds}
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    path.chmod(0o600)
    try:
        yield
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def has_active_instance_lease(instance: str, *, now: float | None = None) -> bool:
    """Return true on a live lease or malformed/unreadable lease evidence."""
    directory = _instance_dir(instance)
    if not directory.exists():
        return False
    current = time.time() if now is None else float(now)
    for path in directory.glob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            valid = (isinstance(value, dict) and value.get("schema_version") == 1 and
                     isinstance(value.get("expires_at"), (int, float)) and
                     not isinstance(value.get("expires_at"), bool))
            if not valid:
                return True
            if float(value["expires_at"]) > current:
                return True
            path.unlink(missing_ok=True)
        except (OSError, TypeError, ValueError):
            return True
    return False


__all__ = ["has_active_instance_lease", "instance_activity"]
