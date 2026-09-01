"""Owner-only target locking and atomic recovery state commits."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from sandbox.core._paths import RUNTIME_DIR
from sandbox.core._hosting import target_mutation_capability
from sandbox.hosting.images.staging_models import (
    AtomicHostStateEvidence, DurableTerminalAuthorityEvidence, staging_digest,
)

from .models import (
    MAX_PHASES, MAX_RECEIPT_BYTES, RESULT_CLASSES, RESULT_FAMILIES,
    RecoveryAction, RecoveryRequest, RecoveryResult,
)


MAX_ATTEMPTS = 64
MAX_TOMBSTONES = 4096


class RecoveryRepository:
    def __init__(self, state_path: str | Path | None = None,
                 lock_dir: str | Path | None = None) -> None:
        raw_state = Path(state_path or (RUNTIME_DIR / "hosts.json")).expanduser()
        raw_locks = Path(lock_dir or (RUNTIME_DIR / "hosting" / "locks")).expanduser()
        self.state_path = raw_state.absolute()
        self.lock_dir = raw_locks.absolute()

    def load(self) -> dict:
        if not self.state_path.exists() and not self.state_path.is_symlink():
            return {"version": 1, "hosts": {}}
        self._ensure_state_parent(create=False)
        descriptor = self._open_owned_file(self.state_path, create=False,
                                           label="managed-host state")
        try:
            with os.fdopen(descriptor, "r") as handle:
                value = json.load(handle)
        except (OSError, ValueError, TypeError):
            raise ValueError("invalid managed-host state format") from None
        # Feature 047 upgrades this shared document to v2. Recovery owns only
        # its fields inside each host record, so sibling image state remains
        # opaque and cannot become recovery authority.
        if value.get("version") not in {1, 2} or not isinstance(value.get("hosts"), dict):
            raise ValueError("invalid managed-host state format")
        return value

    @contextmanager
    def target_lock(self, target_key: str, *, timeout_seconds: float = 30):
        with self.effect_lock(target_key, timeout_seconds=timeout_seconds):
            with self.state_lock(timeout_seconds=timeout_seconds):
                yield

    @contextmanager
    def effect_lock(self, target_key: str, *, timeout_seconds: float = 30):
        digest = hashlib.sha256(target_key.encode()).hexdigest()
        self._ensure_owned_directory(self.lock_dir)
        path = self.lock_dir / f"{digest}.lock"
        # hosts.json is one shared document. Hold its transaction lock for the
        # whole read/modify/write interval so different targets cannot replace
        # each other's state from stale snapshots.
        descriptor = self._open_owned_file(path, create=True, label="recovery effect lock")
        deadline = time.monotonic() + timeout_seconds
        try:
            self._acquire(descriptor, deadline)
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    @contextmanager
    def state_lock(self, *, timeout_seconds: float = 30):
        self._ensure_owned_directory(self.lock_dir)
        path = self.lock_dir / "state.lock"
        descriptor = self._open_owned_file(path, create=True, label="recovery state lock")
        deadline = time.monotonic() + timeout_seconds
        try:
            self._acquire(descriptor, deadline)
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    @staticmethod
    def _acquire(descriptor: int, deadline: float) -> None:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("operation_busy")
                time.sleep(0.02)

    @staticmethod
    def _owned_mode(info: os.stat_result, *, directory: bool) -> bool:
        expected_type = stat.S_ISDIR if directory else stat.S_ISREG
        return (expected_type(info.st_mode) and info.st_uid == os.geteuid() and
                stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600) and
                (info.st_nlink >= 1 if directory else info.st_nlink == 1))

    @staticmethod
    def _walk_existing_parents(path: Path) -> None:
        """Reject every symlink/non-directory before creating a managed child."""
        absolute = path.absolute()
        current = Path(absolute.anchor)
        for part in absolute.parts[1:]:
            current = current / part
            if not current.exists() and not current.is_symlink():
                return
            try:
                info = current.lstat()
            except OSError:
                raise ValueError("recovery parent path is unsafe") from None
            if (stat.S_ISLNK(info.st_mode) and
                    str(current) in {"/var", "/tmp"} and info.st_uid == 0):
                # Canonical macOS compatibility aliases. User-controlled
                # symlinked parents remain strictly non-authorizing.
                continue
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError("recovery parent path is unsafe")

    def _ensure_state_parent(self, *, create: bool) -> None:
        path = self.state_path.parent
        self._walk_existing_parents(path)
        if not path.exists():
            if not create:
                raise ValueError("managed-host state parent is unsafe")
            self._ensure_owned_directory(path)
            return
        info = path.lstat()
        # Existing Sandbox runtime directories historically use 0755. They are
        # compatible when controller-owned and not group/world writable; the
        # managed file itself remains exact owner-only 0600.
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or
                stat.S_IMODE(info.st_mode) & 0o022):
            raise ValueError("managed-host state parent is unsafe")

    def _ensure_owned_directory(self, path: Path) -> None:
        self._walk_existing_parents(path)
        if path.exists() or path.is_symlink():
            try:
                info = path.lstat()
            except OSError:
                raise ValueError("recovery lock directory is unsafe") from None
            if not self._owned_mode(info, directory=True):
                raise ValueError("recovery lock directory is unsafe")
            return
        parent = path.parent
        if parent != path and not parent.exists():
            self._ensure_owned_directory(parent)
        created = False
        try:
            path.mkdir(mode=0o700)
            created = True
        except FileExistsError:
            pass
        info = path.lstat()
        if not self._owned_mode(info, directory=True):
            raise ValueError("recovery lock directory is unsafe")
        if created:
            parent = os.open(
                path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
                getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
            try:
                os.fsync(parent)
            finally:
                os.close(parent)

    def _open_owned_file(self, path: Path, *, create: bool, label: str) -> int:
        existed = path.exists() or path.is_symlink()
        flags = os.O_RDWR if create else os.O_RDONLY
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        if create:
            flags |= os.O_CREAT
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError:
            raise ValueError(f"{label} is unsafe") from None
        try:
            info = os.fstat(descriptor)
            if not self._owned_mode(info, directory=False):
                raise ValueError(f"{label} is unsafe")
            if create and not existed:
                os.fsync(descriptor)
                parent = os.open(
                    path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
                    getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
                try:
                    os.fsync(parent)
                finally:
                    os.close(parent)
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def target(self, state: dict, target_key: str) -> dict:
        record = state["hosts"].setdefault(target_key, {})
        generation = record.setdefault("generation", 0)
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
            raise ValueError("invalid hosting generation")
        attempts = record.setdefault("recovery_attempts", [])
        tombstones = record.setdefault("recovery_tombstones", {})
        if not isinstance(attempts, list) or len(attempts) > MAX_ATTEMPTS:
            raise ValueError("invalid recovery attempt state")
        if not isinstance(tombstones, dict) or len(tombstones) > MAX_TOMBSTONES:
            raise ValueError("invalid recovery tombstone state")
        attempts[:] = [self._validated_terminal(item, tombstone=False)
                       for item in attempts]
        validated_tombstones = {}
        for identity, item in tombstones.items():
            safe = self._validated_terminal(item, tombstone=True)
            if identity != safe["request_id"]:
                raise ValueError("invalid recovery tombstone state")
            validated_tombstones[identity] = safe
        record["recovery_tombstones"] = tombstones = validated_tombstones
        ids = [item["request_id"] for item in attempts]
        if len(ids) != len(set(ids)) or set(ids) & set(tombstones):
            raise ValueError("duplicate recovery identity")
        terminals = [*attempts, *tombstones.values()]
        if any((item["result_family"] == "success" and
                item["generation"]["resulting"] > generation) or
               (item["result_class"] == "effect_unknown" and
                item["generation"]["resulting"] != generation)
               for item in terminals):
            raise ValueError("invalid recovery terminal generation")
        uncertainty = record.get("recovery_uncertainty")
        if uncertainty is not None and not self._valid_uncertainty(uncertainty):
            raise ValueError("invalid recovery uncertainty")
        active = record.get("active_operation")
        if active is not None and not self._valid_active(active):
            raise ValueError("invalid active recovery operation")
        if active is not None and active["expected_generation"] != generation:
            raise ValueError("invalid active recovery operation")
        terminal_ids = {item["request_id"] for item in terminals}
        terminal_digests = {item["request_digest"] for item in terminals}
        provisional = record.get("recovery_provisional")
        if provisional is not None and not self._valid_provisional(provisional):
            raise ValueError("invalid recovery provisional state")
        if provisional is not None and provisional["expected_generation"] != generation:
            raise ValueError("invalid recovery provisional state")
        if provisional is not None and (
                provisional["request_id"] in terminal_ids or
                provisional["request_digest"] in terminal_digests):
            raise ValueError("invalid recovery provisional state")
        if active is not None and (
                active["request_id"] in terminal_ids or
                active["request_digest"] in terminal_digests):
            raise ValueError("invalid active recovery operation")
        provisional_phase = (isinstance(active, dict) and
                             active.get("phase") == "reconciliation_provisional")
        if provisional_phase != (provisional is not None):
            raise ValueError("invalid recovery provisional state")
        if provisional_phase and (
                provisional.get("request_id") != active.get("request_id") or
                provisional.get("request_digest") != active.get("request_digest") or
                provisional.get("expected_generation") !=
                active.get("expected_generation")):
            raise ValueError("invalid recovery provisional state")
        if active is not None and uncertainty is not None:
            raise ValueError("invalid recovery uncertainty")
        if uncertainty is not None:
            terminal = next((item for item in [*attempts, *tombstones.values()]
                             if item["request_id"] == uncertainty["request_id"]), None)
            if (terminal is None or terminal["request_digest"] != uncertainty["request_digest"] or
                    terminal["result_class"] != "effect_unknown" or
                    terminal["generation"]["resulting"] != uncertainty["generation"]):
                raise ValueError("invalid recovery uncertainty")
        return record

    @classmethod
    def _validated_terminal(cls, value: object, *, tombstone: bool) -> dict:
        """Return the exact safe replay schema; persisted extras never escape."""
        required = {"ok", "schema_version", "action", "result_family", "result_class",
                    "request_id", "request_digest", "original", "target", "generation",
                    "effect_scope", "evidence", "phases", "completed_at"}
        optional = {"accepted_at", "started_at"}
        if tombstone:
            required.add("effect_unknown")
        try:
            encoded = json.dumps(
                value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        except (TypeError, ValueError):
            raise ValueError("invalid recovery tombstone state" if tombstone else
                             "invalid recovery attempt state") from None
        if (not isinstance(value, dict) or not required <= set(value) or
                set(value) - required - optional or len(encoded) > MAX_RECEIPT_BYTES):
            raise ValueError("invalid recovery tombstone state" if tombstone else
                             "invalid recovery attempt state")
        action = value.get("action")
        scope = value.get("effect_scope")
        expected_scope = {RecoveryAction.OBSERVE_RECONCILE.value: "receipt_only",
                          RecoveryAction.CONTINUE_EDGE.value: "edge_only"}.get(action)
        family = value.get("result_family")
        result_class = value.get("result_class")
        original = value.get("original")
        target = value.get("target")
        generation = value.get("generation")
        evidence = value.get("evidence")
        phases = value.get("phases")
        timestamps = [value.get("completed_at")]
        timestamps.extend(value[name] for name in optional if name in value)
        valid_success = ((action == RecoveryAction.OBSERVE_RECONCILE.value and
                          result_class == "observation_reconciled") or
                         (action == RecoveryAction.CONTINUE_EDGE.value and
                          result_class == "edge_only_completed"))
        valid_effect_unknown = (action == RecoveryAction.CONTINUE_EDGE.value and
                                family == "uncertain" and result_class == "effect_unknown")
        expected_family = (
            "success" if result_class in {"observation_reconciled", "edge_only_completed"}
            else "uncertain" if result_class == "effect_unknown"
            else "failed" if result_class in {
                "observation_failed", "edge_failed", "persistence_failed"}
            else "refused")
        valid = (
            value.get("schema_version") == 1 and expected_scope == scope and
            family in RESULT_FAMILIES and result_class in RESULT_CLASSES and
            family == expected_family and
            value.get("ok") is (family == "success") and
            cls._valid_id(value.get("request_id")) and
            cls._valid_digest(value.get("request_digest")) and
            isinstance(original, dict) and set(original) == {"job_id", "request_id"} and
            cls._valid_id(original.get("job_id")) and cls._valid_id(original.get("request_id")) and
            isinstance(target, dict) and set(target) == {"remote", "project", "environment"} and
            all(cls._valid_id(target.get(name)) for name in target) and
            isinstance(generation, dict) and set(generation) == {"expected", "resulting"} and
            all(isinstance(generation.get(name), int) and
                not isinstance(generation.get(name), bool) and generation[name] >= 0
                for name in generation) and
            isinstance(evidence, dict) and set(evidence) == {"id", "complete", "expires_at"} and
            (evidence.get("id") is None or cls._valid_digest(evidence.get("id"))) and
            evidence.get("complete") is (evidence.get("id") is not None) and
            (evidence.get("expires_at") is None or
             (isinstance(evidence.get("expires_at"), int) and
              not isinstance(evidence.get("expires_at"), bool) and
              evidence.get("expires_at") >= 0)) and
            isinstance(phases, list) and len(phases) <= MAX_PHASES and
            all(isinstance(item, dict) and set(item) == {"phase", "state"} and
                cls._valid_id(item.get("phase")) and cls._valid_id(item.get("state"))
                for item in phases) and
            all(isinstance(item, int) and not isinstance(item, bool) and item >= 0
                for item in timestamps) and
            (("accepted_at" in value) == ("started_at" in value)) and
            ((family == "success" and valid_success and
              generation["resulting"] == generation["expected"] + 1 and
              evidence.get("id") is not None) or
             (family != "success" and result_class not in {
                 "observation_reconciled", "already_reconciled", "edge_only_completed"} and
              generation["resulting"] == generation["expected"])) and
            (family == "uncertain") is valid_effect_unknown)
        if tombstone:
            valid = valid and value.get("effect_unknown") is (result_class == "effect_unknown") and not phases
        if not valid:
            raise ValueError("invalid recovery tombstone state" if tombstone else
                             "invalid recovery attempt state")
        safe = {name: value[name] for name in required | optional if name in value}
        return json.loads(json.dumps(
            safe, sort_keys=True, separators=(",", ":"), ensure_ascii=True))

    @staticmethod
    def _valid_uncertainty(value: object) -> bool:
        required = {"schema_version", "request_id", "request_digest", "action",
                    "generation", "effect_scope"}
        return (isinstance(value, dict) and set(value) == required and
                value.get("schema_version") == 1 and
                RecoveryRepository._valid_id(value.get("request_id")) and
                RecoveryRepository._valid_digest(value.get("request_digest")) and
                value.get("action") == RecoveryAction.CONTINUE_EDGE.value and
                isinstance(value.get("generation"), int) and
                not isinstance(value.get("generation"), bool) and
                value.get("generation") >= 0 and value.get("effect_scope") == "edge_only")

    @staticmethod
    def _valid_active(value: object) -> bool:
        if (not isinstance(value, dict) or value.get("schema_version") != 1 or
                not RecoveryRepository._valid_id(value.get("request_id")) or
                not RecoveryRepository._valid_digest(value.get("request_digest")) or
                value.get("action") not in {item.value for item in RecoveryAction}):
            return False
        generation = value.get("expected_generation")
        if (not isinstance(generation, int) or isinstance(generation, bool) or
                generation < 0):
            return False
        action = value.get("action")
        phase = value.get("phase")
        entered = value.get("effect_entered")
        required = {"schema_version", "request_id", "request_digest", "action",
                    "expected_generation", "accepted_at", "started_at", "phase",
                    "effect_entered"}
        if phase == "effect_entered":
            required.add("effect_entered_at")
        if set(value) != required or any(
                not isinstance(value.get(name), int) or
                isinstance(value.get(name), bool) or value.get(name) < 0
                for name in ("accepted_at", "started_at")):
            return False
        if ("effect_entered_at" in required and
                (not isinstance(value.get("effect_entered_at"), int) or
                 isinstance(value.get("effect_entered_at"), bool) or
                 value.get("effect_entered_at") < 0)):
            return False
        return ((action == RecoveryAction.OBSERVE_RECONCILE.value and
                 phase in {"observation_pending", "reconciliation_provisional"} and
                 entered is False) or
                (action == RecoveryAction.CONTINUE_EDGE.value and
                 ((phase == "edge_pending" and entered is False) or
                  (phase == "effect_entered" and entered is True))))

    @staticmethod
    def _valid_id(value: object) -> bool:
        return (isinstance(value, str) and
                re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}", value) is not None)

    @staticmethod
    def _valid_digest(value: object) -> bool:
        return (isinstance(value, str) and
                re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None)

    @staticmethod
    def _valid_provisional(value: object) -> bool:
        generation = value.get("expected_generation") if isinstance(value, dict) else None
        created = value.get("created_at") if isinstance(value, dict) else None
        required = {"schema_version", "request_id", "request_digest",
                    "operation_digest", "evidence_id", "expected_generation",
                    "authorizing", "created_at"}
        return (isinstance(value, dict) and set(value) == required and
                value.get("schema_version") == 1 and
                RecoveryRepository._valid_id(value.get("request_id")) and
                RecoveryRepository._valid_digest(value.get("request_digest")) and
                RecoveryRepository._valid_digest(value.get("operation_digest")) and
                RecoveryRepository._valid_digest(value.get("evidence_id")) and
                isinstance(generation, int) and not isinstance(generation, bool) and
                generation >= 0 and value.get("authorizing") is False and
                isinstance(created, int) and not isinstance(created, bool) and created >= 0 and
                len(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()) <= 2048)

    @classmethod
    def replay(cls, record: dict, request: RecoveryRequest) -> dict | None:
        for attempt in record.get("recovery_attempts", []):
            if attempt.get("request_id") == request.request_id:
                attempt = cls._validated_terminal(attempt, tombstone=False)
                if attempt.get("request_digest") != request.digest:
                    raise ValueError("binding_mismatch")
                return cls._public_terminal(attempt)
        tombstone = (record.get("recovery_tombstones") or {}).get(request.request_id)
        if tombstone is not None:
            tombstone = cls._validated_terminal(tombstone, tombstone=True)
            if tombstone.get("request_digest") != request.digest:
                raise ValueError("binding_mismatch")
            return cls._public_terminal(tombstone)
        return None

    @staticmethod
    def _public_terminal(value: dict) -> dict:
        public = {key: item for key, item in value.items() if key != "effect_unknown"}
        return json.loads(json.dumps(
            public, sort_keys=True, separators=(",", ":"), ensure_ascii=True))

    def begin(self, state: dict, target_key: str, request: RecoveryRequest) -> None:
        record = self.target(state, target_key)
        active = record.get("active_operation")
        if isinstance(active, dict):
            same_observation_resume = (
                request.action is RecoveryAction.OBSERVE_RECONCILE and
                active.get("request_id") == request.request_id and
                active.get("request_digest") == request.digest and
                active.get("action") == request.action.value and
                active.get("phase") == "observation_pending" and
                active.get("effect_entered") is False)
            if same_observation_resume:
                return
            raise ValueError("operation_busy")
        if record["generation"] != request.expected_generation:
            raise ValueError("generation_conflict")
        if (len(record["recovery_attempts"]) >= MAX_ATTEMPTS and
                len(record["recovery_tombstones"]) >= MAX_TOMBSTONES):
            raise ValueError("retention_full")
        now = int(time.time())
        record["active_operation"] = {
            "schema_version": 1,
            "request_id": request.request_id,
            "request_digest": request.digest,
            "action": request.action.value,
            "expected_generation": request.expected_generation,
            "accepted_at": now,
            "started_at": now,
            "phase": ("observation_pending" if
                      request.action is RecoveryAction.OBSERVE_RECONCILE else "edge_pending"),
            "effect_entered": False,
        }
        self._write(state)

    def mark_effect_entered(self, state: dict, target_key: str,
                            request: RecoveryRequest) -> None:
        record = self.target(state, target_key)
        active = record.get("active_operation")
        if (not isinstance(active, dict) or active.get("request_id") != request.request_id or
                active.get("request_digest") != request.digest or
                active.get("action") != RecoveryAction.CONTINUE_EDGE.value or
                active.get("phase") != "edge_pending" or active.get("effect_entered") is not False):
            raise ValueError("operation_busy")
        active["phase"] = "effect_entered"
        active["effect_entered"] = True
        active["effect_entered_at"] = int(time.time())
        self._write(state)

    def provision_reconciliation(self, state: dict, target_key: str,
                                 request: RecoveryRequest, *, operation_digest: str,
                                 evidence_id: str) -> None:
        record = self.target(state, target_key)
        active = record.get("active_operation")
        if (not isinstance(active, dict) or active.get("request_id") != request.request_id or
                active.get("request_digest") != request.digest or
                active.get("action") != RecoveryAction.OBSERVE_RECONCILE.value or
                active.get("phase") != "observation_pending" or
                active.get("effect_entered") is not False or
                record["generation"] != request.expected_generation or
                not self._valid_digest(operation_digest) or
                not self._valid_digest(evidence_id)):
            raise ValueError("operation_busy")
        provisional = {
            "schema_version": 1,
            "request_id": request.request_id,
            "request_digest": request.digest,
            "operation_digest": operation_digest,
            "evidence_id": evidence_id,
            "expected_generation": request.expected_generation,
            "authorizing": False,
            "created_at": int(time.time()),
        }
        record["recovery_provisional"] = provisional
        active["phase"] = "reconciliation_provisional"
        self._write(state)

    def commit(self, state: dict, target_key: str, request: RecoveryRequest,
               result: RecoveryResult, *, receipt: dict | None = None) -> dict:
        record = self.target(state, target_key)
        active = record.get("active_operation")
        if (isinstance(active, dict) and
                (active.get("request_id") != request.request_id or
                 active.get("request_digest") != request.digest)):
            raise ValueError("operation_busy")
        if record["generation"] != request.expected_generation:
            raise ValueError("generation_conflict")
        replay = self.replay(record, request)
        if replay is not None:
            return replay
        payload = result.as_dict()
        if isinstance(active, dict):
            payload["accepted_at"] = active.get("accepted_at")
            payload["started_at"] = active.get("started_at")
        payload["completed_at"] = int(time.time())
        safe = self._validated_terminal(payload, tombstone=False)
        if (safe["request_id"] != request.request_id or
                safe["request_digest"] != request.digest or
                safe["action"] != request.action.value or
                safe["effect_scope"] != request.effect_scope or
                safe["target"] != request.target.as_dict() or
                safe["generation"]["expected"] != request.expected_generation):
            raise ValueError("invalid recovery attempt state")
        if safe["ok"]:
            record["generation"] = safe["generation"]["resulting"]
            if receipt is not None:
                record["recovery_receipt"] = receipt
                record["consumed_observation_authority"] = {
                    "schema_version": 1,
                    "operation_digest": receipt.get("operation_digest"),
                    "request_id": request.request_id,
                    "starting_generation": request.expected_generation,
                    "resulting_generation": safe["generation"]["resulting"],
                }
        if (isinstance(active, dict) and
                active.get("request_id") == request.request_id and
                active.get("request_digest") == request.digest):
            record.pop("recovery_provisional", None)
        if safe["result_class"] == "effect_unknown":
            # A different request must never repeat an effect whose outcome is
            # unknown. Only a future explicit reconciliation authority may
            # clear this target-level fence.
            record["recovery_uncertainty"] = {
                "schema_version": 1,
                "request_id": request.request_id,
                "request_digest": request.digest,
                "action": request.action.value,
                "generation": record["generation"],
                "effect_scope": request.effect_scope,
            }
        record["recovery_attempts"].append(safe)
        while len(record["recovery_attempts"]) > MAX_ATTEMPTS:
            old = record["recovery_attempts"].pop(0)
            if len(record["recovery_tombstones"]) >= MAX_TOMBSTONES:
                raise ValueError("retention_full")
            tombstone = {
                "schema_version": 1,
                "ok": old["result_family"] == "success",
                "request_id": old["request_id"],
                "request_digest": old["request_digest"],
                "action": old["action"],
                "effect_scope": old["effect_scope"],
                "result_family": old["result_family"],
                "result_class": old["result_class"],
                "generation": old["generation"],
                "original": old.get("original"),
                "target": old.get("target"),
                "evidence": old.get("evidence"),
                "phases": [],
                "effect_unknown": old["result_class"] == "effect_unknown",
                "completed_at": old.get("completed_at"),
            }
            for name in ("accepted_at", "started_at"):
                if name in old:
                    tombstone[name] = old[name]
            record["recovery_tombstones"][old["request_id"]] = tombstone
        if (isinstance(active, dict) and
                active.get("request_id") == request.request_id and
                active.get("request_digest") == request.digest):
            record["active_operation"] = None
        self._write(state)
        return self._public_terminal(safe)

    def target_mutation_port(self, capability: str, *, timeout_seconds: float = 30):
        """Return the shared target owner for one registered capability."""
        return _TargetMutationPort(self, capability, timeout_seconds=timeout_seconds)

    def activation_host_state_port(self):
        """Return the only Feature 051 outer hosts.json transaction port."""
        return _ActivationHostStatePort(self)

    def _write(self, state: dict) -> None:
        self._ensure_state_parent(create=True)
        if self.state_path.exists() or self.state_path.is_symlink():
            descriptor = self._open_owned_file(
                self.state_path, create=False, label="managed-host state")
            os.close(descriptor)
        descriptor, temporary = tempfile.mkstemp(
            prefix="hosts-", suffix=".json", dir=self.state_path.parent)
        try:
            with os.fdopen(descriptor, "w") as handle:
                json.dump(state, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.state_path)
            parent = os.open(self.state_path.parent, os.O_RDONLY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise


class _TargetMutationPort:
    def __init__(self, repository: RecoveryRepository, capability: str,
                 *, timeout_seconds: float = 30) -> None:
        self.repository = repository
        self.capability = capability
        self.capability_revision = target_mutation_capability(capability)
        self.timeout_seconds = timeout_seconds
        self._owned_target = None
        self._ownership_depth = 0

    @contextmanager
    def target_mutation_transaction(self, target_identity: str):
        # Membership was checked before any lock or state path was opened.
        target_mutation_capability(self.capability)
        if self._ownership_depth:
            if self._owned_target != target_identity:
                raise ValueError("operation_busy")
            self._ownership_depth += 1
            try:
                yield self
            finally:
                self._ownership_depth -= 1
            return
        with self.repository.effect_lock(target_identity, timeout_seconds=self.timeout_seconds):
            self._owned_target = target_identity
            self._ownership_depth = 1
            try:
                yield self
            finally:
                self._ownership_depth = 0
                self._owned_target = None


class _ActivationHostStatePort:
    """Narrow nested read/CAS/atomic commit port held under state.lock.

    The activation package provides only a validated nested candidate. This
    object alone loads and writes the outer document and preserves every
    unknown/legacy sibling field.
    """

    def __init__(self, repository: RecoveryRepository) -> None:
        self.repository = repository
        self._state = None
        self._target = None

    @contextmanager
    def atomic_host_state_transaction(self, target_identity: str):
        if self._state is not None:
            raise ValueError("operation_busy")
        with self.repository.state_lock():
            self._state = self.repository.load()
            self._target = target_identity
            try:
                yield self
            finally:
                self._state = None
                self._target = None

    def _record(self, target_identity: str) -> dict:
        if self._state is None or self._target != target_identity:
            raise ValueError("operation_busy")
        hosts = self._state.setdefault("hosts", {})
        record = hosts.setdefault(target_identity, {})
        generation = record.setdefault("generation", 0)
        if type(generation) is not int or generation < 0:
            raise ValueError("generation_conflict")
        return record

    @staticmethod
    def activation_acceptance_receipt(target_identity: str, *, holder: str,
                                      request_id: str, request_digest: str,
                                      proof_digest: str) -> str:
        body = {"target_identity": target_identity, "holder": holder,
                "request_id": request_id, "request_digest": request_digest,
                "proof_digest": proof_digest}
        return "host-acceptance/" + hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def read_activation_nested(self, target_identity: str) -> dict | None:
        record = self._record(target_identity)
        value = record.get("image_activation")
        if value is None:
            from sandbox.hosting.images.activation.repository import empty_activation_state
            value = empty_activation_state()
            value["generation"] = record["generation"]
        return json.loads(json.dumps(value))

    def compare_and_commit_activation(self, target_identity: str, *, expected_generation: int,
                                      candidate: dict, holder: str, request_id: str,
                                      request_digest: str, proof_digest: str,
                                      acceptance_receipt: str) -> AtomicHostStateEvidence:
        from sandbox.hosting.images.activation.repository import encode_activation_state
        record = self._record(target_identity)
        if record["generation"] != expected_generation:
            raise ValueError("generation_conflict")
        safe = encode_activation_state(candidate)
        if safe["generation"] != expected_generation:
            raise ValueError("generation_conflict")
        active = safe.get("active")
        pin = active.get("proof_pin") if isinstance(active, dict) else None
        if (not isinstance(active, dict) or active.get("holder") != holder or
                active.get("request_id") != request_id or
                active.get("request_digest") != request_digest or
                not isinstance(pin, dict) or pin.get("proof_digest") != proof_digest or
                pin.get("host_acceptance_receipt") != acceptance_receipt):
            raise ValueError("binding_mismatch")
        record["image_activation"] = safe
        self.repository._write(self._state)
        return self._accepted_evidence(holder, request_id, request_digest, proof_digest,
                                       acceptance_receipt)

    def lookup_activation_acceptance(self, target_identity: str, *, holder: str,
                                     request_id: str, request_digest: str,
                                     proof_digest: str) -> AtomicHostStateEvidence:
        record = self._record(target_identity)
        nested = record.get("image_activation") or {}
        active = nested.get("active")
        result = (nested.get("results") or {}).get(request_id)
        if isinstance(result, dict) and isinstance(result.get("result"), dict):
            result = {**result["result"], "holder": result.get("holder"),
                      "proof_pin": result.get("proof_pin")}
        candidate = active if isinstance(active, dict) and active.get("request_id") == request_id else result
        if not isinstance(candidate, dict) or candidate.get("request_digest") != request_digest:
            return self._atomic_evidence(holder, request_id, request_digest, proof_digest,
                                         "absent", None)
        pin = candidate.get("proof_pin") or {}
        receipt = pin.get("host_acceptance_receipt")
        if candidate.get("holder", holder) != holder or pin.get("proof_digest") != proof_digest \
                or not isinstance(receipt, str):
            return self._atomic_evidence(holder, request_id, request_digest, proof_digest,
                                         "ambiguous", None)
        return self._accepted_evidence(holder, request_id, request_digest, proof_digest, receipt)

    def absent_activation_evidence(self, target_identity: str, *, holder: str,
                                   request_id: str, request_digest: str,
                                   proof_digest: str) -> AtomicHostStateEvidence:
        existing = self.lookup_activation_acceptance(
            target_identity, holder=holder, request_id=request_id,
            request_digest=request_digest, proof_digest=proof_digest)
        if existing.state == "accepted":
            return self._atomic_evidence(holder, request_id, request_digest, proof_digest,
                                         "ambiguous", None)
        return self._atomic_evidence(holder, request_id, request_digest, proof_digest,
                                     "absent", None)

    @staticmethod
    def _accepted_evidence(holder, request_id, request_digest, proof_digest, receipt):
        return _ActivationHostStatePort._atomic_evidence(
            holder, request_id, request_digest, proof_digest, "accepted", receipt)

    @staticmethod
    def _atomic_evidence(holder, request_id, request_digest, proof_digest, state, receipt):
        body = {"holder": holder, "activation_request_id": request_id,
                "activation_request_digest": request_digest, "proof_digest": proof_digest,
                "state": state, "acceptance_receipt": receipt}
        return AtomicHostStateEvidence(
            **body, evidence_digest=staging_digest(
                "sandbox.hosting.images.atomic-host-state-evidence.v1", body))

    def validate_atomic_host_state_evidence(self, evidence: object) -> bool:
        if type(evidence) is not AtomicHostStateEvidence:
            return False
        expected = self._atomic_evidence(
            evidence.holder, evidence.activation_request_id,
            evidence.activation_request_digest, evidence.proof_digest,
            evidence.state, evidence.acceptance_receipt)
        return expected == evidence

    def validate_durable_terminal_authority(self, evidence: object) -> bool:
        if type(evidence) is not DurableTerminalAuthorityEvidence:
            return False
        target = self._target
        if target is None:
            return False
        record = self._record(target)
        try:
            from sandbox.hosting.images.activation.repository import decode_activation_state
            nested = decode_activation_state(record.get("image_activation"))
        except (TypeError, ValueError):
            return False
        terminal = next((item for item in (nested.get("results") or {}).values()
                         if isinstance(item, dict) and isinstance(item.get("result"), dict)
                         and item["result"].get("transaction_digest") == evidence.terminal_receipt), None)
        if terminal is None:
            return False
        pin = terminal.get("proof_pin")
        return (isinstance(pin, dict) and terminal.get("holder") == evidence.holder
                and terminal.get("proof_digest") == evidence.proof_digest
                and pin.get("holder") == evidence.holder
                and pin.get("proof_digest") == evidence.proof_digest
                and pin.get("host_acceptance_receipt") == evidence.acceptance_receipt)

    def durable_terminal_authority_evidence(self, lease: object, *,
                                            terminal_receipt: str) -> DurableTerminalAuthorityEvidence:
        body = {"holder": lease.holder, "proof_digest": lease.proof_digest,
                "acceptance_receipt": lease.acceptance_receipt,
                "terminal_receipt": terminal_receipt}
        return DurableTerminalAuthorityEvidence(
            **body, evidence_digest=staging_digest(
                "sandbox.hosting.images.durable-terminal-authority.v1", body))

    def update_activation_nested(self, target_identity: str, expected_generation: int, update):
        record = self._record(target_identity)
        if record["generation"] != expected_generation:
            raise ValueError("generation_conflict")
        candidate = update(record.get("image_activation"))
        from sandbox.hosting.images.activation.repository import encode_activation_state
        safe = encode_activation_state(candidate)
        if safe["generation"] not in {expected_generation, expected_generation + 1}:
            raise ValueError("generation_conflict")
        record["image_activation"] = safe
        record["generation"] = safe["generation"]
        self.repository._write(self._state)
        return json.loads(json.dumps(safe))

    def store_activation_recovery_provisional(self, target_identity: str,
                                              expected_generation: int,
                                              provisional: dict) -> None:
        from sandbox.hosting.images.activation.repository import decode_activation_state, encode_activation_state
        record = self._record(target_identity)
        if record["generation"] != expected_generation:
            raise ValueError("generation_conflict")
        nested = decode_activation_state(record.get("image_activation"))
        existing = nested.get("recovery_provisional")
        if existing is not None and existing != provisional:
            raise ValueError("operation_busy")
        nested["recovery_provisional"] = json.loads(json.dumps(provisional))
        record["image_activation"] = encode_activation_state(nested)
        self.repository._write(self._state)

    def commit_activation_recovery_result(self, target_identity: str, expected_generation: int,
                                          request_id: str, request_digest: str, *,
                                          code: str, promote: bool, close_active: bool) -> dict:
        from sandbox.hosting.images.activation.models import ActivationResult, MAX_RESULTS, MAX_TOMBSTONES
        from sandbox.hosting.images.activation.repository import decode_activation_state, encode_activation_state
        record = self._record(target_identity)
        if record["generation"] != expected_generation:
            raise ValueError("generation_conflict")
        nested = decode_activation_state(record.get("image_activation"))
        existing = nested["recovery_results"].get(request_id)
        if existing is not None:
            if existing.get("request_digest") != request_digest:
                raise ValueError("binding_mismatch")
            return json.loads(json.dumps(existing))
        from sandbox.hosting.images.activation.models import MAX_RECOVERY_RESULTS
        if len(nested["recovery_results"]) >= MAX_RECOVERY_RESULTS:
            raise ValueError("retention_full")
        provisional = nested.get("recovery_provisional")
        if not isinstance(provisional, dict) or provisional.get("request_id") != request_id \
                or provisional.get("request_digest") != request_digest \
                or provisional.get("authorizing") is not False:
            raise ValueError("operation_busy")
        active = nested.get("active")
        activation_request_id = active.get("request_id") if isinstance(active, dict) else None
        if not isinstance(activation_request_id, str):
            raise ValueError("operation_busy")
        promoted = False
        if promote:
            candidate = active.get("candidate_generation") if isinstance(active, dict) else None
            if not isinstance(candidate, dict):
                code = "effect_unknown"
                close_active = False
            else:
                nested["previous"] = nested["current"]
                nested["current"] = candidate
                nested["generation"] = expected_generation + 1
                record["generation"] = expected_generation + 1
                promoted = True
        if code in {"committed", "recovery_no_effect"}:
            terminal = ActivationResult(
                1, code == "committed", "success" if code == "committed" else "refused",
                code, active["operation"], active["request_id"], active["request_digest"],
                active["starting_generation"], record["generation"], active["transaction_digest"],
                (active.get("candidate_generation") or {}).get("generation_digest") if promoted else None,
                (active.get("running_observation") or {}).get("observation_digest") if promoted else None)
            nested["results"][activation_request_id] = {
                "result": terminal.as_mapping(), "holder": active["holder"],
                "proof_digest": active["proof_pin"]["proof_digest"],
                "proof_pin": json.loads(json.dumps(active["proof_pin"]))}
            while len(nested["results"]) > MAX_RESULTS:
                compact_id = next(iter(nested["results"]))
                if compact_id == activation_request_id and len(nested["results"]) > 1:
                    compact_id = next(key for key in nested["results"] if key != activation_request_id)
                compact = nested["results"].pop(compact_id)["result"]
                if len(nested["tombstones"]) >= MAX_TOMBSTONES:
                    raise ValueError("retention_full")
                nested["tombstones"][compact_id] = {
                    "request_id": compact_id, "request_digest": compact["request_digest"],
                    "result_class": compact["result_class"], "code": compact["code"]}
        result = {"schema_version": 1,
                  "ok": code == "committed",
                  "request_id": request_id,
                  "activation_request_id": activation_request_id,
                  "request_digest": request_digest, "code": code,
                  "promoted": promoted, "starting_generation": expected_generation,
                  "resulting_generation": record["generation"]}
        nested["recovery_results"][request_id] = result
        nested["recovery_provisional"] = None
        if close_active or promoted:
            nested["active"] = None
        record["image_activation"] = encode_activation_state(nested)
        self.repository._write(self._state)
        return json.loads(json.dumps(result))
