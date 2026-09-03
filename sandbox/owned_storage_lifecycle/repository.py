"""Storage authority lifecycle repository with file locking, atomic replacement, and CAS."""

import dataclasses
import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from sandbox.owned_storage_lifecycle.models import (
    AcceptanceOutcome,
    AcceptancePhase,
    AcceptanceState,
    AuthorityCapability,
    CapabilityAcceptance,
    CapabilityAcceptanceRequest,
    CapabilityPromotion,
    CapabilityReviewDecision,
    CapabilityReviewRequest,
    CapabilityRevocation,
    PromotionPhase,
    ReviewDecision,
    ReviewPhase,
    SupportTier,
)


class LifecycleError(Exception):
    """Base error for lifecycle repository."""


class LifecycleCASError(LifecycleError):
    """Raised when expected generation does not match current lifecycle generation."""


class LifecycleConflictError(LifecycleError):
    """Raised when replaying a request ID with conflicting content."""


class StorageAuthorityLifecycleRepository:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        # Ensure parent directory has 0700 permissions
        try:
            os.chmod(self.lock_path.parent, 0o700)
        except OSError:
            pass

        with open(self.lock_path, "a+") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _default_state(self) -> Dict[str, Any]:
        return {
            "generation": 0,
            "capabilities": {},
            "review_requests": {},
            "review_decisions": {},
            "promotions": {},
            "acceptance_requests": {},
            "acceptances": {},
            "revocations": {},
        }

    def load_state(self) -> Dict[str, Any]:
        with self._lock():
            return self._read_state_locked()

    def _read_state_locked(self) -> Dict[str, Any]:
        if not self.path.exists():
            state = self._default_state()
            self._write_state_locked(state)
            return state
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            state = self._default_state()
            self._write_state_locked(state)
            return state

    def _write_state_locked(self, state: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(f"{self.path.name}.tmp.{os.getpid()}")
        data = json.dumps(state, indent=2, sort_keys=True)
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fchmod(f.fileno(), 0o600)
            os.fsync(f.fileno())

        os.replace(tmp_path, self.path)

        # fsync parent directory
        try:
            parent_fd = os.open(str(self.path.parent), os.O_RDONLY)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        except OSError:
            pass

    def get_generation(self) -> int:
        with self._lock():
            state = self._read_state_locked()
            return int(state.get("generation", 0))

    def save_capability(self, capability: AuthorityCapability, expected_generation: int) -> int:
        with self._lock():
            state = self._read_state_locked()
            current_gen = state.get("generation", 0)
            if current_gen != expected_generation:
                raise LifecycleCASError(
                    f"Generation mismatch: expected {expected_generation}, found {current_gen}"
                )

            data = dataclasses.asdict(capability)
            # Serialize enums to string values
            if isinstance(data.get("support_tier"), SupportTier):
                data["support_tier"] = data["support_tier"].value
            if isinstance(data.get("acceptance_state"), AcceptanceState):
                data["acceptance_state"] = data["acceptance_state"].value

            state["capabilities"][capability.remote_identity] = data
            state["generation"] = current_gen + 1
            self._write_state_locked(state)
            return state["generation"]

    def get_capability(self, remote_identity: str) -> Optional[AuthorityCapability]:
        with self._lock():
            state = self._read_state_locked()
            data = state.get("capabilities", {}).get(remote_identity)
            if not data:
                return None

            data = dict(data)
            if data.get("support_tier"):
                data["support_tier"] = SupportTier(data["support_tier"])
            if data.get("acceptance_state"):
                data["acceptance_state"] = AcceptanceState(data["acceptance_state"])
            return AuthorityCapability(**data)

    def record_review_request(self, request: CapabilityReviewRequest, expected_generation: int) -> int:
        with self._lock():
            state = self._read_state_locked()
            current_gen = state.get("generation", 0)

            existing = state.get("review_requests", {}).get(request.review_request_id)
            if existing:
                if existing.get("request_digest") == request.request_digest:
                    return current_gen
                raise LifecycleConflictError(
                    f"Review request {request.review_request_id} already exists with different digest"
                )

            if current_gen != expected_generation:
                raise LifecycleCASError(
                    f"Generation mismatch: expected {expected_generation}, found {current_gen}"
                )

            data = dataclasses.asdict(request)
            if isinstance(data.get("requested_decision"), ReviewDecision):
                data["requested_decision"] = data["requested_decision"].value
            if isinstance(data.get("phase"), ReviewPhase):
                data["phase"] = data["phase"].value

            state["review_requests"][request.review_request_id] = data
            state["generation"] = current_gen + 1
            self._write_state_locked(state)
            return state["generation"]

    def record_promotion(self, promotion: CapabilityPromotion, expected_generation: int) -> int:
        with self._lock():
            state = self._read_state_locked()
            current_gen = state.get("generation", 0)

            existing = state.get("promotions", {}).get(promotion.promotion_id)
            if existing:
                if existing.get("request_digest") == promotion.request_digest:
                    return current_gen
                raise LifecycleConflictError(
                    f"Promotion {promotion.promotion_id} already exists with different digest"
                )

            if current_gen != expected_generation:
                raise LifecycleCASError(
                    f"Generation mismatch: expected {expected_generation}, found {current_gen}"
                )

            data = dataclasses.asdict(promotion)
            if isinstance(data.get("phase"), PromotionPhase):
                data["phase"] = data["phase"].value
            if isinstance(data.get("support_tier"), SupportTier):
                data["support_tier"] = data["support_tier"].value

            state["promotions"][promotion.promotion_id] = data
            state["generation"] = current_gen + 1
            self._write_state_locked(state)
            return state["generation"]

    def get_promotion(self, promotion_id: str) -> Optional[CapabilityPromotion]:
        with self._lock():
            state = self._read_state_locked()
            data = state.get("promotions", {}).get(promotion_id)
            if not data:
                return None
            data = dict(data)
            if data.get("phase"):
                data["phase"] = PromotionPhase(data["phase"])
            if data.get("support_tier"):
                data["support_tier"] = SupportTier(data["support_tier"])
            return CapabilityPromotion(**data)
