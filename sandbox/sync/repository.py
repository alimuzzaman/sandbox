"""Private atomic synchronization relationship journal."""

from __future__ import annotations

from dataclasses import replace
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable

from .models import (
    DivergenceRecord,
    Participant,
    SourceGeneration,
    SynchronizationRelationship,
    utc_now,
    validate_count,
    validate_identifier,
    validate_timestamp,
)


SCHEMA_VERSION = 1


class SyncRepositoryError(RuntimeError):
    pass


class SyncJournalCorruption(SyncRepositoryError):
    pass


class RelationshipConflict(SyncRepositoryError):
    pass


class RequestDigestConflict(SyncRepositoryError):
    pass


class RelationshipNotFound(SyncRepositoryError):
    pass


class GenerationNotFound(SyncRepositoryError):
    pass


def default_journal_path() -> Path:
    home = Path(os.environ.get("SANDBOX_HOME", "~/sandbox")).expanduser().resolve()
    return home / "runtime" / "sync" / "journal.json"


class SyncRepository:
    """Cross-process JSON journal with replay-safe generation reservation."""

    def __init__(self, path: str | Path | None = None, *, replace_file: Callable = os.replace) -> None:
        self.path = Path(path).expanduser().resolve() if path is not None else default_journal_path()
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._replace = replace_file

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "relationships": {}, "generations": {}, "requests": {},
            "participants": {}, "divergences": {}, "metrics": {},
        }

    def _prepare(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        if self.path.is_symlink() or self.lock_path.is_symlink():
            raise SyncJournalCorruption("synchronization journal path is unsafe")
        descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        os.fchmod(descriptor, 0o600)
        os.close(descriptor)

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SyncJournalCorruption("synchronization journal is unreadable") from exc
        if (not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION or
                not all(isinstance(value.get(key), dict) for key in (
                    "relationships", "generations", "requests",
                ))):
            raise SyncJournalCorruption("synchronization journal schema is invalid")
        # These additive collections were introduced after the original v1
        # journal. Keep old owner-only journals readable without advertising a
        # new schema before a migration is necessary.
        for key in ("participants", "divergences", "metrics"):
            collection = value.setdefault(key, {})
            if not isinstance(collection, dict):
                raise SyncJournalCorruption("synchronization journal schema is invalid")
        try:
            for key, item in value["relationships"].items():
                if SynchronizationRelationship.from_dict(item).relationship_id != key:
                    raise ValueError("relationship key mismatch")
            for key, item in value["generations"].items():
                if SourceGeneration.from_dict(item).generation_id != key:
                    raise ValueError("generation key mismatch")
            for key, item in value["requests"].items():
                if (not isinstance(key, str) or not isinstance(item, dict) or
                        set(item) != {"digest", "generation_id"} or
                        not isinstance(item["digest"], str) or
                        len(item["digest"]) != 64):
                    raise ValueError("request record invalid")
                validate_identifier(item["generation_id"], "generation id")
            for key, item in value["participants"].items():
                participant = Participant.from_dict(item)
                if key != f"{participant.relationship_id}:{participant.participant_id}":
                    raise ValueError("participant key mismatch")
            for key, item in value["divergences"].items():
                divergence = DivergenceRecord.from_dict(item)
                if key != divergence.relationship_id:
                    raise ValueError("divergence key mismatch")
            for key, item in value["metrics"].items():
                validate_identifier(key, "relationship id")
                if not isinstance(item, dict) or set(item) != {
                    "attempts", "accepted", "refused", "failed", "unknown",
                    "file_count", "byte_count", "observed_at",
                }:
                    raise ValueError("metrics record invalid")
                for field in ("attempts", "accepted", "refused", "failed", "unknown"):
                    validate_count(item[field], field, maximum=2**63 - 1)
                validate_count(item["file_count"], "file count", maximum=1_000_000)
                validate_count(item["byte_count"], "byte count", maximum=512 * 1024 * 1024)
                validate_timestamp(item["observed_at"], "observed at")
        except (TypeError, ValueError) as exc:
            raise SyncJournalCorruption("synchronization journal contains invalid records") from exc
        return value

    def _write_unlocked(self, value: dict[str, Any]) -> None:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent,
        )
        temporary_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(value, stream, sort_keys=True, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary_path, 0o600)
            self._replace(temporary_path, self.path)
            self.path.chmod(0o600)
            directory = os.open(str(self.path.parent), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary_path.unlink(missing_ok=True)

    def _locked(self, operation: Callable[[dict[str, Any]], Any], *, write: bool = False) -> Any:
        self._prepare()
        with self.lock_path.open("r+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                value = self._read_unlocked()
                result = operation(value)
                if write:
                    self._write_unlocked(value)
                return result
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def put_relationship(self, relationship: SynchronizationRelationship) -> SynchronizationRelationship:
        def operation(value: dict[str, Any]) -> SynchronizationRelationship:
            for item in value["relationships"].values():
                prior = SynchronizationRelationship.from_dict(item)
                if (prior.ownership_key == relationship.ownership_key and
                        prior.relationship_id != relationship.relationship_id):
                    raise RelationshipConflict("synchronization relationship already has an owner")
            existing = value["relationships"].get(relationship.relationship_id)
            if existing is not None:
                prior = SynchronizationRelationship.from_dict(existing)
                if prior.ownership_key != relationship.ownership_key:
                    raise RelationshipConflict("relationship identity cannot be reassigned")
                if relationship.owner_generation < prior.owner_generation:
                    raise RelationshipConflict("generation sequence cannot move backwards")
            value["relationships"][relationship.relationship_id] = relationship.as_dict()
            return relationship
        return self._locked(operation, write=True)

    def get_relationship(self, relationship_id: str) -> SynchronizationRelationship | None:
        validate_identifier(relationship_id, "relationship id")
        return self._locked(lambda value: (
            SynchronizationRelationship.from_dict(value["relationships"][relationship_id])
            if relationship_id in value["relationships"] else None
        ))

    def find_relationship(
        self, project_identity: str, remote_name: str, workspace_id: str,
    ) -> SynchronizationRelationship | None:
        key = (project_identity, remote_name, workspace_id)
        def operation(value: dict[str, Any]) -> SynchronizationRelationship | None:
            return next((
                relationship for relationship in (
                    SynchronizationRelationship.from_dict(item)
                    for item in value["relationships"].values()
                ) if relationship.ownership_key == key
            ), None)
        return self._locked(operation)

    def find_workspace_owner(
        self, remote_name: str, workspace_id: str,
    ) -> SynchronizationRelationship | None:
        """Return the sole recorded owner of a remote/workspace pair.

        This lookup intentionally ignores the requesting project identity so a
        fresh clone is refused before capture or transport.
        """
        def operation(value: dict[str, Any]) -> SynchronizationRelationship | None:
            matches = [
                SynchronizationRelationship.from_dict(item)
                for item in value["relationships"].values()
                if item.get("remote_name") == remote_name
                and item.get("workspace_id") == workspace_id
            ]
            if len(matches) > 1:
                raise SyncJournalCorruption("workspace has multiple synchronization owners")
            return matches[0] if matches else None
        return self._locked(operation)

    def set_mode(
        self, relationship_id: str, mode: str, *, lifecycle: str,
        updated_at: str | None = None,
    ) -> SynchronizationRelationship:
        validate_identifier(relationship_id, "relationship id")

        def operation(value: dict[str, Any]) -> SynchronizationRelationship:
            raw = value["relationships"].get(relationship_id)
            if raw is None:
                raise RelationshipNotFound("synchronization relationship was not found")
            relationship = SynchronizationRelationship.from_dict(raw)
            updated = replace(
                relationship, mode=mode, lifecycle=lifecycle,
                updated_at=updated_at or utc_now(),
            )
            value["relationships"][relationship_id] = updated.as_dict()
            return updated
        return self._locked(operation, write=True)

    def register_participant(
        self, participant: Participant, *, maximum: int = 64,
    ) -> Participant:
        if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 256:
            raise ValueError("participant bound is invalid")

        def operation(value: dict[str, Any]) -> Participant:
            if participant.relationship_id not in value["relationships"]:
                raise RelationshipNotFound("synchronization relationship was not found")
            prefix = f"{participant.relationship_id}:"
            peers = [key for key in value["participants"] if key.startswith(prefix)]
            key = prefix + participant.participant_id
            if key not in value["participants"] and len(peers) >= maximum:
                raise RelationshipConflict("synchronization participant limit was reached")
            value["participants"][key] = participant.as_dict()
            return participant
        return self._locked(operation, write=True)

    def list_participants(self, relationship_id: str) -> list[Participant]:
        validate_identifier(relationship_id, "relationship id")
        prefix = f"{relationship_id}:"
        return self._locked(lambda value: sorted(
            (
                Participant.from_dict(item)
                for key, item in value["participants"].items()
                if key.startswith(prefix)
            ),
            key=lambda item: item.participant_id,
        ))

    def list_relationships(self) -> list[SynchronizationRelationship]:
        return self._locked(lambda value: sorted(
            (SynchronizationRelationship.from_dict(item)
             for item in value["relationships"].values()),
            key=lambda item: item.relationship_id,
        ))

    def list_generations(self, relationship_id: str, *, limit: int = 256) -> list[SourceGeneration]:
        validate_identifier(relationship_id, "relationship id")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 256:
            raise ValueError("generation list limit is invalid")
        return self._locked(lambda value: sorted(
            (
                SourceGeneration.from_dict(item)
                for item in value["generations"].values()
                if item.get("relationship_id") == relationship_id
            ),
            key=lambda item: item.sequence,
        )[-limit:])

    def put_divergence(self, divergence: DivergenceRecord) -> DivergenceRecord:
        def operation(value: dict[str, Any]) -> DivergenceRecord:
            if divergence.relationship_id not in value["relationships"]:
                raise RelationshipNotFound("synchronization relationship was not found")
            value["divergences"][divergence.relationship_id] = divergence.as_dict()
            relationship = SynchronizationRelationship.from_dict(
                value["relationships"][divergence.relationship_id]
            )
            value["relationships"][divergence.relationship_id] = replace(
                relationship, lifecycle="diverged", updated_at=divergence.detected_at,
            ).as_dict()
            return divergence
        return self._locked(operation, write=True)

    def get_divergence(self, relationship_id: str) -> DivergenceRecord | None:
        validate_identifier(relationship_id, "relationship id")
        return self._locked(lambda value: (
            DivergenceRecord.from_dict(value["divergences"][relationship_id])
            if relationship_id in value["divergences"] else None
        ))

    def clear_divergence(self, relationship_id: str) -> bool:
        validate_identifier(relationship_id, "relationship id")
        def operation(value: dict[str, Any]) -> bool:
            return value["divergences"].pop(relationship_id, None) is not None
        return self._locked(operation, write=True)

    def record_metrics(
        self, relationship_id: str, *, outcome: str, file_count: int,
        byte_count: int, observed_at: str | None = None,
    ) -> dict[str, Any]:
        validate_identifier(relationship_id, "relationship id")
        if outcome not in {"accepted", "refused", "failed", "unknown"}:
            raise ValueError("metric outcome is invalid")
        validate_count(file_count, "file count", maximum=1_000_000)
        validate_count(byte_count, "byte count", maximum=512 * 1024 * 1024)
        when = validate_timestamp(observed_at or utc_now(), "observed at")

        def operation(value: dict[str, Any]) -> dict[str, Any]:
            if relationship_id not in value["relationships"]:
                raise RelationshipNotFound("synchronization relationship was not found")
            current = dict(value["metrics"].get(relationship_id) or {
                "attempts": 0, "accepted": 0, "refused": 0, "failed": 0,
                "unknown": 0, "file_count": 0, "byte_count": 0,
                "observed_at": when,
            })
            current["attempts"] += 1
            current[outcome] += 1
            current["file_count"] = file_count
            current["byte_count"] = byte_count
            current["observed_at"] = when
            value["metrics"][relationship_id] = current
            return dict(current)
        return self._locked(operation, write=True)

    def metrics(self, relationship_id: str) -> dict[str, Any] | None:
        validate_identifier(relationship_id, "relationship id")
        return self._locked(lambda value: (
            dict(value["metrics"][relationship_id])
            if relationship_id in value["metrics"] else None
        ))

    def delete_relationship(self, relationship_id: str) -> bool:
        """Delete an unused local relationship record, never generation history."""
        validate_identifier(relationship_id, "relationship id")
        def operation(value: dict[str, Any]) -> bool:
            if relationship_id not in value["relationships"]:
                return False
            if any(item.get("relationship_id") == relationship_id
                   for item in value["generations"].values()):
                raise RelationshipConflict(
                    "relationship with generation history cannot be deleted"
                )
            del value["relationships"][relationship_id]
            return True
        return self._locked(operation, write=True)

    @staticmethod
    def canonical_request_digest(payload: Any) -> str:
        try:
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("request payload must be canonical JSON") from exc
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def reserve_generation(
        self,
        *,
        relationship_id: str,
        request_id: str,
        request_digest: str,
        manifest_digest: str,
        file_count: int,
        byte_count: int,
        commit: str | None = None,
        dirty_digest: str | None = None,
        created_at: str | None = None,
    ) -> tuple[SourceGeneration, bool]:
        validate_identifier(relationship_id, "relationship id")
        validate_identifier(request_id, "request id")
        if not isinstance(request_digest, str) or len(request_digest) != 64 or any(
                char not in "0123456789abcdef" for char in request_digest):
            raise ValueError("request digest is invalid")
        request_key = f"{relationship_id}:{request_id}"

        def operation(value: dict[str, Any]) -> tuple[SourceGeneration, bool]:
            relationship_raw = value["relationships"].get(relationship_id)
            if relationship_raw is None:
                raise RelationshipNotFound("synchronization relationship was not found")
            prior_request = value["requests"].get(request_key)
            if prior_request is not None:
                if prior_request["digest"] != request_digest:
                    raise RequestDigestConflict("request identity was reused with different input")
                generation = SourceGeneration.from_dict(
                    value["generations"][prior_request["generation_id"]]
                )
                return generation, True

            relationship = SynchronizationRelationship.from_dict(relationship_raw)
            source_identity = hashlib.sha256(
                (relationship_id + "\0" + manifest_digest).encode()
            ).hexdigest()
            generation_id = f"gen_{source_identity}"
            existing = value["generations"].get(generation_id)
            if existing is not None:
                generation = SourceGeneration.from_dict(existing)
                value["requests"][request_key] = {
                    "digest": request_digest, "generation_id": generation_id,
                }
                return generation, True

            sequence = relationship.owner_generation + 1
            generation = SourceGeneration(
                generation_id=generation_id, relationship_id=relationship_id,
                sequence=sequence, manifest_digest=manifest_digest,
                file_count=file_count, byte_count=byte_count, lifecycle="pending",
                request_id=request_id, commit=commit, dirty_digest=dirty_digest,
                created_at=created_at or utc_now(),
            )
            updated_relationship = replace(
                relationship, owner_generation=sequence,
                pending_generation_id=generation_id, updated_at=created_at or utc_now(),
            )
            value["generations"][generation_id] = generation.as_dict()
            value["relationships"][relationship_id] = updated_relationship.as_dict()
            value["requests"][request_key] = {
                "digest": request_digest, "generation_id": generation_id,
            }
            return generation, False

        return self._locked(operation, write=True)

    def lookup_request(self, relationship_id: str, request_id: str) -> SourceGeneration | None:
        validate_identifier(relationship_id, "relationship id")
        validate_identifier(request_id, "request id")
        request_key = f"{relationship_id}:{request_id}"
        def operation(value: dict[str, Any]) -> SourceGeneration | None:
            record = value["requests"].get(request_key)
            return (SourceGeneration.from_dict(value["generations"][record["generation_id"]])
                    if record is not None else None)
        return self._locked(operation)

    def get_generation(self, generation_id: str) -> SourceGeneration | None:
        validate_identifier(generation_id, "generation id")
        return self._locked(lambda value: (
            SourceGeneration.from_dict(value["generations"][generation_id])
            if generation_id in value["generations"] else None
        ))

    def claim_generation_transfer(self, generation_id: str) -> tuple[SourceGeneration, bool]:
        """Atomically claim one pending generation for its only remote launch.

        A transferring generation may have been accepted remotely while its
        acknowledgment was lost. It is therefore returned unclaimed and must
        be reconciled with the original request identity, never launched again.
        """
        validate_identifier(generation_id, "generation id")

        def operation(value: dict[str, Any]) -> tuple[SourceGeneration, bool]:
            raw = value["generations"].get(generation_id)
            if raw is None:
                raise GenerationNotFound("source generation was not found")
            generation = SourceGeneration.from_dict(raw)
            if generation.lifecycle != "pending":
                return generation, False
            claimed = replace(generation, lifecycle="transferring")
            value["generations"][generation_id] = claimed.as_dict()
            return claimed, True

        return self._locked(operation, write=True)

    def transition_generation(
        self, generation_id: str, lifecycle: str, *, refusal_code: str | None = None,
        accepted_at: str | None = None,
    ) -> SourceGeneration:
        validate_identifier(generation_id, "generation id")
        allowed = {
            "pending": {"transferring", "refused", "failed"},
            "transferring": {"accepted", "refused", "failed", "diverged"},
        }
        def operation(value: dict[str, Any]) -> SourceGeneration:
            raw = value["generations"].get(generation_id)
            if raw is None:
                raise GenerationNotFound("source generation was not found")
            generation = SourceGeneration.from_dict(raw)
            if lifecycle == generation.lifecycle:
                return generation
            if lifecycle not in allowed.get(generation.lifecycle, set()):
                raise SyncRepositoryError("source generation transition is invalid")
            when = (accepted_at or utc_now()) if lifecycle == "accepted" else None
            updated = replace(
                generation, lifecycle=lifecycle, refusal_code=refusal_code,
                accepted_at=when,
            )
            relationship = SynchronizationRelationship.from_dict(
                value["relationships"][generation.relationship_id]
            )
            if lifecycle == "accepted":
                relationship = replace(
                    relationship, accepted_generation_id=generation_id,
                    pending_generation_id=(None if relationship.pending_generation_id == generation_id
                                           else relationship.pending_generation_id),
                    updated_at=when or utc_now(),
                )
            elif lifecycle in {"refused", "failed", "diverged"}:
                relationship = replace(
                    relationship,
                    pending_generation_id=(None if relationship.pending_generation_id == generation_id
                                           else relationship.pending_generation_id),
                    lifecycle=("diverged" if lifecycle == "diverged" else relationship.lifecycle),
                    updated_at=utc_now(),
                )
            value["generations"][generation_id] = updated.as_dict()
            value["relationships"][relationship.relationship_id] = relationship.as_dict()
            return updated
        return self._locked(operation, write=True)


__all__ = [
    "GenerationNotFound", "RelationshipConflict", "RelationshipNotFound",
    "RequestDigestConflict", "SyncJournalCorruption", "SyncRepository",
    "SyncRepositoryError", "default_journal_path",
]
