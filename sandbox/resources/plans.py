from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Callable, Iterable

from .models import CleanupPlan, CleanupRun, StorageTarget, utc_now


_PLAN_ID = re.compile(r"^[a-f0-9]{32}$")


class ResourcePlanError(RuntimeError):
    def __init__(self, message: str, code: str = "invalid_plan") -> None:
        super().__init__(message)
        self.code = code


class PlanStore:
    def __init__(self, root: Path, *, clock: Callable = utc_now) -> None:
        self.root = Path(root)
        self.clock = clock

    def _path(self, plan_id: str) -> Path:
        if not isinstance(plan_id, str) or not _PLAN_ID.fullmatch(plan_id):
            raise ResourcePlanError("plan id is invalid", "invalid_plan_id")
        return self.root / f"{plan_id}.json"

    @contextmanager
    def _locked(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass
        lock = self.root / ".lock"
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            try:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def _read(self, path: Path) -> CleanupPlan:
        try:
            with path.open(encoding="utf-8") as handle:
                value = json.load(handle)
            return CleanupPlan.from_dict(value)
        except FileNotFoundError as exc:
            raise ResourcePlanError("plan was not found", "plan_not_found") from exc
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ResourcePlanError("plan record is invalid", "invalid_plan") from exc

    def _write(self, plan: CleanupPlan) -> CleanupPlan:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{plan.plan_id}.", suffix=".tmp", dir=self.root,
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(plan.to_dict(), handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self._path(plan.plan_id))
            try:
                directory = os.open(self.root, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            except OSError:
                pass
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
        return plan

    def save(self, plan: CleanupPlan) -> CleanupPlan:
        with self._locked():
            path = self._path(plan.plan_id)
            if path.exists():
                raise ResourcePlanError("plan already exists", "plan_already_exists")
            return self._write(plan)

    def load(self, plan_id: str) -> CleanupPlan:
        with self._locked():
            return self._read(self._path(plan_id))

    def begin(self, plan_id: str, target: StorageTarget,
              *, expected_scopes: Iterable[str] | None = None) -> CleanupPlan:
        with self._locked():
            path = self._path(plan_id)
            plan = self._read(path)
            if expected_scopes is not None and plan.scope not in frozenset(expected_scopes):
                raise ResourcePlanError(
                    "plan kind does not match the selected cleanup operation",
                    "plan_kind_mismatch",
                )
            if plan.target != target:
                raise ResourcePlanError(
                    "plan target does not match the selected host",
                    "plan_target_mismatch",
                )
            if plan.state != "planned":
                code = (
                    "plan_indeterminate" if plan.state == "indeterminate"
                    else "plan_already_used"
                )
                raise ResourcePlanError("plan was already used", code)
            if self.clock() >= plan.expires_at:
                self._write(replace(plan, state="expired"))
                raise ResourcePlanError("plan expired", "plan_expired")
            started = replace(plan, state="in_progress")
            return self._write(started)

    def finish(self, plan_id: str, state: str) -> CleanupPlan:
        if state not in {"completed", "indeterminate"}:
            raise ResourcePlanError("invalid terminal plan state", "invalid_plan_state")
        with self._locked():
            plan = self._read(self._path(plan_id))
            if plan.state != "in_progress":
                raise ResourcePlanError("plan is not in progress", "plan_not_in_progress")
            return self._write(replace(plan, state=state))

    def record_run(self, run: CleanupRun) -> Path:
        """Persist one immutable owner-only cleanup receipt atomically."""
        with self._locked():
            target = self.root / f"{run.run_id}.run.json"
            if target.exists():
                raise ResourcePlanError("cleanup receipt already exists", "run_already_exists")
            descriptor, temp_name = tempfile.mkstemp(
                prefix=f".{run.run_id}.", suffix=".tmp", dir=self.root,
            )
            try:
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(
                        run.to_dict(),
                        handle,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_name, target)
            except Exception:
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass
                raise
            return target
