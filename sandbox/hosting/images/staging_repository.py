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
import stat
import tempfile
import time
from typing import Any, Iterator

from sandbox.core._paths import RUNTIME_DIR
from .staging_models import (
    AtomicHostStateEvidence, DurableTerminalAuthorityEvidence, MAX_LEDGER_BYTES,
    MAX_LIVE_PROOF_LEASES, MAX_PROOFS, MAX_STAGE_FRAME_BYTES, MAX_TOMBSTONES,
    ProofCustodyPort, StageProofActivationLease, StageProofTombstone, StageRequest,
    StageResult, StagedImageProof, StagingContractError, canonical_bytes,
)

TERMINAL_PHASES = frozenset({"succeeded", "refused", "failed", "cancelled", "uncertain"})
EFFECT_PHASES = frozenset({"pulling", "cleanup_pending", "observing", "succeeded"})
TERMINAL_RESERVATION_BYTES = MAX_STAGE_FRAME_BYTES + 4096


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
                or type(raw["reserved_terminal_bytes"]) is not int \
                or raw["reserved_terminal_bytes"] not in {0, TERMINAL_RESERVATION_BYTES} \
                or (raw["active_owner"] is None) != (raw["reserved_terminal_bytes"] == 0) \
                or any(type(raw[key]) is not dict
                       for key in ("records", "proofs", "tombstones", "leases")):
            raise StageRepositoryError("ledger_invalid")
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
    def _stored_result(result: StageResult) -> dict[str, Any]:
        stored = result.as_mapping()
        stored.pop("proof", None)
        return stored

    def _compact_for_reservation(self, state: dict[str, Any]) -> None:
        pinned = self._pinned_proofs(state)
        while len(state["proofs"]) >= MAX_PROOFS:
            candidate = next((request_id for request_id, proof in state["proofs"].items()
                              if proof.get("proof_digest") not in pinned), None)
            if candidate is None or len(state["tombstones"]) >= MAX_TOMBSTONES - 1:
                raise StageRepositoryError("retention_full")
            proof = state["proofs"].pop(candidate)
            record = state["records"][candidate]
            state["tombstones"][candidate] = StageProofTombstone(
                candidate, record["request_digest"], proof["proof_digest"]).as_mapping()
            record["phase"] = "refused"
            record["result"] = self._stored_result(StageResult(
                1, False, "refused", "proof_expired", candidate, record["generation"]))

    @staticmethod
    def _assert_reserved_bound(state: dict[str, Any]) -> None:
        try:
            encoded = canonical_bytes(state, maximum=MAX_LEDGER_BYTES)
        except StagingContractError:
            raise StageRepositoryError("retention_full") from None
        if len(encoded) + state["reserved_terminal_bytes"] > MAX_LEDGER_BYTES:
            raise StageRepositoryError("retention_full")

    @staticmethod
    def lookup_result_unlocked(state: dict[str, Any], request_id: str) -> StageResult | None:
        result = state["records"][request_id].get("result")
        if type(result) is not dict:
            return None
        proof_raw = state["proofs"].get(request_id)
        proof = StagedImageProof.from_mapping(proof_raw) if result.get("ok") else None
        if result.get("ok") and proof is None:
            raise StageRepositoryError("ledger_invalid")
        try:
            return StageResult(result["schema_version"], result["ok"], result["result_class"],
                               result["code"], request_id, result["generation"], proof)
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

    def accept(self, request: StageRequest) -> tuple[str, int, StageResult | None]:
        target = request.target.target_identity
        with self.target_lock(target):
            state = self._load_unlocked(target)
            existing = state["records"].get(request.request_id)
            if existing is not None:
                if existing.get("request_digest") != request.request_digest:
                    return "conflict", state["generation"], StageResult(
                        1, False, "refused", "request_conflict", request.request_id,
                        state["generation"])
                result = self.lookup_result_unlocked(state, request.request_id)
                if result is None:
                    result = StageResult(1, False, "in_progress", "accepted",
                                         request.request_id, state["generation"])
                return "replay", state["generation"], result
            if request.request_id in state["tombstones"]:
                tombstone = state["tombstones"][request.request_id]
                code = "proof_expired" if tombstone.get("request_digest") == request.request_digest \
                    else "request_conflict"
                return "replay", state["generation"], StageResult(
                    1, False, "refused", code, request.request_id, state["generation"])
            if len(state["tombstones"]) >= MAX_TOMBSTONES:
                raise StageRepositoryError("retention_full")
            if state["active_owner"] is not None:
                return "busy", state["generation"], StageResult(
                    1, False, "refused", "target_busy", request.request_id,
                    state["generation"])
            if request.expected_generation != state["generation"]:
                return "conflict", state["generation"], StageResult(
                    1, False, "refused", "generation_conflict", request.request_id,
                    state["generation"])
            self._compact_for_reservation(state)
            next_generation = state["generation"] + 1
            next_revision = state["ledger_revision"] + 1
            owner = {"request_id": request.request_id, "request_digest": request.request_digest,
                     "generation": next_generation, "phase": "accepted",
                     "effect_entered": False, "process": None, "cleanup": None}
            state["records"][request.request_id] = {**owner, "ledger_revision": next_revision,
                                                     "result": None}
            state["active_owner"] = dict(owner)
            state["reserved_terminal_bytes"] = TERMINAL_RESERVATION_BYTES
            state["generation"] = next_generation
            state["ledger_revision"] = next_revision
            self._assert_reserved_bound(state)
            self._write_unlocked(target, state)
            return "accepted", next_generation, None

    def transition(self, request: StageRequest, phase: str, *, process: dict | None = None,
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
            state["ledger_revision"] += 1
            self._assert_reserved_bound(state)
            self._write_unlocked(target, state)
            return state["generation"]

    def commit(self, request: StageRequest, result: StageResult) -> StageResult:
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
            state["ledger_revision"] += 1
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

    def record_status(self, target_identity: str, request_id: str) -> dict | None:
        """Return private durable phase evidence for read-only reconciliation."""
        with self.target_lock(target_identity):
            state = self._load_unlocked(target_identity)
            record = state["records"].get(request_id)
            return dict(record) if type(record) is dict else None

    def fence_possible_effect(self, request: StageRequest, *, code: str = "unknown_effect") -> StageResult:
        generation = self.transition(request, "uncertain")
        return self.commit(request, StageResult(
            1, False, "uncertain", code, request.request_id, generation))

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

    def prepare(self, **binding: object) -> StageProofActivationLease:
        required = {"lease_id", "holder", "admission_deadline", "activation_request_id",
                    "activation_request_digest", "stage_request_id", "stage_request_digest",
                    "proof_digest", "stage_generation"}
        if set(binding) != required:
            raise StageRepositoryError("lease_conflict")
        state = self._load()
        proof = state["proofs"].get(binding["stage_request_id"])
        record = state["records"].get(binding["stage_request_id"])
        if not proof or not record or record["request_digest"] != binding["stage_request_digest"] \
                or proof["proof_digest"] != binding["proof_digest"] \
                or proof["staging_generation"] != binding["stage_generation"]:
            raise StageRepositoryError("proof_expired")
        candidate = StageProofActivationLease(
            phase="prepared", target_identity=self._target,
            ledger_revision=record["ledger_revision"], **binding)
        existing = state["leases"].get(candidate.lease_id)
        if existing is not None:
            parsed = _lease_from(existing)
            if parsed.as_mapping() != candidate.as_mapping():
                raise StageRepositoryError("lease_conflict")
            return parsed
        if candidate.expired:
            raise StageRepositoryError("lease_expired")
        if len(state["leases"]) >= MAX_LIVE_PROOF_LEASES:
            raise StageRepositoryError("lease_capacity")
        state["leases"][candidate.lease_id] = candidate.as_mapping()
        state["ledger_revision"] += 1
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
        state["ledger_revision"] += 1
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
        state["ledger_revision"] += 1
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
        state["ledger_revision"] += 1
        self._repository._write_unlocked(self._target, state)
