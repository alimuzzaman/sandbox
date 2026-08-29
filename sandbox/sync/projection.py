"""Generation-pinned managed-source projection policy."""

from __future__ import annotations

from dataclasses import dataclass

from .models import DivergenceRecord, validate_identifier


class SourceWriteRefused(RuntimeError):
    code = "managed_source_read_only"
    retryable = False


@dataclass(frozen=True)
class ManagedSourceProjection:
    """Describe source access without exposing a filesystem locator."""

    def prepare(
        self, *, relationship_id: str, generation_id: str,
        source_access: str, requests_source_write: bool = False,
    ) -> dict[str, object]:
        validate_identifier(relationship_id, "relationship id")
        validate_identifier(generation_id, "generation id")
        if source_access not in {"managed_read_only", "isolated_copy"}:
            raise ValueError("source access is invalid")
        if requests_source_write and source_access != "isolated_copy":
            raise SourceWriteRefused(
                "managed synchronized source is read-only; request an isolated copy"
            )
        return {
            "relationship_id": relationship_id,
            "generation_id": generation_id,
            "source_access": source_access,
            "managed_source_writable": False,
            "output_boundary": "artifacts" if source_access == "isolated_copy" else None,
        }

    def detect_divergence(
        self, *, relationship_id: str, generation_id: str,
        expected_digest: str, observed_digest: str, affected_count: int,
        detected_at: str,
    ) -> DivergenceRecord | None:
        """Return aggregate conflict evidence; never retain either digest."""
        validate_identifier(relationship_id, "relationship id")
        validate_identifier(generation_id, "generation id")
        for value in (expected_digest, observed_digest):
            if (not isinstance(value, str) or len(value) != 64 or
                    any(character not in "0123456789abcdef" for character in value)):
                raise ValueError("projection digest is invalid")
        if expected_digest == observed_digest:
            return None
        return DivergenceRecord(
            relationship_id=relationship_id,
            affected_count=affected_count,
            comparison_generation_id=generation_id,
            detected_at=detected_at,
            resolution_code="explicit_resolution_required",
        )


__all__ = ["ManagedSourceProjection", "SourceWriteRefused"]
