"""Machine-local state for controller auto-update (spec 072).

``$SANDBOX_HOME/runtime/controller-update/state.json`` holds the newest
installed source revision each remote reported (untrusted hints) and a short
memo of refused revisions. Every checkout on the machine shares it. See
``specs/072-controller-auto-update/contracts/state-file.md``.

Writers are best effort: they return ``False`` instead of raising, because a
hint is never worth failing the command that observed it.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
import time
from pathlib import Path

from sandbox.controller_update.models import (
    HINT_SOURCES, Hint, MEMOIZED_REASONS, Refusal, SkipReason, State,
    parse_remote_name, parse_revision,
)

SCHEMA_VERSION = 1
_LOCK_BUDGET = 5.0
_REASONS = {reason.code: reason for reason in MEMOIZED_REASONS}


def default_path(runtime_dir: Path | None = None) -> Path:
    if runtime_dir is None:
        from sandbox.core._paths import RUNTIME_DIR
        runtime_dir = RUNTIME_DIR
    return Path(runtime_dir) / "controller-update" / "state.json"


def _now(now: float | None) -> float:
    return time.time() if now is None else float(now)


def _parse(raw: object, now: float) -> State:
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        return State()
    hints = []
    raw_hints = raw.get("hints")
    if isinstance(raw_hints, dict):
        for remote, entry in raw_hints.items():
            if not isinstance(entry, dict):
                continue
            name = parse_remote_name(remote)
            revision = parse_revision(entry.get("revision"))
            source = entry.get("source")
            recorded_at = entry.get("recorded_at")
            if (name is None or revision is None or source not in HINT_SOURCES
                    or isinstance(recorded_at, bool)
                    or not isinstance(recorded_at, (int, float))):
                continue
            hints.append(Hint(name, revision, source, float(recorded_at)))
    refusals = []
    raw_refusals = raw.get("refusals")
    if isinstance(raw_refusals, list):
        for entry in raw_refusals:
            if not isinstance(entry, dict):
                continue
            revision = parse_revision(entry.get("revision"))
            upstream = entry.get("upstream")
            reason = _REASONS.get(entry.get("reason"))
            until = entry.get("until")
            if (revision is None or not isinstance(upstream, str) or not upstream
                    or reason is None or isinstance(until, bool)
                    or not isinstance(until, (int, float)) or float(until) <= now):
                continue
            refusals.append(Refusal(revision, upstream, reason, float(until)))
    return State(tuple(hints), tuple(refusals))


def _read(path: Path, now: float) -> State:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return State()
    return _parse(raw, now)


def load(path: Path | None = None, *, now: float | None = None) -> State:
    """Return the parsed state; a missing or malformed file is empty."""
    return _read(path or default_path(), _now(now))


def refused(current: State, revision: str, upstream: str, *, now: float | None = None) -> bool:
    moment = _now(now)
    return any(r.revision == revision and r.upstream == upstream and r.until > moment
               for r in current.refusals)


def _serialize(current: State) -> str:
    return json.dumps({
        "schema_version": SCHEMA_VERSION,
        "hints": {h.remote: {"revision": h.revision, "source": h.source,
                             "recorded_at": h.recorded_at} for h in current.hints},
        "refusals": [{"revision": r.revision, "upstream": r.upstream,
                      "reason": r.reason.code, "until": r.until} for r in current.refusals],
    }, sort_keys=True, indent=1) + "\n"


@contextlib.contextmanager
def _locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd = os.open(str(path) + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + _LOCK_BUDGET
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("controller update state is busy") from None
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)


def _update(path: Path | None, now: float | None, change) -> bool:
    target = path or default_path()
    moment = _now(now)
    try:
        with _locked(target):
            current = _read(target, moment)
            updated = change(current, moment)
            fd, temporary = tempfile.mkstemp(prefix=".state.", dir=str(target.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(_serialize(updated))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temporary, 0o600)
                os.replace(temporary, target)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(temporary)
                raise
    except (OSError, TimeoutError, ValueError):
        return False
    return True


def record_hint(remote: str, revision: str, source: str, *,
                path: Path | None = None, now: float | None = None) -> bool:
    """Remember that ``remote`` reported ``revision``; replaces its old hint."""
    name = parse_remote_name(remote)
    sha = parse_revision(revision)
    if name is None or sha is None or source not in HINT_SOURCES:
        return False

    def change(current: State, moment: float) -> State:
        hints = [h for h in current.hints if h.remote != name]
        hints.append(Hint(name, sha, source, moment))
        return State(tuple(sorted(hints, key=lambda h: h.remote)), current.refusals)

    return _update(path, now, change)


def record_refusal(revision: str, upstream: str, reason: SkipReason, ttl: float, *,
                   path: Path | None = None, now: float | None = None) -> bool:
    """Remember a refused revision for ``ttl`` seconds."""
    sha = parse_revision(revision)
    if sha is None or reason not in MEMOIZED_REASONS or not upstream:
        return False

    def change(current: State, moment: float) -> State:
        kept = [r for r in current.refusals
                if not (r.revision == sha and r.upstream == upstream)]
        kept.append(Refusal(sha, upstream, reason, moment + float(ttl)))
        return State(current.hints, tuple(kept))

    return _update(path, now, change)
