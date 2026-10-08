"""Routine config and run records on the host.

Layout under ``$SANDBOX_HOME/runtime/resources/cleanup-routine/`` (0700):
``routine.json`` and ``runs/<run_id>.json``, each 0600 JSON.  Deletion
manifests stay where the probe writes them, in the sibling ``deletions/``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any

from ..host_guard import host_runtime
from .contract import MAX_HISTORY

RETAINED_RUNS = MAX_HISTORY
# The init-system backstop fires 60 seconds after the run's own bound.
BACKSTOP_SECONDS = 60
_MAX_RECORD_BYTES = 64 * 1024
_MAX_MANIFEST_BYTES = 16 * 1024 * 1024
_RUN_ID = re.compile(r"^[0-9a-f]{32}$")


def utc_iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def default_root() -> Path:
    return host_runtime() / "resources" / "cleanup-routine"


def manifest_reference(run_id: str) -> str:
    return f"deletions/{run_id}.jsonl"


class RoutineStore:
    """Owner-only JSON records with atomic replace and bounded reads."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or default_root())
        self.config_path = self.root / "routine.json"
        self.runs_dir = self.root / "runs"
        self.deletions_dir = self.root.parent / "deletions"

    # -- primitives -------------------------------------------------------

    @staticmethod
    def _private_dir(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        state = path.lstat()
        if (not stat.S_ISDIR(state.st_mode) or state.st_uid != os.getuid()):
            raise OSError(f"routine directory is unsafe: {path.name}")
        if stat.S_IMODE(state.st_mode) != 0o700:
            os.chmod(path, 0o700)

    def _write(self, path: Path, payload: dict) -> None:
        self._private_dir(self.root)
        self._private_dir(path.parent)
        try:
            existing = path.lstat()
        except FileNotFoundError:
            existing = None
        if existing is not None and not stat.S_ISREG(existing.st_mode):
            raise OSError(f"routine record is not a regular file: {path.name}")
        content = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            temporary = None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    @staticmethod
    def _read(path: Path) -> dict | None:
        try:
            descriptor = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            return None
        try:
            state = os.fstat(descriptor)
            if not stat.S_ISREG(state.st_mode) or state.st_uid != os.getuid():
                return None
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                raw = handle.read(_MAX_RECORD_BYTES + 1)
        except OSError:
            return None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if len(raw) > _MAX_RECORD_BYTES:
            return None
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    # -- config -----------------------------------------------------------

    def read_config(self) -> dict | None:
        return self._read(self.config_path)

    def write_config(self, config: dict) -> None:
        self._write(self.config_path, dict(config))

    # -- runs -------------------------------------------------------------

    def _run_path(self, run_id: Any) -> Path:
        if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
            raise ValueError("run id must be 32 lowercase hex characters")
        return self.runs_dir / f"{run_id}.json"

    def write_run(self, record: dict) -> None:
        self._write(self._run_path(record.get("run_id")), dict(record))
        self.prune()

    def _all_runs(self) -> list[dict]:
        records = []
        try:
            entries = list(self.runs_dir.iterdir())
        except OSError:
            return records
        for path in entries:
            if path.suffix != ".json" or _RUN_ID.fullmatch(path.stem) is None:
                continue
            record = self._read(path)
            if record is None or record.get("run_id") != path.stem:
                continue
            records.append(record)
        records.sort(
            key=lambda item: (parse_iso(item.get("started_at"))
                              or datetime.min.replace(tzinfo=timezone.utc),
                              item["run_id"]),
            reverse=True,
        )
        return records

    def runs(self, limit: int = RETAINED_RUNS) -> list[dict]:
        return self._all_runs()[:max(int(limit), 0)]

    def prune(self, keep: int = RETAINED_RUNS) -> None:
        for record in self._all_runs()[keep:]:
            try:
                self._run_path(record["run_id"]).unlink()
            except OSError:
                pass

    # -- backstop ---------------------------------------------------------

    def _manifest_counts(self, run_id: str) -> tuple[dict[str, Any], bool]:
        """Count completed outcomes in one run's manifest, bounded."""
        counts = {"removed": 0, "bytes_reclaimed": 0, "skipped": 0,
                  "skipped_reasons": {}}
        path = self.deletions_dir / f"{run_id}.jsonl"
        try:
            with path.open("rb") as handle:
                raw = handle.read(_MAX_MANIFEST_BYTES)
        except OSError:
            return counts, False
        for line in raw.decode("utf-8", "replace").splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if not isinstance(item, dict) or item.get("phase") != "outcome":
                continue
            status = item.get("status")
            if status == "removed":
                counts["removed"] += 1
                size = item.get("bytes")
                if isinstance(size, int) and not isinstance(size, bool) and size > 0:
                    counts["bytes_reclaimed"] += size
            elif status == "skipped":
                counts["skipped"] += 1
                reason = str(item.get("reason") or "skipped")[:64]
                counts["skipped_reasons"][reason] = counts["skipped_reasons"].get(reason, 0) + 1
        return counts, True

    def finalize_stale(self, *, now: datetime, default_timeout_seconds: float = 1800) -> list[dict]:
        """Close ``running`` records the init-system backstop left open."""
        finalized = []
        for record in self._all_runs():
            if record.get("outcome") != "running":
                continue
            started = parse_iso(record.get("started_at"))
            bound = record.get("timeout_seconds")
            if isinstance(bound, bool) or not isinstance(bound, (int, float)) or bound <= 0:
                bound = default_timeout_seconds
            if started is not None and now - started <= timedelta(seconds=bound + BACKSTOP_SECONDS):
                continue
            counts, present = self._manifest_counts(record["run_id"])
            record.update(counts)
            record.update({
                "outcome": "timed_out",
                "reason": "run_bound_exceeded",
                "ended_at": utc_iso(now),
                "manifest": manifest_reference(record["run_id"]) if present else None,
            })
            self._write(self._run_path(record["run_id"]), record)
            finalized.append(record)
        return finalized


__all__ = ["BACKSTOP_SECONDS", "RETAINED_RUNS", "RoutineStore", "default_root",
           "manifest_reference", "parse_iso", "utc_iso"]
