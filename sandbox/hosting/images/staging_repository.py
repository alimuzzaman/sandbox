"""Owner-only durable ledger and ordered proof custody for image staging.

This repository never opens ``hosts.json`` and never reuses its lock. Proof
custody exists only while target mutation, atomic host-state, then this
stage-ledger target lock are held in that order.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from typing import Any, Iterator

from sandbox.core._paths import RUNTIME_DIR
from .staging_models import (
    AtomicHostStateEvidence, DurableTerminalAuthorityEvidence, MAX_LEDGER_BYTES,
    MAX_LIVE_PROOF_LEASES, MAX_PERSISTED_LEDGER_COUNTER, MAX_PROOFS,
    MAX_STAGE_FRAME_BYTES, MAX_TOMBSTONES,
    ProofCustodyPort, StageProofActivationLease, StageProofTombstone, StageRequest,
    StageResult, StagedImageProof, StagingContractError, canonical_bytes,
)
from .staging_v2 import StageRequestSet, StageResultSet, StagedImageProofSet

TERMINAL_PHASES = frozenset({"succeeded", "refused", "failed", "cancelled", "uncertain"})
EFFECT_PHASES = frozenset({"pulling", "cleanup_pending", "observing", "succeeded"})
TERMINAL_RESERVATION_BYTES = MAX_STAGE_FRAME_BYTES + 4096
STAGE_LEDGER_AUTHORITY = "feature-050-stage-ledger-v2"
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_RECORD_PHASES = frozenset({
    "accepted", "credential_pending", "helper_running", "pulling",
    "cleanup_pending", "observing", *TERMINAL_PHASES,
})
_RECORD_FIELDS = frozenset({
    "request_id", "request_digest", "generation", "phase", "effect_entered",
    "process", "cleanup", "ledger_revision", "result",
})
_OWNER_FIELDS = _RECORD_FIELDS - {"ledger_revision", "result"}
_PROCESS_FIELDS = frozenset({"unit_inactive", "cgroup_empty_or_removed"})
_BOOTSTRAP_FIELDS = frozenset({"bootstrap_phase", "bootstrap_code"})
_PROCESS_VARIANTS = frozenset({
    _PROCESS_FIELDS,
    _PROCESS_FIELDS | {"not_launched"},
    _PROCESS_FIELDS | {"cleanup_complete"},
    _PROCESS_FIELDS | {"unit_name"},
    _PROCESS_FIELDS | {"unit_name", "cgroup", "delegated", "escape_allowed"},
    _PROCESS_FIELDS | _BOOTSTRAP_FIELDS,
    _PROCESS_FIELDS | {"not_launched"} | _BOOTSTRAP_FIELDS,
})


def _bounded_text(value: object) -> bool:
    return type(value) is str and 0 < len(value) <= 512 \
        and not any(ord(char) < 32 or ord(char) == 127 for char in value)


def _validate_process(value: object) -> None:
    if value is None:
        return
    if type(value) is not dict or frozenset(value) not in _PROCESS_VARIANTS \
            or any(type(value[name]) is not bool for name in _PROCESS_FIELDS):
        raise ValueError
    if "unit_name" in value and not _bounded_text(value["unit_name"]):
        raise ValueError
    if "cgroup" in value and (not _bounded_text(value["cgroup"])
                              or value["unit_name"] not in value["cgroup"]):
        raise ValueError
    if "delegated" in value and (value["delegated"] is not False
                                 or value["escape_allowed"] is not False):
        raise ValueError
    if "not_launched" in value and value["not_launched"] is not True:
        raise ValueError
    if "cleanup_complete" in value and type(value["cleanup_complete"]) is not bool:
        raise ValueError
    if "bootstrap_phase" in value:
        allowed = {"inode": {"inode_os", "inode_json", "inode_key", "inode_exec"},
                   "plan": {"plan_invalid"}, "cgroup": {"cgroup_invalid"},
                   "workspace": {"workspace_invalid"},
                   "unknown": {"bootstrap_unavailable"}}
        if value["bootstrap_phase"] not in allowed \
                or value["bootstrap_code"] not in allowed[value["bootstrap_phase"]]:
            raise ValueError


def _validate_cleanup(value: object) -> None:
    if value is not None and (type(value) is not dict or set(value) != {"complete"}
                              or type(value["complete"]) is not bool):
        raise ValueError


def _validate_record_shape(request_id: str, record: object, *, generation: int,
                           ledger_revision: int) -> None:
    if type(record) is not dict or set(record) != _RECORD_FIELDS \
            or request_id != record["request_id"] \
            or _IDENTITY.fullmatch(request_id) is None \
            or type(record["request_digest"]) is not str \
            or _DIGEST.fullmatch(record["request_digest"]) is None \
            or type(record["generation"]) is not int \
            or not 1 <= record["generation"] <= MAX_PERSISTED_LEDGER_COUNTER \
            or record["generation"] > generation \
            or type(record["ledger_revision"]) is not int \
            or not 1 <= record["ledger_revision"] <= MAX_PERSISTED_LEDGER_COUNTER \
            or record["ledger_revision"] > ledger_revision \
            or record["phase"] not in _RECORD_PHASES \
            or type(record["effect_entered"]) is not bool:
        raise ValueError
    _validate_process(record["process"])
    _validate_cleanup(record["cleanup"])
    if record["cleanup"] is not None and record["process"] is None:
        raise ValueError
    if record["phase"] in {"accepted", "credential_pending"} \
            and (record["effect_entered"] or record["process"] is not None
                 or record["cleanup"] is not None):
        raise ValueError
    if record["phase"] == "helper_running" \
            and (record["effect_entered"] or record["process"] is None
                 or record["cleanup"] is not None):
        raise ValueError
    if record["phase"] in {"helper_running", "pulling"} \
            and (record["process"] is None or record["cleanup"] is not None):
        raise ValueError
    if record["phase"] in {"pulling", "cleanup_pending", "observing", "succeeded"} \
            and record["effect_entered"] is not True:
        raise ValueError
    if record["phase"] in {"cleanup_pending", "observing", "succeeded"} \
            and (record["process"] is None or record["cleanup"] is None):
        raise ValueError
    if record["phase"] in {"observing", "succeeded"} \
            and (record["process"].get("unit_inactive") is not True
                 or record["process"].get("cgroup_empty_or_removed") is not True
                 or record["cleanup"] != {"complete": True}):
        raise ValueError


class StageRepositoryError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _lease_from(raw: object) -> StageProofActivationLease:
    if type(raw) is not dict:
        raise StageRepositoryError("lease_conflict")
    try:
        return StageProofActivationLease(**raw)
    except (TypeError, ValueError):
        raise StageRepositoryError("lease_conflict") from None


def _proof_from(raw: object):
    if type(raw) is not dict:
        raise StageRepositoryError("ledger_invalid")
    try:
        if raw.get("schema_version") == 1:
            return StagedImageProof.from_mapping(raw)
        if raw.get("schema_version") == 2:
            return StagedImageProofSet.from_mapping(raw)
    except (TypeError, ValueError, StagingContractError):
        pass
    raise StageRepositoryError("ledger_invalid")


def _result_from(raw: object, request_id: str, proof=None):
    if type(raw) is not dict:
        raise StageRepositoryError("ledger_invalid")
    try:
        values = (raw["schema_version"], raw["ok"], raw["result_class"], raw["code"],
                  request_id, raw["generation"], proof)
        if raw.get("schema_version") == 1:
            return StageResult(*values)
        if raw.get("schema_version") == 2:
            return StageResultSet(*values)
    except (KeyError, TypeError, ValueError, StagingContractError):
        pass
    raise StageRepositoryError("ledger_invalid")


def _failure_for(request, result_class: str, code: str, generation: int):
    cls = StageResultSet if type(request) is StageRequestSet else StageResult
    version = 2 if cls is StageResultSet else 1
    return cls(version, False, result_class, code, request.request_id, generation)


class StageRepository:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or (RUNTIME_DIR / "hosting" / "image-staging")).expanduser().resolve(strict=False)
        self.ledger_dir = self.root / "ledgers"
        self.lock_dir = self.root / "locks"

    @staticmethod
    def _target_name(target_identity: str) -> str:
        if not isinstance(target_identity, str) or not target_identity:
            raise StageRepositoryError("target_invalid")
        return hashlib.sha256(target_identity.encode()).hexdigest()

    def _paths(self, target_identity: str) -> tuple[Path, Path]:
        name = self._target_name(target_identity)
        return self.ledger_dir / f"{name}.json", self.lock_dir / f"{name}.lock"

    def _ensure_owned_dir(self, path: Path) -> None:
        """Create owned storage without following any path component symlink."""
        try:
            path.relative_to(self.root)
        except ValueError:
            if path != self.root:
                raise StageRepositoryError("ledger_invalid") from None
        parts = path.parts[1:]
        owned_index = len(self.root.parts[1:]) - 1
        descriptor = os.open("/", os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            for index, component in enumerate(parts):
                flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
                try:
                    child = os.open(component, flags, dir_fd=descriptor)
                except FileNotFoundError:
                    if index < owned_index:
                        raise StageRepositoryError("ledger_invalid") from None
                    os.mkdir(component, 0o700, dir_fd=descriptor)
                    os.fsync(descriptor)
                    child = os.open(component, flags, dir_fd=descriptor)
                info = os.fstat(child)
                if index >= owned_index and (not stat.S_ISDIR(info.st_mode)
                        or info.st_uid != os.geteuid()
                        or stat.S_IMODE(info.st_mode) != 0o700):
                    os.close(child)
                    raise StageRepositoryError("ledger_invalid")
                os.close(descriptor)
                descriptor = child
        except OSError:
            raise StageRepositoryError("ledger_invalid") from None
        finally:
            os.close(descriptor)

    @contextmanager
    def target_lock(self, target_identity: str, *, timeout_seconds: float = 30) -> Iterator[None]:
        _ledger, lock = self._paths(target_identity)
        self._ensure_owned_dir(self.lock_dir)
        descriptor = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() \
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
            os.close(descriptor)
            raise StageRepositoryError("ledger_invalid")
        deadline = time.monotonic() + timeout_seconds
        try:
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("stage target lock unavailable")
                    time.sleep(0.02)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    @staticmethod
    def _empty(target_identity: str) -> dict[str, Any]:
        return {"schema_version": 2, "target_identity": target_identity, "generation": 0,
                "ledger_revision": 0, "active_owner": None, "reserved_terminal_bytes": 0,
                "records": {}, "proofs": {}, "tombstones": {}, "leases": {}}

    @staticmethod
    def _advance_counter(state: dict[str, Any], name: str) -> int:
        current = state[name]
        if type(current) is not int or not 0 <= current < MAX_PERSISTED_LEDGER_COUNTER:
            raise StageRepositoryError("retention_full")
        state[name] = current + 1
        return state[name]

    @classmethod
    def _advance_counters(cls, state: dict[str, Any], *names: str) -> tuple[int, ...]:
        for name in names:
            current = state[name]
            if type(current) is not int or not 0 <= current < MAX_PERSISTED_LEDGER_COUNTER:
                raise StageRepositoryError("retention_full")
        return tuple(cls._advance_counter(state, name) for name in names)

    def _load_unlocked(self, target_identity: str) -> dict[str, Any]:
        ledger, _lock = self._paths(target_identity)
        self._ensure_owned_dir(self.ledger_dir)
        try:
            descriptor = os.open(ledger, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError:
            return self._empty(target_identity)
        except OSError:
            raise StageRepositoryError("ledger_invalid") from None
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() \
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
                raise StageRepositoryError("ledger_invalid")
            payload = b""
            while True:
                chunk = os.read(descriptor, min(65536, MAX_LEDGER_BYTES + 1 - len(payload)))
                if not chunk: break
                payload += chunk
            if len(payload) > MAX_LEDGER_BYTES:
                raise StageRepositoryError("retention_full")
            raw = json.loads(payload)
        except StageRepositoryError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise StageRepositoryError("ledger_invalid") from None
        finally:
            os.close(descriptor)
        required = {"schema_version", "target_identity", "generation", "ledger_revision",
                    "active_owner", "reserved_terminal_bytes", "records", "proofs",
                    "tombstones", "leases"}
        if type(raw) is not dict or set(raw) != required or raw["schema_version"] != 2 \
                or raw["target_identity"] != target_identity \
                or type(raw["generation"]) is not int or type(raw["ledger_revision"]) is not int \
                or not 0 <= raw["generation"] <= MAX_PERSISTED_LEDGER_COUNTER \
                or not 0 <= raw["ledger_revision"] <= MAX_PERSISTED_LEDGER_COUNTER \
                or raw["ledger_revision"] < raw["generation"] \
                or type(raw["reserved_terminal_bytes"]) is not int \
                or raw["reserved_terminal_bytes"] not in {0, TERMINAL_RESERVATION_BYTES} \
                or (raw["active_owner"] is None) != (raw["reserved_terminal_bytes"] == 0) \
                or any(type(raw[key]) is not dict
                       for key in ("records", "proofs", "tombstones", "leases")) \
                or len(raw["records"]) > MAX_PROOFS or len(raw["proofs"]) > MAX_PROOFS \
                or len(raw["tombstones"]) > MAX_TOMBSTONES \
                or len(raw["leases"]) > MAX_LIVE_PROOF_LEASES \
                or set(raw["tombstones"]) & (set(raw["records"]) | set(raw["proofs"])):
            raise StageRepositoryError("ledger_invalid")
        try:
            decoded_proofs = {
                request_id: _proof_from(proof_raw)
                for request_id, proof_raw in raw["proofs"].items()
            }
            for request_id, record in raw["records"].items():
                _validate_record_shape(
                    request_id, record, generation=raw["generation"],
                    ledger_revision=raw["ledger_revision"])
                proof = decoded_proofs.get(request_id)
                result = record["result"]
                if result is None:
                    if proof is not None or record["phase"] == "succeeded":
                        raise ValueError
                    continue
                if type(result) is not dict or set(result) != {
                        "schema_version", "ok", "result_class", "code",
                        "request_id", "generation"}:
                    raise ValueError
                parsed_result = _result_from(result, result["request_id"], proof)
                if result != self._stored_result(parsed_result) \
                        or parsed_result.request_id != request_id \
                        or parsed_result.generation != record["generation"] \
                        or (parsed_result.ok and record["phase"] != "succeeded") \
                        or (not parsed_result.ok and record["phase"] != parsed_result.result_class):
                    raise ValueError
            for request_id, proof_raw in raw["proofs"].items():
                proof = decoded_proofs[request_id]
                record = raw["records"].get(request_id)
                if request_id != proof.request_id or type(record) is not dict \
                        or record.get("request_digest") != proof.request_digest \
                        or record.get("generation") != proof.staging_generation \
                        or record.get("phase") != "succeeded":
                    raise ValueError
            for request_id, tombstone in raw["tombstones"].items():
                if type(tombstone) is not dict or set(tombstone) != {
                        "request_id", "request_digest", "proof_digest", "result_code"} \
                        or request_id != tombstone["request_id"] \
                        or _IDENTITY.fullmatch(request_id) is None \
                        or type(tombstone["request_digest"]) is not str \
                        or _DIGEST.fullmatch(tombstone["request_digest"]) is None \
                        or type(tombstone["proof_digest"]) is not str \
                        or _DIGEST.fullmatch(tombstone["proof_digest"]) is None \
                        or tombstone["result_code"] != "proof_expired":
                    raise ValueError
            for lease_id, lease_raw in raw["leases"].items():
                lease = _lease_from(lease_raw)
                proof_raw = raw["proofs"].get(lease.stage_request_id)
                record = raw["records"].get(lease.stage_request_id)
                if lease_id != lease.lease_id or lease.target_identity != target_identity \
                        or lease.ledger_authority != STAGE_LEDGER_AUTHORITY \
                        or type(proof_raw) is not dict or type(record) is not dict \
                        or proof_raw.get("proof_digest") != lease.proof_digest \
                        or proof_raw.get("staging_generation") != lease.stage_generation \
                        or record.get("ledger_revision") != lease.ledger_revision:
                    raise ValueError
            owner = raw["active_owner"]
            active_records = [record for record in raw["records"].values()
                              if record["phase"] not in TERMINAL_PHASES
                              or record["result"] is None
                              or record["phase"] == "uncertain"]
            if owner is None:
                if active_records:
                    raise ValueError
            else:
                if type(owner) is not dict or set(owner) != _OWNER_FIELDS:
                    raise ValueError
                _validate_process(owner["process"])
                _validate_cleanup(owner["cleanup"])
                record = raw["records"].get(owner["request_id"])
                if type(record) is not dict \
                        or owner != {key: record[key] for key in _OWNER_FIELDS} \
                        or len(active_records) != 1 or active_records[0] is not record \
                        or owner["generation"] != raw["generation"]:
                    raise ValueError
        except (KeyError, TypeError, ValueError, StagingContractError, StageRepositoryError):
            raise StageRepositoryError("ledger_invalid") from None
        return raw

    def _write_unlocked(self, target_identity: str, state: dict[str, Any]) -> None:
        try:
            payload = canonical_bytes(state, maximum=MAX_LEDGER_BYTES)
        except StagingContractError:
            raise StageRepositoryError("retention_full") from None
        ledger, _lock = self._paths(target_identity)
        self._ensure_owned_dir(self.ledger_dir)
        try:
            existing = os.lstat(ledger)
        except FileNotFoundError:
            existing = None
        if existing is not None and (not stat.S_ISREG(existing.st_mode)
                or stat.S_ISLNK(existing.st_mode) or existing.st_uid != os.geteuid()
                or stat.S_IMODE(existing.st_mode) != 0o600 or existing.st_nlink != 1):
            raise StageRepositoryError("ledger_invalid")
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{ledger.name}.", dir=self.ledger_dir)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=True) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, ledger)
            parent = os.open(self.ledger_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass

    @staticmethod
    def _pinned_proofs(state: dict[str, Any]) -> set[str]:
        return {raw["proof_digest"] for raw in state["leases"].values()
                if type(raw) is dict and raw.get("phase") in {"prepared", "accepted"}}

    @staticmethod
    def _stored_result(result) -> dict[str, Any]:
        stored = result.as_mapping()
        stored.pop("proof", None)
        return stored

    def _compact_for_reservation(self, state: dict[str, Any]) -> None:
        pinned = self._pinned_proofs(state)
        while len(state["records"]) >= MAX_PROOFS or len(state["proofs"]) >= MAX_PROOFS:
            candidate = next((request_id for request_id, proof in state["proofs"].items()
                              if proof.get("proof_digest") not in pinned), None)
            if candidate is None or len(state["tombstones"]) >= MAX_TOMBSTONES - 1:
                raise StageRepositoryError("retention_full")
            proof = state["proofs"].pop(candidate)
            record = state["records"][candidate]
            state["tombstones"][candidate] = StageProofTombstone(
                candidate, record["request_digest"], proof["proof_digest"]).as_mapping()
            del state["records"][candidate]

    @staticmethod
    def _assert_reserved_bound(state: dict[str, Any]) -> None:
        try:
            encoded = canonical_bytes(state, maximum=MAX_LEDGER_BYTES)
        except StagingContractError:
            raise StageRepositoryError("retention_full") from None
        if len(encoded) + state["reserved_terminal_bytes"] > MAX_LEDGER_BYTES:
            raise StageRepositoryError("retention_full")

    @staticmethod
    def lookup_result_unlocked(state: dict[str, Any], request_id: str):
        result = state["records"][request_id].get("result")
        if type(result) is not dict:
            return None
        proof_raw = state["proofs"].get(request_id)
        proof = _proof_from(proof_raw) if result.get("ok") else None
        if result.get("ok") and proof is None:
            raise StageRepositoryError("ledger_invalid")
        try:
            return _result_from(result, request_id, proof)
        except (KeyError, TypeError, ValueError):
            raise StageRepositoryError("ledger_invalid") from None

    def lookup(self, target_identity: str, request_id: str) -> StageResult | dict | None:
        with self.target_lock(target_identity):
            state = self._load_unlocked(target_identity)
            if request_id in state["tombstones"]:
                return StageResult(1, False, "refused", "proof_expired", request_id,
                                   state["generation"])
            record = state["records"].get(request_id)
            if record is None:
                return None
            return self.lookup_result_unlocked(state, request_id) or dict(record)

    def lookup_for_request(self, request):
        """Read status bound to one exact request and its schema.

        The legacy ``lookup`` surface remains unchanged.  V2 callers use this
        exact request-bound form so a retained tombstone can preserve both its
        digest semantics and the caller's closed result schema.
        """
        if type(request) not in {StageRequest, StageRequestSet}:
            raise StageRepositoryError("request_conflict")
        target = request.target.target_identity
        with self.target_lock(target):
            state = self._load_unlocked(target)
            tombstone = state["tombstones"].get(request.request_id)
            if tombstone is not None:
                code = "proof_expired" \
                    if tombstone.get("request_digest") == request.request_digest \
                    else "request_conflict"
                return _failure_for(request, "refused", code, state["generation"])
            record = state["records"].get(request.request_id)
            if record is None:
                return None
            if record.get("request_digest") != request.request_digest:
                return _failure_for(
                    request, "refused", "request_conflict", state["generation"])
            result = self.lookup_result_unlocked(state, request.request_id)
            if result is not None:
                expected = StageResultSet if type(request) is StageRequestSet else StageResult
                if type(result) is not expected:
                    raise StageRepositoryError("ledger_invalid")
                return result
            return dict(record)

    def accept(self, request):
        target = request.target.target_identity
        with self.target_lock(target):
            state = self._load_unlocked(target)
            existing = state["records"].get(request.request_id)
            if existing is not None:
                if existing.get("request_digest") != request.request_digest:
                    return "conflict", state["generation"], _failure_for(
                        request, "refused", "request_conflict", state["generation"])
                result = self.lookup_result_unlocked(state, request.request_id)
                if result is None:
                    result = _failure_for(request, "in_progress", "accepted", state["generation"])
                return "replay", state["generation"], result
            if request.request_id in state["tombstones"]:
                tombstone = state["tombstones"][request.request_id]
                code = "proof_expired" if tombstone.get("request_digest") == request.request_digest \
                    else "request_conflict"
                return "replay", state["generation"], _failure_for(
                    request, "refused", code, state["generation"])
            if len(state["tombstones"]) >= MAX_TOMBSTONES:
                raise StageRepositoryError("retention_full")
            if state["active_owner"] is not None:
                return "busy", state["generation"], _failure_for(
                    request, "refused", "target_busy", state["generation"])
            if request.expected_generation != state["generation"]:
                return "conflict", state["generation"], _failure_for(
                    request, "refused", "generation_conflict", state["generation"])
            self._compact_for_reservation(state)
            next_generation, next_revision = self._advance_counters(
                state, "generation", "ledger_revision")
            owner = {"request_id": request.request_id, "request_digest": request.request_digest,
                     "generation": next_generation, "phase": "accepted",
                     "effect_entered": False, "process": None, "cleanup": None}
            state["records"][request.request_id] = {**owner, "ledger_revision": next_revision,
                                                     "result": None}
            state["active_owner"] = dict(owner)
            state["reserved_terminal_bytes"] = TERMINAL_RESERVATION_BYTES
            self._assert_reserved_bound(state)
            self._write_unlocked(target, state)
            return "accepted", next_generation, None

    def transition(self, request, phase: str, *, process: dict | None = None,
                   cleanup: dict | None = None) -> int:
        if phase not in {"credential_pending", "helper_running", "pulling", "cleanup_pending",
                         "observing", *TERMINAL_PHASES}:
            raise StageRepositoryError("phase_invalid")
        target = request.target.target_identity
        with self.target_lock(target):
            state = self._load_unlocked(target)
            record = state["records"].get(request.request_id)
            owner = state["active_owner"]
            if not record or type(owner) is not dict \
                    or owner.get("request_id") != request.request_id \
                    or owner.get("request_digest") != request.request_digest \
                    or record["request_digest"] != request.request_digest \
                    or record["phase"] in TERMINAL_PHASES:
                raise StageRepositoryError("request_conflict")
            record["phase"] = owner["phase"] = phase
            if phase in EFFECT_PHASES:
                record["effect_entered"] = owner["effect_entered"] = True
            if process is not None:
                record["process"] = process
                owner["process"] = process
            if cleanup is not None:
                record["cleanup"] = cleanup
                owner["cleanup"] = cleanup
            self._advance_counter(state, "ledger_revision")
            self._assert_reserved_bound(state)
            self._write_unlocked(target, state)
            return state["generation"]

    def commit(self, request, result):
        target = request.target.target_identity
        with self.target_lock(target):
            state = self._load_unlocked(target)
            record = state["records"].get(request.request_id)
            if not record or record["request_digest"] != request.request_digest:
                raise StageRepositoryError("request_conflict")
            existing = self.lookup_result_unlocked(state, request.request_id)
            if existing is not None:
                if existing.as_mapping() == result.as_mapping():
                    return existing
                if existing.result_class != "uncertain" or result.result_class == "uncertain":
                    raise StageRepositoryError("request_conflict")
            owner = state["active_owner"]
            if type(owner) is not dict or owner.get("request_id") != request.request_id \
                    or owner.get("request_digest") != request.request_digest:
                raise StageRepositoryError("request_conflict")
            if result.generation != owner["generation"] or result.generation != state["generation"]:
                raise StageRepositoryError("generation_conflict")
            record["phase"] = "succeeded" if result.ok else result.result_class
            record["result"] = self._stored_result(result)
            self._advance_counter(state, "ledger_revision")
            record["ledger_revision"] = state["ledger_revision"]
            if result.proof is not None:
                state["proofs"][request.request_id] = result.proof.as_mapping()
            if result.result_class != "uncertain":
                state["active_owner"] = None
                state["reserved_terminal_bytes"] = 0
            try:
                canonical_bytes(state, maximum=MAX_LEDGER_BYTES)
            except StagingContractError:
                raise StageRepositoryError("retention_full") from None
            self._write_unlocked(target, state)
            return result

    def close_precredential_uncertain(self, request, *, expected_ledger_revision: int):
        """Atomically terminalize one exact v2 pre-effect uncertain owner."""
        from .staging_v2 import StageRequestSet, StageResultSet
        if type(request) is not StageRequestSet or type(expected_ledger_revision) is not int:
            raise StageRepositoryError("request_conflict")
        target = request.target.target_identity
        with self.target_lock(target):
            state = self._load_unlocked(target)
            record = state["records"].get(request.request_id)
            owner = state["active_owner"]
            existing = self.lookup_result_unlocked(state, request.request_id)
            if type(record) is not dict or type(owner) is not dict \
                    or type(existing) is not StageResultSet \
                    or existing.result_class != "uncertain" \
                    or record["phase"] != "uncertain" \
                    or record["effect_entered"] is not False \
                    or record["request_digest"] != request.request_digest \
                    or record["generation"] != state["generation"] \
                    or record["ledger_revision"] != expected_ledger_revision \
                    or owner != {key: record[key] for key in _OWNER_FIELDS}:
                raise StageRepositoryError("request_conflict")
            result = StageResultSet(2, False, "failed", "precredential_bootstrap_failed",
                                    request.request_id, record["generation"])
            record["phase"] = "failed"
            record["process"] = {"unit_inactive": True,
                                 "cgroup_empty_or_removed": True}
            record["cleanup"] = {"complete": True}
            record["result"] = self._stored_result(result)
            self._advance_counter(state, "ledger_revision")
            record["ledger_revision"] = state["ledger_revision"]
            state["active_owner"] = None
            state["reserved_terminal_bytes"] = 0
            self._assert_reserved_bound(state)
            self._write_unlocked(target, state)
            return result

    def close_posteffect_uncertain(self, request, *, expected_ledger_revision: int):
        """Atomically close one exact v2 effect-entered cleanup uncertainty."""
        from .staging_v2 import StageRequestSet, StageResultSet
        if type(request) is not StageRequestSet or type(expected_ledger_revision) is not int:
            raise StageRepositoryError("request_conflict")
        target = request.target.target_identity
        with self.target_lock(target):
            state = self._load_unlocked(target)
            record = state["records"].get(request.request_id)
            owner = state["active_owner"]
            existing = self.lookup_result_unlocked(state, request.request_id)
            if type(record) is not dict or type(owner) is not dict \
                    or type(existing) is not StageResultSet \
                    or existing.result_class != "uncertain" \
                    or record["phase"] != "uncertain" \
                    or record["effect_entered"] is not True \
                    or record["request_digest"] != request.request_digest \
                    or record["generation"] != state["generation"] \
                    or record["ledger_revision"] != expected_ledger_revision \
                    or owner != {key: record[key] for key in _OWNER_FIELDS}:
                raise StageRepositoryError("request_conflict")
            result = StageResultSet(2, False, "failed", "cleanup_reconciled",
                                    request.request_id, record["generation"])
            record["phase"] = "failed"
            record["process"] = {"unit_inactive": True,
                                 "cgroup_empty_or_removed": True}
            record["cleanup"] = {"complete": True}
            record["result"] = self._stored_result(result)
            self._advance_counter(state, "ledger_revision")
            record["ledger_revision"] = state["ledger_revision"]
            state["active_owner"] = None
            state["reserved_terminal_bytes"] = 0
            self._assert_reserved_bound(state)
            self._write_unlocked(target, state)
            return result

    def record_status(self, target_identity: str, request_id: str) -> dict | None:
        """Return private durable phase evidence for read-only reconciliation."""
        with self.target_lock(target_identity):
            state = self._load_unlocked(target_identity)
            record = state["records"].get(request_id)
            return dict(record) if type(record) is dict else None

    def target_revision(self, target_identity: str) -> tuple[int, int]:
        """Return the exact generation and ledger revision through repository custody."""
        with self.target_lock(target_identity):
            state = self._load_unlocked(target_identity)
            return state["generation"], state["ledger_revision"]

    def fence_possible_effect(self, request, *, code: str = "unknown_effect"):
        generation = self.transition(request, "uncertain")
        return self.commit(request, _failure_for(request, "uncertain", code, generation))

    @contextmanager
    def proof_custody_transaction(self, target_identity: str, *, target_mutation_port,
                                  host_state_port) -> Iterator[ProofCustodyPort]:
        target_transaction = getattr(target_mutation_port, "target_mutation_transaction", None)
        host_transaction = getattr(host_state_port, "atomic_host_state_transaction", None)
        atomic_validator = getattr(host_state_port, "validate_atomic_host_state_evidence", None)
        terminal_validator = getattr(host_state_port, "validate_durable_terminal_authority", None)
        if not callable(target_transaction) or not callable(host_transaction) \
                or not callable(atomic_validator) or not callable(terminal_validator):
            raise StageRepositoryError("lease_conflict")
        with target_transaction(target_identity):
            with host_transaction(target_identity):
                with self.target_lock(target_identity):
                    port = _LockedCustodyPort(
                        self, target_identity, atomic_validator, terminal_validator)
                    try:
                        yield port
                    finally:
                        port.close()


class _LockedCustodyPort(ProofCustodyPort):
    def __init__(self, repository: StageRepository, target_identity: str,
                 atomic_validator, terminal_validator) -> None:
        self._repository = repository
        self._target = target_identity
        self._active = True
        self._atomic_validator = atomic_validator
        self._terminal_validator = terminal_validator

    def close(self) -> None:
        self._active = False

    def _load(self) -> dict[str, Any]:
        if not self._active:
            raise StageRepositoryError("lease_conflict")
        return self._repository._load_unlocked(self._target)

    def lookup(self, lease_id: str) -> StageProofActivationLease | None:
        state = self._load()
        raw = state["leases"].get(lease_id)
        return None if raw is None else _lease_from(raw)

    def validate_retained_proof(self, **binding: object):
        required = {"stage_request_id", "stage_request_digest", "proof_digest",
                    "stage_generation", "ledger_authority", "ledger_revision",
                    "supplied_proof"}
        if set(binding) != required:
            raise StageRepositoryError("lease_conflict")
        state = self._load()
        if binding["ledger_authority"] != STAGE_LEDGER_AUTHORITY:
            raise StageRepositoryError("lease_conflict")
        proof_raw = state["proofs"].get(binding["stage_request_id"])
        record = state["records"].get(binding["stage_request_id"])
        try:
            retained = _proof_from(proof_raw)
            supplied_value = binding["supplied_proof"]
            supplied_raw = supplied_value.as_mapping() if type(supplied_value) in {
                StagedImageProof, StagedImageProofSet} else supplied_value
            supplied = _proof_from(supplied_raw)
        except (KeyError, TypeError, ValueError, StagingContractError, StageRepositoryError):
            raise StageRepositoryError("proof_invalid") from None
        if type(record) is not dict \
                or record.get("request_id") != retained.request_id \
                or record.get("request_digest") != retained.request_digest \
                or record.get("generation") != retained.staging_generation \
                or record.get("ledger_revision") != binding["ledger_revision"] \
                or retained.request_id != binding["stage_request_id"] \
                or retained.request_digest != binding["stage_request_digest"] \
                or retained.proof_digest != binding["proof_digest"] \
                or retained.staging_generation != binding["stage_generation"]:
            raise StageRepositoryError("proof_expired")
        if canonical_bytes(retained.as_mapping()) != canonical_bytes(supplied.as_mapping()):
            raise StageRepositoryError("proof_invalid")
        return retained

    def prepare(self, **binding: object) -> StageProofActivationLease:
        required = {"lease_id", "holder", "admission_deadline", "activation_request_id",
                    "activation_request_digest", "stage_request_id", "stage_request_digest",
                    "proof_digest", "stage_generation", "ledger_authority",
                    "ledger_revision"}
        if set(binding) != required:
            raise StageRepositoryError("lease_conflict")
        state = self._load()
        proof = state["proofs"].get(binding["stage_request_id"])
        record = state["records"].get(binding["stage_request_id"])
        if not proof or not record or record["request_digest"] != binding["stage_request_digest"] \
                or record["ledger_revision"] != binding["ledger_revision"] \
                or binding["ledger_authority"] != STAGE_LEDGER_AUTHORITY \
                or proof["proof_digest"] != binding["proof_digest"] \
                or proof["staging_generation"] != binding["stage_generation"]:
            raise StageRepositoryError("proof_expired")
        candidate = StageProofActivationLease(
            phase="prepared", target_identity=self._target,
            **binding)
        existing = state["leases"].get(candidate.lease_id)
        if existing is not None:
            parsed = _lease_from(existing)
            if replace(parsed, phase="prepared", acceptance_receipt=None).as_mapping() \
                    != candidate.as_mapping():
                raise StageRepositoryError("lease_conflict")
            return parsed
        if candidate.expired:
            raise StageRepositoryError("lease_expired")
        if len(state["leases"]) >= MAX_LIVE_PROOF_LEASES:
            raise StageRepositoryError("lease_capacity")
        state["leases"][candidate.lease_id] = candidate.as_mapping()
        self._repository._advance_counter(state, "ledger_revision")
        self._repository._write_unlocked(self._target, state)
        return candidate

    @staticmethod
    def _exact_host_evidence(current: StageProofActivationLease,
                             evidence: AtomicHostStateEvidence) -> None:
        if evidence.holder != current.holder \
                or evidence.activation_request_id != current.activation_request_id \
                or evidence.activation_request_digest != current.activation_request_digest \
                or evidence.proof_digest != current.proof_digest:
            raise StageRepositoryError("holder_mismatch")

    def promote(self, lease: StageProofActivationLease,
                evidence: AtomicHostStateEvidence) -> StageProofActivationLease:
        state = self._load()
        if type(evidence) is not AtomicHostStateEvidence \
                or self._atomic_validator(evidence) is not True:
            raise StageRepositoryError("acceptance_ambiguous")
        current = _lease_from(state["leases"].get(lease.lease_id))
        if current.as_mapping() != lease.as_mapping():
            raise StageRepositoryError("lease_conflict")
        self._exact_host_evidence(current, evidence)
        if evidence.state == "ambiguous":
            raise StageRepositoryError("acceptance_ambiguous")
        if evidence.state != "accepted" or not evidence.acceptance_receipt:
            raise StageRepositoryError("lease_conflict")
        if current.phase == "accepted":
            if current.acceptance_receipt != evidence.acceptance_receipt:
                raise StageRepositoryError("lease_conflict")
            return current
        promoted = replace(current, phase="accepted",
                           acceptance_receipt=evidence.acceptance_receipt)
        state["leases"][current.lease_id] = promoted.as_mapping()
        self._repository._advance_counter(state, "ledger_revision")
        self._repository._write_unlocked(self._target, state)
        return promoted

    def cancel(self, lease: StageProofActivationLease,
               evidence: AtomicHostStateEvidence) -> None:
        state = self._load()
        if type(evidence) is not AtomicHostStateEvidence \
                or self._atomic_validator(evidence) is not True:
            raise StageRepositoryError("acceptance_ambiguous")
        current = _lease_from(state["leases"].get(lease.lease_id))
        if current.as_mapping() != lease.as_mapping():
            raise StageRepositoryError("lease_conflict")
        self._exact_host_evidence(current, evidence)
        if evidence.state == "ambiguous":
            raise StageRepositoryError("acceptance_ambiguous")
        if current.phase != "prepared" or evidence.state != "absent":
            raise StageRepositoryError("lease_conflict")
        if not current.expired:
            raise StageRepositoryError("lease_conflict")
        del state["leases"][current.lease_id]
        self._repository._advance_counter(state, "ledger_revision")
        self._repository._write_unlocked(self._target, state)

    def release(self, lease: StageProofActivationLease,
                evidence: DurableTerminalAuthorityEvidence) -> None:
        state = self._load()
        if type(evidence) is not DurableTerminalAuthorityEvidence \
                or self._terminal_validator(evidence) is not True:
            raise StageRepositoryError("terminal_not_durable")
        current = _lease_from(state["leases"].get(lease.lease_id))
        if current.as_mapping() != lease.as_mapping() or current.phase != "accepted" \
                or evidence.holder != current.holder \
                or evidence.proof_digest != current.proof_digest \
                or evidence.acceptance_receipt != current.acceptance_receipt:
            raise StageRepositoryError("terminal_not_durable")
        del state["leases"][current.lease_id]
        self._repository._advance_counter(state, "ledger_revision")
        self._repository._write_unlocked(self._target, state)
